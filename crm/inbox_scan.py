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
from datetime import datetime, timedelta, date

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

from models import get_session, Contact, InboxRecommendation, TaskRecommendation, ProcessedGmailMessage

# ── Config ────────────────────────────────────────────────────────────────────

SCOPES             = ['https://www.googleapis.com/auth/gmail.readonly']
MAX_AI_CALLS       = 20    # cap for unmatched-sender evaluation
MAX_TOTAL_AI_CALLS = 30    # combined cap (unmatched senders + task triage)
AI_COST_PER_CALL   = 0.001 # rough Haiku estimate

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

TASK_TRIAGE_PROMPT = """\
You are helping Mitch Radakovich, board chair of All Aboard Ohio (a nonprofit \
passenger rail advocacy organization), manage his professional network during a \
three-month DC advocacy sabbatical.

An inbound email from a known contact is shown below. Evaluate whether it \
contains an action signal: a request, commitment, deadline, event, follow-up \
prompt, or introduction offer that warrants creating a task for Mitch.

Return JSON only — no markdown, no text outside the JSON. Schema:
{
  "has_task_signal": true | false,
  "summary": "<one sentence: what needs action, or why no action needed>",
  "suggested_task": {
    "title": "<short imperative phrase, e.g. \\"Follow up with Jane about proposal\\">",
    "description": "<one or two sentences of context>",
    "priority": "low | medium | high",
    "due_date": "<YYYY-MM-DD or null>"
  }
}
Only include "suggested_task" when has_task_signal is true."""


def scan_inbox_for_task_triage(service, my_email, since_dt, known_emails, processed_ids):
    """
    Returns list of {message_id, from_email, from_name, subject, snippet, date}
    for the most recent unprocessed inbound message per known contact in the window.
    List is in newest-first order (Gmail API returns messages newest-first).
    """
    query = f'in:inbox after:{since_dt.strftime("%Y/%m/%d")}'
    result = service.users().messages().list(
        userId='me', q=query, maxResults=100
    ).execute()

    candidates = []
    seen_senders = set()

    for stub in result.get('messages', []):
        msg_id = stub['id']
        if msg_id in processed_ids:
            continue

        msg = service.users().messages().get(
            userId='me',
            id=msg_id,
            format='metadata',
            metadataHeaders=['From', 'Subject'],
        ).execute()

        headers = {h['name'].lower(): h['value'] for h in msg['payload']['headers']}
        name, addr = parse_from(headers.get('from', ''))

        if not addr or addr == my_email or is_automated(addr):
            continue
        if addr not in known_emails:
            continue
        if addr in seen_senders:
            continue

        seen_senders.add(addr)
        candidates.append({
            'message_id': msg_id,
            'from_email':  addr,
            'from_name':   name,
            'subject':     headers.get('subject', '(no subject)'),
            'snippet':     msg.get('snippet', '')[:200],
            'date':        datetime.utcfromtimestamp(int(msg['internalDate']) / 1000),
        })
        time.sleep(0.1)

    return candidates


def evaluate_task_signal(client, msg, contact_name):
    """
    Calls Claude Haiku and returns the parsed JSON task-triage dict.
    Raises on API error or JSON parse failure.
    """
    user_msg = (
        f"From: {contact_name} <{msg['from_email']}>\n"
        f"Subject: {msg['subject']}\n"
        f"Date: {msg['date'].strftime('%Y-%m-%d')}\n"
        f"Snippet: {msg['snippet']}"
    )
    resp = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        system=TASK_TRIAGE_PROMPT,
        messages=[{'role': 'user', 'content': user_msg}],
    )
    raw = resp.content[0].text.strip()
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
        email_to_contact = {
            row[0].lower(): {'id': row[1], 'name': row[2]}
            for row in session.query(Contact.email, Contact.id, Contact.name)
                              .filter(Contact.email.isnot(None)).all()
        }
        known_emails = set(email_to_contact.keys())
        pending_emails = {
            row[0].lower()
            for row in session.query(InboxRecommendation.sender_email)
                               .filter_by(status='pending').all()
        }
        processed_ids = {
            row[0]
            for row in session.query(ProcessedGmailMessage.message_id).all()
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

    # ── Task triage for known contacts ────────────────────────────────────────
    task_budget = max(0, MAX_TOTAL_AI_CALLS - len(candidates))
    print(f"\nTask triage budget: {task_budget} call(s)")

    task_candidates = []
    if task_budget > 0:
        print("Scanning for task signals in emails from known contacts...")
        try:
            task_candidates = scan_inbox_for_task_triage(
                service, my_email, since, known_emails, processed_ids
            )
        except Exception as e:
            print(f"  WARNING: Gmail scan for task triage failed: {e}")

        if len(task_candidates) > task_budget:
            task_candidates = task_candidates[:task_budget]
            print(f"  (capped at {task_budget})")

    print(f"Task triage candidates: {len(task_candidates)}\n")

    tasks_created = task_triage_skipped = task_triage_errors = 0
    new_processed_ids = []

    for i, msg in enumerate(task_candidates, 1):
        contact_info = email_to_contact.get(msg['from_email'], {})
        contact_id   = contact_info.get('id')
        contact_name = contact_info.get('name', msg['from_email'])
        print(f"  [{i}/{len(task_candidates)}] {contact_name} — {msg['subject'][:50]}")

        try:
            rec = evaluate_task_signal(ai_client, msg, contact_name)
        except Exception as e:
            task_triage_errors += 1
            print(f"    ERROR: {e}")
            new_processed_ids.append(msg['message_id'])
            time.sleep(1)
            continue

        new_processed_ids.append(msg['message_id'])

        if not rec.get('has_task_signal'):
            task_triage_skipped += 1
            print(f"    no signal — {rec.get('summary', '')[:80]}")
            time.sleep(0.3)
            continue

        suggested = rec.get('suggested_task', {})
        due_date  = None
        due_str   = suggested.get('due_date')
        if due_str:
            try:
                due_date = date.fromisoformat(due_str)
            except (ValueError, AttributeError):
                pass

        task_rec = TaskRecommendation(
            title             = suggested.get('title', 'Follow up (from email)')[:255],
            description       = suggested.get('description', ''),
            due_date          = due_date,
            priority          = suggested.get('priority', 'medium'),
            linked_contact_id = contact_id,
            category          = 'outreach',
            source            = 'gmail',
            source_context    = msg['subject'][:500],
            ai_summary        = rec.get('summary', ''),
            status            = 'pending',
        )
        session.add(task_rec)
        tasks_created += 1
        print(f"    task — {rec.get('summary', '')[:80]}")
        time.sleep(0.3)

    for mid in new_processed_ids:
        session.add(ProcessedGmailMessage(message_id=mid))

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        sys.exit(f"ERROR: Failed to save to Supabase: {e}")
    finally:
        session.close()

    total_ai_calls = len(candidates) + len(task_candidates)
    est_cost = total_ai_calls * AI_COST_PER_CALL
    print(f"\n=== Inbox scan complete ===")
    print(f"  Emails scanned     : {len(by_sender)}")
    print(f"  Candidates for AI  : {len(candidates)}")
    print(f"  Saved as pending   : {saved}")
    print(f"  Skipped by AI      : {skipped_by_ai}")
    print(f"  AI errors          : {ai_errors}")
    print(f"  Task triage        : {len(task_candidates)} evaluated, "
          f"{tasks_created} created, {task_triage_skipped} no signal, "
          f"{task_triage_errors} error(s)")
    print(f"  Total AI calls     : {total_ai_calls}")
    print(f"  Est. cost          : ${est_cost:.3f}")
    if cap_reached:
        print(f"  Cap reached        : yes (max {MAX_AI_CALLS} unmatched / {MAX_TOTAL_AI_CALLS} total)")


if __name__ == '__main__':
    main()
