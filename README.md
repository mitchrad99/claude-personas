# AAO CRM

A personal CRM for Mitch Radakovich, board chair of [All Aboard Ohio](https://allaboardohio.org), built to support a three-month DC advocacy sabbatical focused on fundraising, congressional outreach, and building a national passenger rail coalition. It syncs email activity from Gmail automatically, surfaces AI-powered recommendations for new contacts from unrecognized senders, and includes a natural-language chat interface powered by Claude for logging meetings and creating follow-up tasks.

## Stack

| Layer | Technology |
|---|---|
| Web app | Flask (Python 3.11), gunicorn, Render.com |
| Database | SQLAlchemy ORM, Supabase (PostgreSQL) |
| Auth | HTTP Basic Auth via flask-httpauth |
| AI | Anthropic API — Claude Sonnet (chat), Claude Haiku (inbox scan) |
| Email | Gmail API, OAuth 2.0 |
| Automation | GitHub Actions, cron every 6 hours |

## Diagrams

- [System Architecture](docs/system-architecture.md)
- [Data Model](docs/data-model.md)

## Environment variables

| Variable | Where | Description |
|---|---|---|
| `DATABASE_URL` | Render | Supabase PostgreSQL connection string |
| `SUPABASE_URL` | GitHub Actions | Same connection string (used by sync scripts) |
| `SECRET_KEY` | Render | Flask session secret — generate with `openssl rand -base64 32` |
| `CRM_USERNAME` | Render | HTTP Basic Auth username (default: `admin`) |
| `CRM_PASSWORD` | Render | HTTP Basic Auth password |
| `ANTHROPIC_API_KEY` | Render + GitHub Actions | Anthropic API key |
| `GMAIL_TOKEN_JSON` | GitHub Actions | Base64-encoded Gmail OAuth `token.json` |

## Running locally

```bash
cd crm
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export CRM_PASSWORD=localpass
export ANTHROPIC_API_KEY=sk-ant-...

python3 app.py
# → http://localhost:5000  (SQLite, no Gmail sync)
```

Local dev uses SQLite (`crm/aao_crm.db`). Postgres is used in production. The chat tab works locally as long as `ANTHROPIC_API_KEY` is set. Gmail sync requires running `crm/auth_gmail.py` once to generate `token.json`.

## GitHub Actions secrets

Three secrets required in repo Settings → Secrets → Actions:

```
SUPABASE_URL
GMAIL_TOKEN_JSON
ANTHROPIC_API_KEY
```

See `crm/auth_gmail.py` for the one-time Gmail OAuth setup flow.

## Project layout

```
crm/
├── app.py                  # Flask routes and API endpoints
├── models.py               # SQLAlchemy ORM (6 tables)
├── chat.py                 # Claude chat engine with CRM tool use
├── gmail_sync.py           # Updates contact email fields from Gmail
├── inbox_scan.py           # AI-powered inbox scan for unknown senders
├── auth_gmail.py           # One-time local OAuth flow for Gmail
├── migrate_to_supabase.py  # One-time SQLite → Supabase migration script
├── requirements.txt
├── Procfile                # web: gunicorn app:app
├── runtime.txt             # python-3.11.0
├── migrations/             # Standalone SQL migration files
└── templates/
    └── index.html          # Single-page web UI

docs/
├── system-architecture.md  # Mermaid system diagram
└── data-model.md           # Mermaid ER diagram

.github/
└── workflows/
    └── gmail_sync.yml      # Cron job: gmail_sync.py + inbox_scan.py
```

---

## Original project: Claude Persona CLI

The root `chat.py` and `personas/` directory contain a separate earlier project — a multi-persona Claude CLI with persistent memory summaries. See the original README content below for details.

Each persona in `personas/` is a JSON file with `system_prompt`, `history`, and `memory_summary`. When history exceeds 15 turns, older messages are compressed into `memory_summary` automatically.

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python chat.py
```
