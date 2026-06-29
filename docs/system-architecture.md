# System Architecture

```mermaid
flowchart TD
    subgraph Client["Client"]
        User["User\nbrowser / mobile"]
    end

    subgraph Render["Render.com"]
        Flask["Flask + gunicorn\nHTTP Basic Auth\ncrm/app.py"]
    end

    subgraph DB["Supabase"]
        PG["PostgreSQL\n6 tables"]
    end

    subgraph GHA["GitHub Actions — cron every 6 hours"]
        Sync["gmail_sync.py\nupdates contact email fields"]
        Scan["inbox_scan.py\nfinds unknown senders"]
    end

    subgraph APIs["External APIs"]
        Gmail["Gmail API\nOAuth 2.0"]
        Claude["Anthropic API\nClaude Sonnet / Haiku"]
    end

    User       -->|"HTTPS"| Flask
    Flask      -->|"SQLAlchemy / psycopg2"| PG
    Flask      -->|"chat + AI tool use"| Claude

    Sync       -->|"search threads per contact"| Gmail
    Gmail      -->|"email metadata + dates"| Sync
    Sync       -->|"UPDATE contacts\nlast_email_*"| PG

    Scan       -->|"scan inbox for\nunknown senders"| Gmail
    Gmail      -->|"messages + snippets"| Scan
    Scan       -->|"evaluate sender\n≤20 calls / run"| Claude
    Claude     -->|"recommendation JSON"| Scan
    Scan       -->|"INSERT inbox_recommendations"| PG
```

## Component notes

| Component | Role |
|---|---|
| **Render.com** | Hosts the Flask web app. `DATABASE_URL` injected as env var. Auto-deploys on push to `main`. |
| **Supabase** | Managed PostgreSQL. Schema created via `init_db()` on startup + SQL migrations in `crm/migrations/`. |
| **gmail_sync.py** | Runs every 6 h. For each contact with an email address, finds the most recent thread and writes `last_email_date`, `last_email_subject`, `last_email_direction`, `last_synced_at`. |
| **inbox_scan.py** | Runs every 6 h after `gmail_sync.py`. Scans inbox for senders not in the contacts table, filters automated senders, calls Claude Haiku (≤ 20 calls, ~$0.02 max per run) to evaluate each, saves `pending` recommendations. |
| **Inbox tab** | Web UI review queue — Accept creates the contact/task, Dismiss marks dismissed. |
| **Chat tab** | Claude Sonnet with tool use — describe a meeting in plain English and it updates contact records, creates tasks, and drafts emails. |
```
