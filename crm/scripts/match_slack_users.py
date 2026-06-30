#!/usr/bin/env python3
"""
One-time script: match Slack users to CRM contacts by email and populate slack_user_id.

Calls users.list, normalises emails to lowercase, and updates any contact
whose email matches a Slack user. Skips bots, deleted/deactivated accounts,
contacts that already have the correct slack_user_id, and Slack accounts with
no email on the profile.

Required env vars:
  SUPABASE_URL     - Supabase PostgreSQL connection string
  SLACK_BOT_TOKEN  - Slack Bot token (xoxb-...)
                     Requires the users:read and users:read.email scopes.

Run:
  SUPABASE_URL=postgresql://... SLACK_BOT_TOKEN=xoxb-... python3 crm/scripts/match_slack_users.py
"""
import os
import sys
import time

SUPABASE_URL    = os.environ.get('SUPABASE_URL', '').strip()
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '').strip()

if not SUPABASE_URL:
    sys.exit("ERROR: SUPABASE_URL is not set.")
if not SLACK_BOT_TOKEN:
    sys.exit("ERROR: SLACK_BOT_TOKEN is not set.")

if SUPABASE_URL.startswith('postgres://'):
    SUPABASE_URL = SUPABASE_URL.replace('postgres://', 'postgresql://', 1)

os.environ['DATABASE_URL'] = SUPABASE_URL

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from models import get_session, Contact


def fetch_all_slack_users(client):
    """Return list of active, non-bot Slack user dicts that have a profile email."""
    users = []
    cursor = None
    while True:
        params = {'limit': 200}
        if cursor:
            params['cursor'] = cursor
        try:
            resp = client.users_list(**params)
        except SlackApiError as e:
            sys.exit(f"ERROR: users.list failed: {e.response['error']}")

        for member in resp.get('members', []):
            if member.get('deleted') or member.get('is_bot') or member.get('id') == 'USLACKBOT':
                continue
            email = (member.get('profile') or {}).get('email', '').strip().lower()
            if not email:
                continue
            users.append({
                'id':    member['id'],
                'name':  (member.get('profile') or {}).get('real_name') or member.get('name', member['id']),
                'email': email,
            })

        cursor = resp.get('response_metadata', {}).get('next_cursor')
        if not cursor:
            break
        time.sleep(0.5)

    return users


def main():
    client = WebClient(token=SLACK_BOT_TOKEN)
    try:
        resp = client.auth_test()
        print(f"Slack authenticated as: {resp['user']} (workspace: {resp['team']})\n")
    except SlackApiError as e:
        sys.exit(f"ERROR: Slack auth failed: {e.response['error']}")

    print("Fetching Slack users...")
    slack_users = fetch_all_slack_users(client)
    print(f"  {len(slack_users)} active Slack user(s) with email addresses\n")

    # Build lookup: lowercase email → slack user dict
    slack_by_email = {u['email']: u for u in slack_users}

    session = get_session()
    try:
        contacts = session.query(Contact).filter(Contact.email.isnot(None)).all()
        print(f"Checking {len(contacts)} contact(s) with email addresses...\n")

        matched = []        # list of (name, email, slack_id, slack_name)
        already_correct = []
        no_match = []       # list of (name, email)

        for contact in contacts:
            contact_email = contact.email.strip().lower()
            slack_user = slack_by_email.get(contact_email)

            if slack_user is None:
                no_match.append((contact.name, contact.email))
                continue

            if contact.slack_user_id == slack_user['id']:
                already_correct.append((contact.name, contact.email, slack_user['id']))
                continue

            contact.slack_user_id = slack_user['id']
            matched.append((contact.name, contact.email, slack_user['id'], slack_user['name']))

        if matched:
            session.commit()

    except Exception as e:
        session.rollback()
        sys.exit(f"ERROR: Database operation failed: {e}")
    finally:
        session.close()

    print("=== Results ===")
    if matched:
        print(f"\nUpdated ({len(matched)}):")
        for name, email, slack_id, slack_name in matched:
            print(f"  {name} <{email}> → {slack_id} ({slack_name})")
    if already_correct:
        print(f"\nAlready set correctly ({len(already_correct)}):")
        for name, email, slack_id in already_correct:
            print(f"  {name} <{email}> = {slack_id}")
    if no_match:
        print(f"\nNo Slack match ({len(no_match)}):")
        for name, email in no_match:
            print(f"  {name} <{email}>")

    print(f"\nSummary: {len(matched)} updated, {len(already_correct)} already correct, {len(no_match)} no match")


if __name__ == '__main__':
    main()
