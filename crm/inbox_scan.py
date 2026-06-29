#!/usr/bin/env python3
"""
Inbox scan: finds emails from unknown senders and uses Claude to recommend
whether to add them as contacts or create follow-up tasks.

Cap: at most 20 Anthropic API calls per run (~$0.001 each with Haiku).
Results land in inbox_recommendations table with status='pending' for
review in the CRM Inbox tab.

Required env vars:
  SUPABASE_URL      - Supabase PostgreSQL connection string
  GMAIL_TOKEN_JSON  - base64-encoded token.json from Gmail OAuth
  ANTHROPIC_API_KEY - Anthropic API key

Run locally:
  SUPABASE_URL=... GMAIL_TOKEN_JSON=... ANTHROPIC_API_KEY=... python3 crm/inbox_scan.py
"""
import os
import sys
import base64
import json
import re
import time
import email.utils
from datetime import datetime, timedelta

# ── Validate env vars early ───────────────────────────────────────────────────

SUPABASE_URL      = os.environ.get('SUPABASE_URL', '').strip()
GMAIL_TOKEN_B64   = os.environ.get('GMAIL_TOKEN_JSON', '').strip()
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '').strip()

if not SUPABASE_URL:
    sys.exit("ERROR: SUPABASE_URL is not set.")
if not GMAIL_TOKEN_B64:
    sys.exit("ERROR: GMAIL_TOKEN_JSON is not set.")
if not ANTHROPIC_API_KEY:
    sys.exit("ERROR: ANTHROPIC_API_KEY is not set.")

if SUPABASE_URL.startswith('postgres://'):
    SUPABASE_URL = SUPABASE_URL.replace('postgres://', 'postgresql://', 1)

os.environ['DATABASE_URL'] = SUPABASE_URL

# ── Imports ───────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(__file__))

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import anthropic as anthropic_sdk
from sqlalchemy import func

from models import get_session, Contact, InboxRecommendation

# ── Config ────────────────────────────────────────────────────────────────────

SCOPES           = ['https://www.googleapis.com/auth/gmail.readonly']
MAX_AI_CALLS     = 20
AI_COST_PER_CALL = 0.001   # rough Haiku estimate

# Sender address patterns to skip — automated/noreply senders
SKIP_PATTERNS = [
    'noreply', 'no-reply', 'donotreply', 'do-not-reply',
    'notification', 'newsletter', 'mailer-daemon', 'mailerdaemon',
    'bounce@', 'bounces@', 'unsubscribe', 'automated',
    'postmaster@', 'alerts@', 'updates@',
]

SYSTEM_PROMPT = """\
You are helping Mitch Radakovich, board chair of All Aboard Ohio (a nonprofit \
passenger rail advocacy organization), manage his professional network during a \
three-month DC advocacy sabbatical focused on fundraising, policy, and building \
a rail advocacy coalition.

An email has arrived from someone not in Mitch's CRM. Evaluate whether this \
sender is worth tracking.

Recommend tracking only if the sender is plausibly relevant to: rail/transit \
advocacy, congressional or agency contacts, nonprofit fundraising, DC policy \
network, media, or peer organizations. Skip newsletters, automated \
notifications, commercial vendors, job recruiters, and irrelevant senders.

Return JSON only — no markdown, no text outside the JSON. Schema:
{
  "recommendation_type": "new_contact" | "new_task" | "skip",
  "summary": "<one sentence: why track or why skip>",
  "suggested_fields": {
    // new_contact: { "name": "", "organization": "", "title": "", "email": "",
    //   "category": "advocacy|funder|government|media|peer_org|dc_network|other",
    //   "warmth": "cold|warm|hot", "notes": "" }
    // new_task:    { "title": "", "description": "", "priority": "low|medium|high" }
    // skip:        {}
  }
}"""

# ── Gmail auth ────────────────────────────────────────────────────────────────

def build_gmail_service():
    try:
        token_data = json.loads(base64.b64decode(GMAIL_TOKEN_B64))
    except Exception as e:
        sys.exit(f"ERROR: Could not decode GMAIL_TOKEN_JSON: {e}")

    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    if creds.expired and creds.refresh_token:
        print("Refreshing Gmail OAuth token...")
        creds.refresh(Request())

    if not creds.valid:
        sys.exit(
            "ERROR: Gmail credentials are invalid. "
            "Re-run crm/auth_gmail.py and update GMAIL_TOKEN_JSON."
        )

    return build('gmail', 'v1', credentials=creds)


def get_my_email(service):
    return service.users().getProfile(userId='me').execute()['emailAddress'].lower()

# ── Sender filtering ──────────────────────────────────────────────────────────

def is_automated(addr):
    low = addr.lower()
    return any(p in low for p in SKIP_PATTERNS)


def parse_from(from_header):
    """Parse 'Name <addr>' → (name, addr_lower)."""
    name, addr = email.utils.parseaddr(from_header)
    addr = addr.lower().strip()
    name = name.strip() or addr.split('@')[0]
    return name, addr

# ── Gmail scan ────────────────────────────────────────────────────────────────

def scan_inbox(service, my_email, since_dt):
    """
    Returns dict: sender_email → {name, email, subject, snippet, date}
    keeping only the most recent message per sender (list is newest-first).
    """
    query = f'in:inbox after:{since_dt.strftime("%Y/%m/%d")}'

    result = service.users().messages().list(
        userId='me', q=query, maxResults=200
    ).execute()

    by_sender = {}

    for stub in result.get('messages', []):
        msg = service.users().messages().get(
            userId='me',
            id=stub['id'],
            format='metadata',
            metadataHeaders=['From', 'Subject'],
        ).execute()

        headers = {h['name'].lower(): h['value'] for h in msg['payload']['headers']}
        name, addr = parse_from(headers.get('from', ''))

        if not addr or addr == my_email:
            continue

        if addr not in by_sender:   # first occurrence = most recent
            by_sender[addr] = {
                'name':    name,
                'email':   addr,
                'subject': headers.get('subject', '(no subject)'),
                'snippet': msg.get('snippet', '')[:200],
                'date':    datetime.utcfromtimestamp(int(msg['internalDate']) / 1000),
            }

        time.sleep(0.1)

    return by_sender

# ── Anthropic evaluation ──────────────────────────────────────────────────────

def evaluate_sender(client, sender):
    """
    Calls Claude Haiku and returns the parsed JSON recommendation dict.
    Raises on API error or JSON parse failure.
    """
    user_msg = (
        f"From: {sender['name']} <{sender['email']}>\n"
        f"Subject: {sender['subject']}\n"
        f"Date: {sender['date'].strftime('%Y-%m-%d')}\n"
        f"Snippet: {sender['snippet']}"
    )

    resp = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': user_msg}],
    )

    raw = resp.content[0].text.strip()
    # Strip markdown code fences if the model added them
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Inbox scan started at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    service  = build_gmail_service()
    my_email = get_my_email(service)
    print(f"Authenticated as: {my_email}")

    session = get_session()
    try:
        known_emails = {
            row[0].lower()
            for row in session.query(Contact.email).filter(Contact.email.isnot(None)).all()
        }
        pending_emails = {
            row[0].lower()
            for row in session.query(InboxRecommendation.sender_email)
                               .filter_by(status='pending').all()
        }
        last_scan = session.query(func.max(InboxRecommendation.created_at)).scalar()
    except Exception as e:
        session.close()
        sys.exit(f"ERROR: DB query failed: {e}")

    since = last_scan or (datetime.utcnow() - timedelta(days=30))
    print(f"Scanning since:  {since.strftime('%Y-%m-%d')}\n")

    # ── Fetch from Gmail ──────────────────────────────────────────────────────
    print("Fetching inbox from Gmail...")
    try:
        by_sender = scan_inbox(service, my_email, since)
    except Exception as e:
        session.close()
        sys.exit(f"ERROR: Gmail fetch failed: {e}")

    print(f"Unique senders in window: {len(by_sender)}")

    # ── Filter ────────────────────────────────────────────────────────────────
    candidates = [
        info for addr, info in by_sender.items()
        if not is_automated(addr)
        and addr not in known_emails
        and addr not in pending_emails
    ]
    candidates.sort(key=lambda x: x['date'], reverse=True)   # most recent first

    cap_reached = len(candidates) > MAX_AI_CALLS
    candidates  = candidates[:MAX_AI_CALLS]

    print(f"Unmatched candidates:     {len(candidates)}"
          + (" (cap reached — older senders deferred)" if cap_reached else ""))
    print()

    # ── AI evaluation ─────────────────────────────────────────────────────────
    ai_client  = anthropic_sdk.Anthropic(api_key=ANTHROPIC_API_KEY)
    saved = skipped_by_ai = ai_errors = 0

    for i, sender in enumerate(candidates, 1):
        label = f"[{i}/{len(candidates)}] {sender['name']} <{sender['email']}>"
        print(f"  {label}")
        try:
            rec = evaluate_sender(ai_client, sender)
        except Exception as e:
            ai_errors += 1
            print(f"    ERROR: {e}")
            time.sleep(1)
            continue

        rec_type = rec.get('recommendation_type', 'skip')
        summary  = rec.get('summary', '')

        if rec_type == 'skip':
            skipped_by_ai += 1
            print(f"    skip — {summary}")
            time.sleep(0.3)
            continue

        row = InboxRecommendation(
            sender_name            = sender['name'],
            sender_email           = sender['email'],
            email_subject          = sender['subject'][:500],
            email_date             = sender['date'],
            email_snippet          = sender['snippet'],
            recommendation_type    = rec_type,
            recommendation_json    = json.dumps(rec.get('suggested_fields', {})),
            recommendation_summary = summary,
            status                 = 'pending',
        )
        session.add(row)
        saved += 1
        print(f"    {rec_type} — {summary[:80]}")
        time.sleep(0.3)

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        sys.exit(f"ERROR: Failed to save to Supabase: {e}")
    finally:
        session.close()

    est_cost = len(candidates) * AI_COST_PER_CALL
    print(f"\n=== Inbox scan complete ===")
    print(f"  Emails scanned     : {len(by_sender)}")
    print(f"  Candidates for AI  : {len(candidates)}")
    print(f"  Saved as pending   : {saved}")
    print(f"  Skipped by AI      : {skipped_by_ai}")
    print(f"  AI errors          : {ai_errors}")
    print(f"  Est. cost          : ${est_cost:.3f}")
    if cap_reached:
        print(f"  Cap reached        : yes (max {MAX_AI_CALLS}/run)")


if __name__ == '__main__':
    main()
