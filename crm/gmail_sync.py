#!/usr/bin/env python3
"""
Gmail sync: updates CRM contacts with their most recent email activity.

Checks every contact that has an email address. For each one, searches Gmail
for threads since last_synced_at (or 30 days ago on first run). Records the
most recent message date, subject, and direction (inbound/outbound).

Required env vars:
  SUPABASE_URL     - Supabase PostgreSQL connection string
  GMAIL_TOKEN_JSON - base64-encoded token.json from the OAuth flow
                     (run crm/auth_gmail.py once locally to generate it)

Run locally:
  SUPABASE_URL=... GMAIL_TOKEN_JSON=... python3 crm/gmail_sync.py

GitHub Actions runs this every 6 hours via .github/workflows/gmail_sync.yml
"""
import os
import sys
import base64
import json
import time
from datetime import datetime, timedelta

# ── Validate env vars before any heavy imports ────────────────────────────────

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
GMAIL_TOKEN_B64 = os.environ.get('GMAIL_TOKEN_JSON', '').strip()

if not SUPABASE_URL:
    sys.exit("ERROR: SUPABASE_URL is not set.")
if not GMAIL_TOKEN_B64:
    sys.exit(
        "ERROR: GMAIL_TOKEN_JSON is not set.\n"
        "Run crm/auth_gmail.py locally to generate the token, then base64-encode it:\n"
        "  base64 -i token.json | tr -d '\\n'"
    )

if SUPABASE_URL.startswith('postgres://'):
    SUPABASE_URL = SUPABASE_URL.replace('postgres://', 'postgresql://', 1)

os.environ['DATABASE_URL'] = SUPABASE_URL

# ── Imports ───────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(__file__))

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from models import get_session, Contact

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

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
            "ERROR: Gmail credentials are invalid or expired with no refresh token.\n"
            "Re-run crm/auth_gmail.py locally and update the GMAIL_TOKEN_JSON secret."
        )

    return build('gmail', 'v1', credentials=creds)


def get_my_email(service):
    profile = service.users().getProfile(userId='me').execute()
    return profile['emailAddress'].lower()

# ── Gmail search ──────────────────────────────────────────────────────────────

def find_most_recent_email(service, contact_email, since_dt):
    """
    Returns (email_date: datetime, subject: str, from_addr: str) for the most
    recent message to/from contact_email since since_dt, or None if none found.
    """
    date_str = since_dt.strftime('%Y/%m/%d')
    query = f'(to:{contact_email} OR from:{contact_email}) after:{date_str}'

    result = service.users().messages().list(
        userId='me',
        q=query,
        maxResults=5,   # newest-first; we only need [0] but buffer for safety
    ).execute()

    messages = result.get('messages', [])
    if not messages:
        return None

    msg = service.users().messages().get(
        userId='me',
        id=messages[0]['id'],
        format='metadata',
        metadataHeaders=['From', 'Subject'],
    ).execute()

    headers = {h['name'].lower(): h['value'] for h in msg['payload']['headers']}
    subject = headers.get('subject', '(no subject)')
    from_addr = headers.get('from', '').lower()

    # internalDate is milliseconds since epoch UTC
    email_date = datetime.utcfromtimestamp(int(msg['internalDate']) / 1000)

    return email_date, subject, from_addr

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Gmail sync started at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    service = build_gmail_service()
    my_email = get_my_email(service)
    print(f"Authenticated as: {my_email}\n")

    session = get_session()
    try:
        contacts = (
            session.query(Contact)
            .filter(Contact.email.isnot(None), Contact.email != '')
            .order_by(Contact.name)
            .all()
        )
    except Exception as e:
        session.close()
        sys.exit(f"ERROR: Could not fetch contacts from Supabase: {e}")

    total = len(contacts)
    now = datetime.utcnow()
    updated = no_activity = errors = 0

    print(f"Checking {total} contacts with email addresses...\n")

    for i, contact in enumerate(contacts, 1):
        since = contact.last_synced_at or (now - timedelta(days=30))

        try:
            result = find_most_recent_email(service, contact.email, since)
        except Exception as e:
            errors += 1
            print(f"  [{i}/{total}] {contact.name} <{contact.email}> — ERROR: {e}")
            time.sleep(1)
            continue

        contact.last_synced_at = now

        if result:
            email_date, subject, from_addr = result
            direction = 'outbound' if my_email in from_addr else 'inbound'
            contact.last_email_date = email_date
            contact.last_email_subject = subject[:500]
            contact.last_email_direction = direction
            contact.updated_at = now
            updated += 1

            arrow = '→' if direction == 'outbound' else '←'
            print(f"  [{i}/{total}] {contact.name:30s} {arrow} {email_date.strftime('%Y-%m-%d')}  \"{subject[:55]}\"")
        else:
            no_activity += 1
            print(f"  [{i}/{total}] {contact.name:30s} — no activity since {since.strftime('%Y-%m-%d')}")

        # Stay well under Gmail API quota (250 units/sec per user)
        time.sleep(0.25)

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        sys.exit(f"ERROR: Failed to write updates to Supabase: {e}")
    finally:
        session.close()

    print(f"\n=== Sync complete ===")
    print(f"  Contacts checked : {total}")
    print(f"  Updated          : {updated}")
    print(f"  No activity      : {no_activity}")
    print(f"  Errors           : {errors}")


if __name__ == '__main__':
    main()
