#!/usr/bin/env python3
"""
Slack sync: scans DMs and channels for contact activity and AI-surfaced task recommendations.

DMs:      Matches messages to contacts by slack_user_id. Updates last_contact_date and
          logs Interaction records (type='text'). Unmatched DM senders → inbox_recommendation.
Channels: Scans for @mentions of Mitch in any member channel, plus any channels in
          SLACK_CHANNEL_IDS. Logs Interaction records for matched contacts.
AI triage: Evaluates messages (newest-first, cap 20) for task-worthy signals — requests,
          commitments, deadlines, follow-up cues. Creates task_recommendation rows with
          source='slack'.

Required bot token scopes:
  channels:history, channels:read, groups:history, groups:read,
  im:history, im:read, users:read

Required env vars:
  SUPABASE_URL       - Supabase PostgreSQL connection string
  SLACK_BOT_TOKEN    - Slack Bot token (xoxb-...)
  SLACK_USER_ID      - Mitch's own Slack user ID (e.g., U01ABC123)
  ANTHROPIC_API_KEY  - Anthropic API key

Optional env vars:
  SLACK_CHANNEL_IDS  - Comma-separated channel IDs to always scan (e.g., C01ABC,C02DEF)

Run locally:
  SUPABASE_URL=... SLACK_BOT_TOKEN=... SLACK_USER_ID=... ANTHROPIC_API_KEY=... python3 crm/slack_sync.py

GitHub Actions runs this every 6 hours after gmail_sync.py and inbox_scan.py.
"""
import os
import sys
import json
import re
import time
from datetime import datetime, timedelta, timezone

# ── Validate env vars early ───────────────────────────────────────────────────

SUPABASE_URL      = os.environ.get('SUPABASE_URL', '').strip()
SLACK_BOT_TOKEN   = os.environ.get('SLACK_BOT_TOKEN', '').strip()
SLACK_USER_ID     = os.environ.get('SLACK_USER_ID', '').strip()
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '').strip()
SLACK_CHANNEL_IDS = [c.strip() for c in os.environ.get('SLACK_CHANNEL_IDS', '').split(',') if c.strip()]

if not SUPABASE_URL:
    sys.exit("ERROR: SUPABASE_URL is not set.")
if not SLACK_BOT_TOKEN:
    sys.exit(
        "ERROR: SLACK_BOT_TOKEN is not set.\n"
        "Create a Slack app, add a Bot token with scopes: channels:history, channels:read,\n"
        "groups:history, groups:read, im:history, im:read, users:read."
    )
if not SLACK_USER_ID:
    sys.exit(
        "ERROR: SLACK_USER_ID is not set.\n"
        "Set this to Mitch's Slack user ID (e.g., U01ABC123). Find it in Slack profile → More."
    )
if not ANTHROPIC_API_KEY:
    sys.exit("ERROR: ANTHROPIC_API_KEY is not set.")

if SUPABASE_URL.startswith('postgres://'):
    SUPABASE_URL = SUPABASE_URL.replace('postgres://', 'postgresql://', 1)

os.environ['DATABASE_URL'] = SUPABASE_URL

# ── Imports ───────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(__file__))

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import anthropic as anthropic_sdk
from sqlalchemy import func

from models import get_session, Contact, Interaction, InboxRecommendation, TaskRecommendation

# ── Config ────────────────────────────────────────────────────────────────────

MAX_AI_CALLS     = 20
AI_COST_PER_CALL = 0.001

MENTION_RE = re.compile(rf'<@{re.escape(SLACK_USER_ID)}>', re.IGNORECASE)

TASK_SYSTEM_PROMPT = """\
You are helping Mitch Radakovich, board chair of All Aboard Ohio (a nonprofit \
passenger rail advocacy organization), manage his professional network during a \
three-month DC advocacy sabbatical focused on fundraising, policy, and building \
a rail advocacy coalition.

A Slack message has been received. Evaluate whether it contains a signal that warrants \
creating a follow-up task: a request directed at Mitch, a commitment Mitch made, a \
deadline, a question requiring a response, or a clear follow-up cue.

Return JSON only — no markdown, no text outside the JSON. Schema:
{
  "is_task": true | false,
  "title": "<short action-oriented task title, or null if not a task>",
  "description": "<one sentence describing what the task is, or null>",
  "priority": "low" | "medium" | "high",
  "category": "outreach" | "intro_followup" | "fundraising" | "policy" | "admin" | "career" | "sabbatical_prep" | null,
  "ai_summary": "<one sentence on why this matters to Mitch's DC advocacy work>"
}"""

# ── Slack helpers ─────────────────────────────────────────────────────────────

def build_slack_client():
    client = WebClient(token=SLACK_BOT_TOKEN)
    try:
        resp = client.auth_test()
        print(f"Slack authenticated as: {resp['user']} (workspace: {resp['team']})")
    except SlackApiError as e:
        sys.exit(f"ERROR: Slack auth failed: {e.response['error']}")
    return client


def _dt_to_slack_ts(dt):
    """Convert naive UTC datetime to Slack Unix timestamp string."""
    return str(dt.replace(tzinfo=timezone.utc).timestamp())


def _slack_ts_to_dt(ts_str):
    """Convert Slack Unix timestamp string to naive UTC datetime."""
    return datetime.utcfromtimestamp(float(ts_str))


def get_user_display_name(client, user_id):
    """Fetch Slack display name for a user ID. Returns user_id on failure."""
    try:
        resp = client.users_info(user=user_id)
        profile = resp['user'].get('profile', {})
        return (profile.get('display_name') or profile.get('real_name') or user_id)
    except SlackApiError:
        return user_id


def paginate_conversations(client, **kwargs):
    """Yields all conversations across pagination cursors."""
    cursor = None
    while True:
        params = {**kwargs, 'limit': 200}
        if cursor:
            params['cursor'] = cursor
        try:
            resp = client.conversations_list(**params)
        except SlackApiError as e:
            print(f"  WARNING: conversations.list error: {e.response['error']}")
            return
        yield from resp.get('channels', [])
        cursor = resp.get('response_metadata', {}).get('next_cursor')
        if not cursor:
            break
        time.sleep(0.5)


def get_channel_history(client, channel_id, oldest_ts):
    """Returns list of messages since oldest_ts. Returns [] and logs on error."""
    messages = []
    cursor = None
    while True:
        params = {'channel': channel_id, 'oldest': oldest_ts, 'limit': 200}
        if cursor:
            params['cursor'] = cursor
        try:
            resp = client.conversations_history(**params)
        except SlackApiError as e:
            print(f"  WARNING: conversations.history({channel_id}): {e.response['error']}")
            return messages
        messages.extend(resp.get('messages', []))
        if not resp.get('has_more'):
            break
        cursor = resp.get('response_metadata', {}).get('next_cursor')
        if not cursor:
            break
        time.sleep(0.3)
    return messages

# ── AI evaluation ─────────────────────────────────────────────────────────────

def evaluate_message_for_task(ai_client, sender_name, channel_label, text, msg_date):
    """
    Calls Claude Haiku and returns parsed JSON dict.
    Raises on API error or JSON parse failure.
    """
    user_msg = (
        f"From: {sender_name}\n"
        f"Channel/Context: {channel_label}\n"
        f"Date: {msg_date.strftime('%Y-%m-%d')}\n"
        f"Message: {text[:500]}"
    )
    resp = ai_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        system=TASK_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': user_msg}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.utcnow()
    print(f"Slack sync started at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    client = build_slack_client()
    print()

    session = get_session()
    try:
        contacts_by_slack_id = {
            c.slack_user_id: c
            for c in session.query(Contact).filter(Contact.slack_user_id.isnot(None)).all()
        }
        pending_slack_ids = {
            row[0]
            for row in session.query(InboxRecommendation.sender_email)
                               .filter_by(status='pending').all()
            if row[0]
        }
        last_slack_ts = (
            session.query(func.max(TaskRecommendation.created_at))
            .filter(TaskRecommendation.source == 'slack')
            .scalar()
        )
    except Exception as e:
        session.close()
        sys.exit(f"ERROR: DB query failed: {e}")

    since = last_slack_ts if last_slack_ts else (now - timedelta(hours=24))
    since_ts = _dt_to_slack_ts(since)

    print(f"Scanning since: {since.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Known contacts with Slack IDs: {len(contacts_by_slack_id)}")
    if SLACK_CHANNEL_IDS:
        print(f"Extra channels to scan: {', '.join(SLACK_CHANNEL_IDS)}")
    print()

    # Collections built during scan phase
    ai_candidates    = []   # messages to AI-evaluate for task signals
    interactions     = []   # Interaction rows to insert
    unmatched_dms    = {}   # slack_user_id → {text, date} for unmatched DM senders

    # ── Scan DMs ──────────────────────────────────────────────────────────────
    print("Scanning DMs...")
    dm_channels = list(paginate_conversations(client, types='im'))
    print(f"  Found {len(dm_channels)} DM conversation(s)")

    for dm in dm_channels:
        other_uid = dm.get('user')
        if not other_uid or other_uid == SLACK_USER_ID:
            continue

        messages = get_channel_history(client, dm['id'], since_ts)
        if not messages:
            continue

        contact = contacts_by_slack_id.get(other_uid)

        for msg in messages:
            if msg.get('type') != 'message' or msg.get('subtype') or msg.get('bot_id'):
                continue
            sender_id = msg.get('user', '')
            text      = msg.get('text', '').strip()
            if not text:
                continue
            msg_date  = _slack_ts_to_dt(msg['ts'])
            outbound  = sender_id == SLACK_USER_ID

            if contact:
                if not outbound:
                    if not contact.last_contact_date or msg_date.date() > contact.last_contact_date:
                        contact.last_contact_date = msg_date.date()
                    interactions.append(Interaction(
                        contact_id=contact.id,
                        date=msg_date.date(),
                        type='text',
                        notes=f"Slack DM: {text[:500]}",
                    ))
                ai_candidates.append({
                    'sender_name':   'Mitch' if outbound else contact.name,
                    'sender_id':     sender_id,
                    'channel_label': 'DM (outbound)' if outbound else 'DM (inbound)',
                    'text':          text,
                    'date':          msg_date,
                    'contact':       contact,
                })
            else:
                # Unmatched sender — only track inbound DMs, keep newest message per user
                if not outbound and other_uid not in unmatched_dms:
                    unmatched_dms[other_uid] = {'text': text, 'date': msg_date}
                ai_candidates.append({
                    'sender_name':   other_uid,   # resolved below
                    'sender_id':     sender_id,
                    'channel_label': 'DM (inbound)' if not outbound else 'DM (outbound)',
                    'text':          text,
                    'date':          msg_date,
                    'contact':       None,
                })

        time.sleep(0.2)

    print(f"  Unmatched DM senders: {len(unmatched_dms)}")

    # ── Resolve display names for unmatched senders ───────────────────────────
    resolved_names = {}
    for uid in unmatched_dms:
        name = get_user_display_name(client, uid)
        resolved_names[uid] = name
        unmatched_dms[uid]['name'] = name
        time.sleep(0.2)

    for cand in ai_candidates:
        if cand['contact'] is None and cand['sender_id'] in resolved_names:
            cand['sender_name'] = resolved_names[cand['sender_id']]

    # ── Scan channels for @mentions ───────────────────────────────────────────
    print("\nScanning member channels for @mentions...")
    member_channels = [
        ch for ch in paginate_conversations(client, types='public_channel,private_channel')
        if ch.get('is_member')
    ]
    print(f"  Member of {len(member_channels)} channel(s)")

    mention_count = 0
    for ch in member_channels:
        messages = get_channel_history(client, ch['id'], since_ts)
        ch_label = f"#{ch.get('name', ch['id'])}"
        for msg in messages:
            if msg.get('type') != 'message' or msg.get('subtype') or msg.get('bot_id'):
                continue
            text = msg.get('text', '').strip()
            if not text or not MENTION_RE.search(text):
                continue
            sender_id = msg.get('user', '')
            if sender_id == SLACK_USER_ID:
                continue
            msg_date = _slack_ts_to_dt(msg['ts'])
            contact  = contacts_by_slack_id.get(sender_id)

            if contact:
                if not contact.last_contact_date or msg_date.date() > contact.last_contact_date:
                    contact.last_contact_date = msg_date.date()
                interactions.append(Interaction(
                    contact_id=contact.id,
                    date=msg_date.date(),
                    type='text',
                    notes=f"Slack @mention in {ch_label}: {text[:400]}",
                ))
            ai_candidates.append({
                'sender_name':   contact.name if contact else sender_id,
                'sender_id':     sender_id,
                'channel_label': f"@mention in {ch_label}",
                'text':          text,
                'date':          msg_date,
                'contact':       contact,
            })
            mention_count += 1
        time.sleep(0.2)

    print(f"  @mentions found: {mention_count}")

    # ── Scan SLACK_CHANNEL_IDS ────────────────────────────────────────────────
    if SLACK_CHANNEL_IDS:
        print(f"\nScanning {len(SLACK_CHANNEL_IDS)} configured channel(s)...")
        for ch_id in SLACK_CHANNEL_IDS:
            messages = get_channel_history(client, ch_id, since_ts)
            ch_label = f"#{ch_id}"
            for msg in messages:
                if msg.get('type') != 'message' or msg.get('subtype') or msg.get('bot_id'):
                    continue
                sender_id = msg.get('user', '')
                if sender_id == SLACK_USER_ID:
                    continue
                text = msg.get('text', '').strip()
                if not text:
                    continue
                msg_date = _slack_ts_to_dt(msg['ts'])
                contact  = contacts_by_slack_id.get(sender_id)

                if contact:
                    if not contact.last_contact_date or msg_date.date() > contact.last_contact_date:
                        contact.last_contact_date = msg_date.date()
                    interactions.append(Interaction(
                        contact_id=contact.id,
                        date=msg_date.date(),
                        type='text',
                        notes=f"Slack {ch_label}: {text[:400]}",
                    ))
                ai_candidates.append({
                    'sender_name':   contact.name if contact else sender_id,
                    'sender_id':     sender_id,
                    'channel_label': ch_label,
                    'text':          text,
                    'date':          msg_date,
                    'contact':       contact,
                })
            time.sleep(0.2)

    # ── Inbox recommendations for unmatched DM senders ────────────────────────
    print(f"\nCreating inbox recommendations for {len(unmatched_dms)} unmatched DM sender(s)...")
    inbox_saved = 0
    for uid, info in unmatched_dms.items():
        if uid in pending_slack_ids:
            print(f"  {info.get('name', uid)} ({uid}) — already pending, skipping")
            continue
        row = InboxRecommendation(
            sender_name            = info.get('name', uid),
            sender_email           = uid,
            email_subject          = 'Slack DM',
            email_date             = info['date'],
            email_snippet          = info['text'][:200],
            recommendation_type    = 'new_contact',
            recommendation_json    = json.dumps({
                'name':          info.get('name', uid),
                'slack_user_id': uid,
                'category':      'other',
                'warmth':        'cold',
            }),
            recommendation_summary = (
                f"Received a Slack DM from {info.get('name', uid)}, who is not in the CRM."
            ),
            status = 'pending',
        )
        session.add(row)
        inbox_saved += 1
        print(f"  + inbox rec: {info.get('name', uid)} ({uid})")

    # ── Log interactions ──────────────────────────────────────────────────────
    print(f"\nLogging {len(interactions)} interaction(s)...")
    for interaction in interactions:
        session.add(interaction)

    # ── AI triage ─────────────────────────────────────────────────────────────
    # Deduplicate (same sender + same text prefix) and cap at MAX_AI_CALLS newest-first
    ai_candidates.sort(key=lambda x: x['date'], reverse=True)
    seen = set()
    deduped = []
    for c in ai_candidates:
        key = (c['sender_id'], c['text'][:100])
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    cap_reached = len(deduped) > MAX_AI_CALLS
    deduped     = deduped[:MAX_AI_CALLS]

    print(f"\nAI triage: {len(deduped)} message(s)"
          + (" (cap reached — older messages deferred)" if cap_reached else ""))

    ai_client  = anthropic_sdk.Anthropic(api_key=ANTHROPIC_API_KEY)
    task_saved = skipped_by_ai = ai_errors = 0

    for i, cand in enumerate(deduped, 1):
        label = f"[{i}/{len(deduped)}] {cand['sender_name']} in {cand['channel_label']}"
        print(f"  {label}")
        try:
            result = evaluate_message_for_task(
                ai_client,
                cand['sender_name'],
                cand['channel_label'],
                cand['text'],
                cand['date'],
            )
        except Exception as e:
            ai_errors += 1
            print(f"    ERROR: {e}")
            time.sleep(1)
            continue

        if not result.get('is_task'):
            skipped_by_ai += 1
            print(f"    skip — {result.get('ai_summary', '')[:80]}")
            time.sleep(0.3)
            continue

        contact = cand.get('contact')
        row = TaskRecommendation(
            title             = result.get('title') or f"Follow up: {cand['text'][:60]}",
            description       = result.get('description'),
            priority          = result.get('priority', 'medium'),
            category          = result.get('category'),
            linked_contact_id = contact.id if contact else None,
            source            = 'slack',
            source_context    = cand['text'][:500],
            ai_summary        = result.get('ai_summary'),
            status            = 'pending',
        )
        session.add(row)
        task_saved += 1
        print(f"    task — {result.get('title', '')[:80]}")
        time.sleep(0.3)

    # ── Commit ────────────────────────────────────────────────────────────────
    try:
        session.commit()
    except Exception as e:
        session.rollback()
        sys.exit(f"ERROR: Failed to save to Supabase: {e}")
    finally:
        session.close()

    est_cost = len(deduped) * AI_COST_PER_CALL
    print(f"\n=== Slack sync complete ===")
    print(f"  DM conversations scanned : {len(dm_channels)}")
    print(f"  @mentions found          : {mention_count}")
    print(f"  Interactions logged      : {len(interactions)}")
    print(f"  Inbox recs created       : {inbox_saved}")
    print(f"  Task recs created        : {task_saved}")
    print(f"  Skipped by AI            : {skipped_by_ai}")
    print(f"  AI errors                : {ai_errors}")
    print(f"  Est. AI cost             : ${est_cost:.3f}")
    if cap_reached:
        print(f"  Cap reached              : yes (max {MAX_AI_CALLS}/run)")


if __name__ == '__main__':
    main()
