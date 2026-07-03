# AAO CRM — `crm/`

A personal relationship management tool for Mitch Radakovich, board chair of [All Aboard Ohio](https://allaboardohio.org) (AAO), a nonpartisan 501(c)3 passenger rail advocacy nonprofit. The app was built to support a three-month DC sabbatical in fall 2026 focused on congressional outreach, fundraising, and building a national rail coalition. It is a single-user, password-protected web app — not a multi-tenant SaaS. It tracks contacts, grant prospects, tasks, DC organizations, and career opportunities; syncs email activity automatically from Gmail; scans the inbox for unknown senders using Claude Haiku and surfaces AI-powered recommendations; and includes a natural-language chat interface (Claude Sonnet with tool use) for logging meetings in plain English.

---

## Stack

| Layer | Technology |
|---|---|
| Web framework | Flask 2.3+, gunicorn |
| Database | SQLAlchemy ORM — SQLite locally, PostgreSQL (Supabase) in production |
| Auth | HTTP Basic Auth via `flask-httpauth` + `werkzeug.security` |
| AI — chat | Anthropic `claude-sonnet-4-6` with tool use |
| AI — inbox scan | Anthropic `claude-haiku-4-5-20251001` (~$0.001/call) |
| AI — Slack sync | Anthropic `claude-haiku-4-5-20251001` (~$0.001/call) |
| Email | Gmail API, OAuth 2.0 (readonly scope) |
| Slack | Slack Bot token (Bot API) |
| Deployment | Render.com (auto-deploy from `main`); root directory = `crm/` |
| Automation | GitHub Actions cron, every 6 hours |

---

## Project layout

```
crm/
├── app.py                   # Flask routes and all API endpoints
├── models.py                # SQLAlchemy ORM — 11 tables, dual SQLite/Postgres support
├── chat.py                  # Claude Sonnet chat engine with CRM tool use
├── gmail_sync.py            # Updates contact email fields from Gmail (scheduled)
├── inbox_scan.py            # AI-powered inbox scan for unknown senders (scheduled)
├── slack_sync.py            # Slack DM/channel scan + AI task triage (scheduled)
├── auth_gmail.py            # One-time local OAuth setup — generates token.json
├── migrate_to_supabase.py   # One-time SQLite → Supabase migration (idempotent)
├── requirements.txt
├── Procfile                 # web: gunicorn app:app
├── runtime.txt              # python-3.11.0
├── scripts/
│   ├── seed_test_db.py          # Seed local_test.db with fake data (TEST_MODE dev)
│   ├── merge_contacts.py        # One-off deduplication helper
│   └── match_slack_users.py     # One-off Slack user ID matcher
├── migrations/
│   ├── add_email_sync_fields.sql      # Adds last_email_* columns to contacts
│   ├── add_inbox_recommendations.sql  # Creates inbox_recommendations table
│   ├── add_task_category.sql          # Adds category column to tasks
│   ├── add_interactions.sql           # Creates interactions table
│   ├── add_contact_notes.sql          # Creates contact_notes table
│   ├── add_contact_relationships.sql  # Creates contact_relationships table
│   ├── add_task_recommendations.sql   # Creates task_recommendations table
│   ├── add_slack_user_id.sql          # Adds slack_user_id column to contacts
│   └── add_processed_gmail_messages.sql  # Creates processed_gmail_message_ids table
└── templates/
    └── index.html           # Single-page app (~1200 lines, vanilla JS, no framework)
```

---

## Database schema

Schema is created at startup by `init_db()` (`Base.metadata.create_all`). Additional columns added via SQL migration files in `crm/migrations/` (run once in Supabase SQL editor).

### `contacts`

The core table. Each row is a person in Mitch's professional network.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `name` | string(255) NOT NULL | |
| `organization` | string(255) | |
| `title` | string(255) | |
| `email` | string(255) | Used as lookup key by gmail_sync.py |
| `phone` | string(50) | |
| `warmth` | string(10) | `cold` \| `warm` \| `hot` — manually set |
| `category` | string(20) | `advocacy` \| `funder` \| `government` \| `media` \| `peer_org` \| `dc_network` \| `other` |
| `last_contact_date` | date | Manually set; drives "stale contact" logic (>30 days = stale) |
| `notes` | text | Free-form; chat engine appends with `append_notes=true` |
| `last_email_date` | datetime | Written by gmail_sync.py — most recent email timestamp |
| `last_email_subject` | string(500) | Written by gmail_sync.py |
| `last_email_direction` | string(10) | `inbound` \| `outbound` — written by gmail_sync.py |
| `last_synced_at` | datetime | Written by gmail_sync.py — controls the search window on next run |
| `slack_user_id` | string(100) | Slack user ID (e.g., `U01ABC123`); used by slack_sync.py to match DMs |
| `created_at` | datetime | |
| `updated_at` | datetime | |

### `funders`

Grant prospects and fundraising pipeline.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `organization` | string(255) NOT NULL | Foundation or corporate name |
| `type` | string(20) | `foundation` \| `corporate` \| `government` \| `individual` |
| `focus_areas` | text | Program interest areas |
| `program_officer_name` | string(255) | Plain-text name |
| `program_officer_contact_id` | integer FK → contacts.id | Optional — links to a Contact row |
| `ask_amount` | integer | Dollar amount being requested |
| `status` | string(30) | `research` → `identified` → `outreach` → `meeting_scheduled` → `proposal_submitted` → `funded` \| `declined` \| `dormant` |
| `deadline` | date | Proposal or grant deadline |
| `notes` | text | |
| `created_at` / `updated_at` | datetime | |

### `tasks`

Follow-up actions. Can be linked to a contact, a funder, both, or neither.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `title` | string(255) NOT NULL | |
| `description` | text | |
| `due_date` | date | |
| `priority` | string(10) | `low` \| `medium` \| `high` |
| `status` | string(10) | `pending` \| `done` |
| `category` | string(30) | `outreach` \| `intro_followup` \| `fundraising` \| `policy` \| `admin` \| `career` \| `sabbatical_prep` |
| `linked_contact_id` | integer FK → contacts.id | Optional |
| `linked_funder_id` | integer FK → funders.id | Optional |
| `created_at` / `updated_at` | datetime | |

### `dc_orgs`

DC-based organizations relevant to rail advocacy — think tanks, congressional offices, agencies, coalitions.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `name` | string(255) NOT NULL | |
| `type` | string(20) | `think_tank` \| `advocacy` \| `congressional` \| `agency` \| `coalition` \| `media` |
| `priority` | string(10) | `low` \| `medium` \| `high` |
| `key_contact_id` | integer FK → contacts.id | Optional primary contact at the org |
| `notes` | text | |
| `created_at` / `updated_at` | datetime | |

### `opportunities`

Career and engagement opportunities Mitch is tracking (jobs, fellowships, board seats, speaking).

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `title` | string(255) NOT NULL | |
| `organization` | string(255) | |
| `type` | string(20) | `job` \| `fellowship` \| `board` \| `consulting` \| `speaking` |
| `status` | string(20) | `identified` \| `applied` \| `interviewing` \| `offer` \| `declined` \| `closed` |
| `deadline` | date | |
| `salary_range` | string(100) | |
| `notes` | text | |
| `created_at` / `updated_at` | datetime | |

### `inbox_recommendations`

AI-generated suggestions from inbox_scan.py. Each row represents an unknown sender that Claude evaluated as potentially worth tracking. Stays in this table until accepted or dismissed.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `sender_name` | string(255) | Parsed from the `From:` header |
| `sender_email` | string(255) | Parsed sender address (lowercased) |
| `email_subject` | string(500) | Most recent subject line from this sender |
| `email_date` | datetime | Date of the most recent email from this sender |
| `email_snippet` | text | Up to 200 chars of message preview |
| `recommendation_type` | string(20) | `new_contact` \| `new_task` |
| `recommendation_json` | text | JSON string of Claude-suggested field values; pre-fills the Inbox form |
| `recommendation_summary` | text | One-sentence explanation from Claude |
| `status` | string(20) | `pending` \| `accepted` \| `dismissed` |
| `created_at` | datetime | |

### `interactions`

Touchpoint log for contact meetings, calls, events, etc. Written by the chat engine during debriefs.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `contact_id` | integer FK → contacts.id NOT NULL | |
| `date` | date NOT NULL | Date of the interaction |
| `type` | string(20) | `meeting` \| `call` \| `event` \| `coffee` \| `text` \| `linkedin` |
| `location` | string(255) | Where it happened (optional) |
| `notes` | text | What was discussed, outcomes, impressions |
| `follow_up_needed` | boolean | Default false |
| `created_at` / `updated_at` | datetime | |

Indexed on `contact_id` and `date`.

### `contact_notes`

Append-only timestamped notes per contact. No `updated_at` — rows are never modified after insert.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `contact_id` | integer FK → contacts.id NOT NULL | |
| `note` | text NOT NULL | The note content |
| `source` | string(20) | `manual` \| `chat_debrief` \| `ai_generated` — default `manual` |
| `created_at` | datetime | |

Indexed on `contact_id`.

### `contact_relationships`

Social graph edges between contacts. Tracks introductions made, promised, or pending.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `from_contact_id` | integer FK → contacts.id NOT NULL | The contact who initiated or made the intro |
| `to_contact_id` | integer FK → contacts.id NOT NULL | The contact who was introduced or connected to |
| `type` | string(30) | `introduced_by` \| `wants_to_connect` \| `peer` \| `mentor` \| `referred_funder` |
| `status` | string(20) | `completed` \| `pending` — default `completed` |
| `notes` | text | Context about the relationship or intro |
| `created_at` / `updated_at` | datetime | |

Constraints: `CHECK (from_contact_id != to_contact_id)` and `UNIQUE (from_contact_id, to_contact_id, type)`. Indexed on both FK columns.

### `processed_gmail_message_ids`

Deduplication log for Gmail messages already evaluated by the task triage phase of inbox_scan.py. Prevents re-evaluating the same message on subsequent runs regardless of outcome.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `message_id` | string(255) NOT NULL UNIQUE | Gmail message ID (e.g., `18a9c2d3e4f5`) |
| `processed_at` | datetime | When the evaluation ran |

### `task_recommendations`

AI-generated task suggestions pending human review. Distinct from `inbox_recommendations` (which is tied to unknown email senders) — task recommendations can come from any source (Gmail context, Slack, manual AI suggestions). Stays in this table until accepted or dismissed.

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `title` | string(255) NOT NULL | Suggested task title |
| `description` | text | Optional description |
| `due_date` | date | Suggested due date |
| `priority` | string(10) | `low` \| `medium` \| `high` |
| `linked_contact_id` | integer FK → contacts.id | Optional — contact this task relates to |
| `linked_funder_id` | integer FK → funders.id | Optional |
| `category` | string(30) | Same values as `tasks.category` |
| `source` | string(20) | `gmail` \| `slack` \| `manual` |
| `source_context` | text | Email subject or message snippet that triggered the recommendation |
| `ai_summary` | text | One-sentence explanation from Claude on why this task matters |
| `status` | string(20) | `pending` \| `accepted` \| `dismissed` |
| `created_at` | datetime | |

### `slack_sync.py` — Slack DM and channel scan

**What it does:** Scans Slack for contact activity and AI-generated task signals.

- **DMs**: Iterates all open DM conversations. Matches the other user to a contact via `slack_user_id`. For matched contacts, updates `last_contact_date` and logs an `Interaction` record (type=`text`). For unmatched DM senders, creates an `inbox_recommendation` row (type=`new_contact`) with the Slack display name and message snippet.
- **@mentions**: Scans all channels the bot is a member of. Filters messages that contain `<@MITCH_USER_ID>`. Logs interactions for matched contacts.
- **SLACK_CHANNEL_IDS**: Scans any channels listed in the env var regardless of @mentions. Logs interactions for matched contacts.
- **AI triage**: Evaluates all collected messages (newest-first, cap 20) for task-worthy signals — requests, commitments, deadlines, follow-up cues. Creates `task_recommendation` rows with `source='slack'` for anything actionable.

**Frequency:** Every 6 hours via GitHub Actions, in a separate `slack-sync` job that runs after the `sync` job (Gmail + inbox scan) completes.

**Cost:** At most 20 Anthropic API calls per run → **≤ $0.02 per run**.

**Scan window:** Uses `MAX(task_recommendations.created_at WHERE source='slack')` as the look-back timestamp. Defaults to 24 hours ago on first run or when no Slack task recs exist.

**Deduplication:**
- Unmatched DM senders: skipped if their Slack user ID already appears in a pending `inbox_recommendation.sender_email`.
- AI triage candidates: deduplicated by (sender_id, text prefix) before capping at 20.

**Failure modes:**
- Missing required env vars → immediate `sys.exit` with a clear message before any imports.
- Slack API auth failure → `sys.exit`.
- Per-channel or per-message Slack API error → logs warning, continues to next item.
- Per-message Anthropic API error → logs error, continues (does not abort run).
- DB commit failure → rolls back and `sys.exit`.

**Required bot token scopes:** `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `users:read`.

---

## UI tabs

The frontend is a single HTML page (`templates/index.html`) with tab-based navigation. No framework — vanilla JS with `fetch()`. Tabs are rendered client-side; all data comes from JSON API calls.

### Dashboard

Loads on startup. Shows stat pills in the header: tasks due this week, overdue tasks, stale contacts (no contact in 30 days), hot funders (status = outreach/meeting_scheduled/proposal_submitted). Also sets the Inbox badge count from `pending_inbox` in `/api/summary` so the badge shows before the user clicks the tab.

### Contacts

Table view of all contacts. Columns: Name, Org (hidden on mobile), Warmth, Last Email, Status, Next Task (hidden on mobile).

**Email status logic** (drives the Status column):
- `Reply needed` (red) — most recent email was inbound and arrived >3 days ago
- `Follow up` (yellow) — most recent email was outbound >7 days ago
- `Active` (green) — email activity in the past 3 days (inbound) or 7 days (outbound)
- `No activity` (gray) — no email synced

The Last Email column shows direction (`in` / `out`), relative age, and subject (subject hidden on mobile via `.hide-sm`). Sort options: Name A–Z or "Needs attention" (sorts by email status priority, then days since contact).

### Funders

List of grant prospects with status, ask amount, deadline, and program officer. Filterable by status.

### Tasks

Pending tasks ordered by due date. Shows linked contact/funder name. Clicking a task marks it done.

### DC Orgs

Washington DC organizations by priority. Each row shows type, priority, and the key contact if linked.

### Opportunities

Career pipeline — jobs, fellowships, speaking, etc. Ordered by deadline.

### Chat

Natural-language interface to the CRM. Type a meeting debrief in plain English ("Just met with Jane Smith from DOT — she's interested in rail funding, wants a proposal by October. Follow up in 2 weeks.") and Claude Sonnet will:
- Look up or create the contact record
- Update warmth, title, org
- Append to notes
- Create a follow-up task with a specific due date
- Create or update a funder record if a dollar amount is mentioned

The chat engine (`chat.py`) maintains a rolling history (last 20 turns) in `chat_history.json`. History beyond 20 turns is summarized in a stub message. The model is `claude-sonnet-4-6` with tool use. A "New conversation" button (↺) in the chat input bar calls `POST /api/chat/reset`, which clears both the in-memory history and `chat_history.json`, and resets the displayed messages to the initial greeting.

**Meeting debrief behavior:** When Mitch describes a meeting or call, the engine always calls `log_interaction` (to record the touchpoint) and `add_contact_note` (source=`chat_debrief`) in addition to updating the contact record. When Mitch mentions that someone introduced them to another person, the engine calls `log_relationship` with `type=introduced_by` and `status=completed`. When someone promises a future introduction, the engine calls `log_relationship` with `type=wants_to_connect` and `status=pending`, then also creates a follow-up task.

Available tools (13): `get_contacts`, `create_or_update_contact`, `create_task`, `get_tasks`, `create_or_update_funder`, `get_funders`, `get_dc_orgs`, `create_opportunity`, `draft_email`, `get_summary`, `log_interaction`, `add_contact_note`, `log_relationship`.

### Inbox

Two-section review queue for AI-generated items. The tab badge counts total pending across both sections; `pending_inbox` in `/api/summary` includes both `inbox_recommendations` and `task_recommendations`.

**New Contacts** — `inbox_recommendations` rows with `recommendation_type = new_contact` from inbox_scan.py. Each card shows sender name, email, email snippet, Claude's rationale, and pre-filled suggested fields editable before accepting.
- **Accept** → creates a Contact row from the (edited) suggested fields
- **Dismiss** → marks `dismissed`

**Suggested Tasks** — `task_recommendations` rows. Each card shows the task title, linked contact (if any), source badge (Gmail / Slack), the triggering context snippet, Claude's one-sentence AI summary, and suggested due date. All fields are editable before accepting.
- **Accept** → creates a Task row from the (edited) fields, preserves `linked_contact_id` and `linked_funder_id` from the recommendation
- **Dismiss** → marks `dismissed`

---

## Automated workflows

### `gmail_sync.py` — contact email sync

**What it does:** For every contact that has an email address, searches Gmail for the most recent thread involving that address since the last sync. Updates `last_email_date`, `last_email_subject`, `last_email_direction`, and `last_synced_at` on the contact row. All updates are committed in a single transaction at the end.

**Frequency:** Every 6 hours via GitHub Actions (immediately before inbox_scan.py in the same workflow).

**Cost:** Zero — uses Gmail API with read-only OAuth. No AI calls.

**Rate limiting:** Sleeps 0.25 seconds per contact to stay well under Gmail's 250 quota units/sec limit.

**Failure modes:**
- Missing `SUPABASE_URL` or `GMAIL_TOKEN_JSON` → immediate `sys.exit` with a clear message before any imports.
- Expired OAuth token with a valid refresh token → auto-refreshes silently.
- Expired token with no refresh token → `sys.exit` with instructions to re-run `auth_gmail.py`.
- Per-contact Gmail API error → logs the error, increments an error counter, continues to the next contact (does not abort the whole run).
- DB commit failure → rolls back and `sys.exit`.

**Direction detection:** Checks whether the user's own email address appears in the `From:` header of the most recent message. Outbound if yes, inbound if no.

---

### `inbox_scan.py` — AI inbox scan

**What it does:** Two-phase scan per run:

1. **Unknown sender evaluation** — Scans Gmail inbox for senders not in the contacts table. For each unknown sender, calls Claude Haiku to evaluate whether to recommend a `new_contact`, `new_task`, or `skip`. Saves non-skip results to `inbox_recommendations` as `pending` rows.

2. **Task triage for known contacts** — After the unknown-sender phase, scans recent inbound inbox messages from contacts already in the CRM. For each, calls Claude Haiku to evaluate whether the email contains a request, commitment, deadline, or follow-up signal that warrants a task. Creates `task_recommendation` rows with `source='gmail'`, the email subject as `source_context`, and `linked_contact_id` set. Evaluated message IDs are written to `processed_gmail_message_ids` to prevent re-evaluation on future runs.

**Frequency:** Every 6 hours via GitHub Actions, immediately after gmail_sync.py.

**Cost:** At most 30 Anthropic API calls per run, ~$0.001 each → **≤ $0.03 per run**, ≤ $0.12/day. Prints estimated cost at the end of each run.

**AI call budget:** Unmatched sender evaluation takes priority up to 20 calls; task triage gets the remaining budget (up to 10 calls if sender evaluation used its full 20).

**Scan window:** Uses `MAX(inbox_recommendations.created_at)` as the look-back date. On first run (no rows yet), defaults to 30 days ago.

**Filters before unknown-sender AI evaluation:**
1. Skip senders matching automated patterns: `noreply`, `no-reply`, `donotreply`, `notification`, `newsletter`, `mailer-daemon`, `bounce@`, `bounces@`, `unsubscribe`, `automated`, `postmaster@`, `alerts@`, `updates@`.
2. Skip senders whose email is already in the contacts table.
3. Skip senders already in inbox_recommendations with status=`pending` (avoids duplicates across runs).

**Task triage deduplication:** Message IDs already in `processed_gmail_message_ids` are skipped entirely. One candidate per sender per run (most recent unprocessed message). All evaluated messages are marked as processed regardless of outcome (signal found, no signal, or error).

**AI prompt:** `claude-haiku-4-5-20251001` is given the sender name, address, subject, date, and snippet. Markdown code fences are stripped from the response before JSON parsing in case the model adds them.

**Failure modes:**
- Missing env vars → immediate `sys.exit` before imports.
- Gmail fetch failure for unknown senders → `sys.exit`.
- Gmail fetch failure for task triage → logs warning, skips task triage (does not abort run).
- Per-sender or per-message Anthropic API error → logs error, continues (does not abort run).
- JSON parse failure from Haiku → same as API error — logs and continues.
- DB commit failure → rolls back and `sys.exit`.

---

## API endpoints

All endpoints require HTTP Basic Auth. All responses are JSON.

| Method | Path | Description |
|---|---|---|
| GET | `/api/contacts` | List contacts; filter by `?warmth=`, `?category=`, `?stale_days=N` |
| POST | `/api/contacts` | Create contact (`name` required) |
| PUT | `/api/contacts/<id>` | Update contact fields |
| GET | `/api/funders` | List funders; filter by `?status=` |
| POST | `/api/funders` | Create funder (`organization` required) |
| PUT | `/api/funders/<id>` | Update funder fields |
| GET | `/api/tasks` | List tasks; filter by `?status=`, `?due_before=` |
| POST | `/api/tasks` | Create task (`title` required) |
| PUT | `/api/tasks/<id>` | Update task fields |
| GET | `/api/dc_orgs` | List DC orgs |
| POST | `/api/dc_orgs` | Create DC org (`name` required) |
| GET | `/api/opportunities` | List opportunities (ordered by deadline) |
| POST | `/api/opportunities` | Create opportunity (`title` required) |
| GET | `/api/summary` | Dashboard stats + `pending_inbox` count |
| POST | `/api/chat` | Send message to Claude chat engine |
| POST | `/api/chat/reset` | Clear conversation history (in-memory + `chat_history.json`) |
| GET | `/api/inbox` | List pending inbox recommendations |
| POST | `/api/inbox/<id>/accept` | Accept recommendation → creates Contact or Task |
| POST | `/api/inbox/<id>/dismiss` | Dismiss recommendation |
| GET | `/api/interactions` | List interactions; filter by `?contact_id=`, `?type=`, `?follow_up_needed=` |
| POST | `/api/interactions` | Create interaction (`contact_id` and `date` required) |
| PUT | `/api/interactions/<id>` | Update interaction fields |
| GET | `/api/contact_notes` | List notes for a contact (`contact_id` required) |
| POST | `/api/contact_notes` | Append a note (`contact_id` and `note` required) |
| GET | `/api/contact_relationships` | List relationship edges; filter by `?contact_id=` (matches either side), `?type=` |
| POST | `/api/contact_relationships` | Create relationship (`from_contact_id` and `to_contact_id` required) |
| PUT | `/api/contact_relationships/<id>` | Update relationship type, status, or notes |
| GET | `/api/task_recommendations` | List pending task recommendations |
| POST | `/api/task_recommendations/<id>/accept` | Accept → creates a Task, marks recommendation `accepted` |
| POST | `/api/task_recommendations/<id>/dismiss` | Dismiss recommendation |

`GET /api/contacts` also annotates each contact with `next_task` (title of oldest pending task linked to that contact) and `next_task_due`.

---

## Environment variables

### Render.com (web app)

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | Supabase PostgreSQL connection string (`postgresql://...`) |
| `CRM_PASSWORD` | Yes | HTTP Basic Auth password — app refuses to start without this |
| `CRM_USERNAME` | No | HTTP Basic Auth username (default: `admin`) |
| `SECRET_KEY` | No | Flask session secret — generate with `openssl rand -base64 32` |
| `ANTHROPIC_API_KEY` | For chat tab | Anthropic API key; chat tab errors gracefully if missing |
| `TEST_MODE` | No | Set to `true` to use `crm/local_test.db` instead of Supabase (local dev only) |

### GitHub Actions secrets

| Secret | Description |
|---|---|
| `SUPABASE_URL` | Same PostgreSQL URL used as `DATABASE_URL` on Render |
| `GMAIL_TOKEN_JSON` | Base64-encoded `token.json` from the Gmail OAuth flow |
| `ANTHROPIC_API_KEY` | Anthropic API key (used by inbox_scan.py and slack_sync.py) |
| `SLACK_BOT_TOKEN` | Slack Bot token (`xoxb-...`); required for slack_sync.py |
| `SLACK_USER_ID` | Mitch's Slack user ID (e.g., `U01ABC123`); required for slack_sync.py |
| `SLACK_CHANNEL_IDS` | Optional comma-separated channel IDs to always scan (e.g., `C01ABC,C02DEF`) |

---

## Local development setup

```bash
# From repo root
python3 -m venv crm/venv
source crm/venv/bin/activate
pip install -r crm/requirements.txt

export CRM_PASSWORD=localpass
export ANTHROPIC_API_KEY=sk-ant-...   # only needed for chat tab

cd crm
python3 app.py
# → http://localhost:5000
# Username: admin  Password: localpass
```

### Running tests

```bash
source crm/venv/bin/activate
pip install -r crm/requirements-dev.txt   # first time only

cd crm
pytest                          # run all tests
pytest -v                       # verbose — shows each test name
pytest --cov=. --cov-report=term-missing   # with line coverage
pytest tests/test_models.py     # single file
```

Tests use an in-memory SQLite database — no Supabase connection, no API keys,
no external calls. The conftest.py sets `DATABASE_URL=sqlite:///:memory:` and
patches the SQLAlchemy engine before importing the app, so the test run is
fully self-contained. All external APIs (Anthropic, Slack) are mocked.

For local dev, use `TEST_MODE=true` (see Test mode section below). Without it, `DATABASE_URL` must be set or the app raises `RuntimeError` at startup.

The Gmail sync scripts (`gmail_sync.py`, `inbox_scan.py`) do not run locally unless you also set `SUPABASE_URL` and `GMAIL_TOKEN_JSON`. To run them locally against production Supabase, set those vars and run from the repo root:

```bash
SUPABASE_URL=postgresql://... GMAIL_TOKEN_JSON=... python3 crm/gmail_sync.py
```

### Test mode (local throwaway database)

`TEST_MODE=true` runs the app against a local SQLite file (`crm/local_test.db`) instead of `DATABASE_URL` / Supabase. It is safe to use on your laptop with no risk of touching production data.

**First-time setup:**

```bash
source crm/venv/bin/activate
cd crm

# Create and seed the test database with fake contacts, funders, tasks, etc.
TEST_MODE=true python scripts/seed_test_db.py

# Start the app against the test database
TEST_MODE=true CRM_PASSWORD=localpass python app.py
# → http://localhost:5000  (username: admin, password: localpass)
```

Re-running `seed_test_db.py` drops all tables and recreates them — safe to do any time you want a clean slate.

**What changes in TEST_MODE:**

| Feature | Behavior |
|---|---|
| Database | `crm/local_test.db` (SQLite) instead of `DATABASE_URL` / Supabase |
| UI | Amber "TEST MODE — local data only" banner at the top of every page |
| Chat | Returns a mocked response — no Anthropic API calls, no key required |
| `gmail_sync.py` | Refuses to run: prints a clear error and exits |
| `slack_sync.py` | Refuses to run: prints a clear error and exits |

Nothing changes when `TEST_MODE` is unset or `false` — production runs exactly as today.

**`local_test.db` is gitignored** and will never be committed.

### Gmail OAuth setup (one-time)

Only needed if you're setting up Gmail sync for the first time or refreshing after a revoked token.

1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID (type: Desktop app)
3. Download the JSON and save as `crm/credentials.json` (gitignored)
4. With the venv active: `python3 crm/auth_gmail.py`
5. A browser window opens — sign in and grant read-only Gmail access
6. `crm/token.json` is written (gitignored)
7. The script prints the base64-encoded value — paste it as the `GMAIL_TOKEN_JSON` secret in GitHub Settings → Secrets → Actions

---

## Security

**Never hardcode credentials.** All secrets are passed via environment variables — never committed to the repo. This is enforced at two layers:

- **Pre-commit hook:** `gitleaks` scans staged changes before every commit and blocks the commit if it finds anything secret-shaped. To install:
  ```bash
  brew install pre-commit   # one-time
  pre-commit install        # run from repo root — activates the hook for this clone
  ```
  The hook is configured in `.pre-commit-config.yaml` and runs gitleaks v8.30.1.

- **CI check:** Every push and PR runs gitleaks against the diff in the `Secret Scan` job. A failing secret scan blocks merge.

**What's gitignored:** `.env`, `.env.*`, `credentials.json`, `token.json`, `*.key`, `*.pem`. Never add exceptions for these.

**Adding a new secret:** Add it to `.env.example` with a placeholder value and document it in the Environment variables table above. Never put the real value in any committed file.

---

## Key design decisions

**SQLite locally, Postgres in production.** `models.py` reads `DATABASE_URL` from env. If `TEST_MODE=true`, it uses a local SQLite file (`local_test.db`) instead. If `TEST_MODE` is not set and `DATABASE_URL` is missing, the app raises `RuntimeError` at import time — no silent fallback. `check_same_thread=False` is only passed for SQLite. The `postgres://` → `postgresql://` prefix fix runs at import time because Render (like legacy Heroku) injects the old format that SQLAlchemy 1.4+ rejects.

**`CRM_PASSWORD` raises at startup if missing.** Rather than silently falling back to a default password or returning 500 errors later, the app raises `RuntimeError` immediately at import. This surfaces the missing secret in Render's deploy logs within seconds.

**`@app.before_request` for auth.** All routes are protected by a single `before_request` hook rather than decorating each endpoint. This means a new endpoint is protected by default — you have to opt out, not opt in.

**Chat engine lazy-loads.** `ChatEngine` (which imports `anthropic`) is instantiated on first `/api/chat` request, not at startup. This lets the Flask app start successfully on Render even if `ANTHROPIC_API_KEY` isn't set yet, so the other tabs work while the key is being configured.

**Inbox scan caps at 30 AI calls per run total.** Haiku is cheap but not free. Unknown-sender evaluation takes priority up to 20 calls; task triage for known contacts gets the remainder. The cap prevents a surprise bill if the inbox suddenly has hundreds of messages. Older candidates are deferred to the next run because the scan window advances after each run.

**UTC everywhere, 'Z' suffix in JS.** Python returns naive UTC datetimes as ISO strings without a timezone suffix. The frontend appends `'Z'` before parsing so browsers treat them as UTC instead of local time — otherwise a "3 days ago" label could be wrong depending on the user's timezone.

**No framework on the frontend.** The entire UI is one HTML file with vanilla JS and `fetch()`. Fast to load, zero build tooling, easy to read and modify in a single file. The tradeoff is that the file is long (~1200 lines).

---

## Roadmap

- **Funder detail view** — expand a funder row to show all fields, notes history, linked tasks, and a timeline of status changes
- **Contact detail view** — inline edit all fields, show linked tasks and full notes
- **Email draft modal** — use the `draft_email` chat tool output directly in the UI, not just in the chat tab
- **Token auto-refresh in GitHub Actions** — currently the OAuth token must be manually regenerated when it expires; the workflow could write an updated token back as a secret
- **Opportunity tracking** — the table exists but the Opportunities tab has no edit-in-place or status workflow yet
- **Search** — global search across contacts, funders, tasks by name/org/notes
