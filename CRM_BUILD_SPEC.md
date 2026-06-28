I want to build an AAO CRM. Here is the full spec:

## What we're building

A personal CRM for Mitch Radakovich (Board Chair, All Aboard Ohio) to manage contacts, funders, tasks, DC organizations, and career opportunities ahead of a 3-month DC sabbatical. The primary interface is an AI chat that updates the database from natural language meeting debriefs. Secondary interfaces are a web UI (for browsing/reviewing on desktop and phone) and a CLI (for quick adds).

## Tech stack

- Database: SQLite via SQLAlchemy (single file: crm/aao_crm.db)
- Backend: Python + Flask (REST API + serves the web UI)
- AI chat layer: Anthropic Python SDK with tool use (claude-sonnet-4-6)
- Frontend: Single HTML file with vanilla JS — mobile-responsive, no frameworks
- CLI: Python script for quick terminal access
- Future hosting: Supabase or Railway (design with env vars, not hardcoded paths)

## Project structure

claude-personas/
└── crm/
    ├── app.py              # Flask app — serves API and web UI
    ├── models.py           # SQLAlchemy models (5 tables)
    ├── chat.py             # AI chat engine with tool use
    ├── cli.py              # CLI interface
    ├── importer.py         # CSV importer for Google Sheets migration
    ├── templates/
    │   └── index.html      # Full web UI (single file)
    ├── aao_crm.db          # SQLite database (gitignored)
    └── README.md

## Database schema (5 tables)

### contacts
- id, name (required), organization, role/title, email, phone
- warmth: enum(cold, warm, hot)
- category: enum(advocacy, funder, government, media, peer_org, dc_network, other)
- last_contact_date, notes, created_at, updated_at

### funders
- id, organization (required)
- type: enum(foundation, corporate, government, individual)
- focus_areas (text), program_officer_name, program_officer_contact_id (FK → contacts)
- ask_amount (integer), status: enum(research, identified, outreach, meeting_scheduled, proposal_submitted, funded, declined, dormant)
- deadline, notes, created_at, updated_at

### tasks
- id, title (required), description, due_date, priority: enum(low, medium, high)
- status: enum(pending, done)
- linked_contact_id (FK → contacts), linked_funder_id (FK → funders)
- created_at, updated_at

### dc_orgs
- id, name (required), type: enum(think_tank, advocacy, congressional, agency, coalition, media)
- priority: enum(low, medium, high), key_contact_id (FK → contacts), notes
- created_at, updated_at

### opportunities
- id, title (required), organization
- type: enum(job, fellowship, board, consulting, speaking)
- status: enum(identified, applied, interviewing, offer, declined, closed)
- deadline, salary_range, notes, created_at, updated_at

## AI chat layer (chat.py)

Use Anthropic tool use (function calling) so Claude can read and write the database in response to natural language.

System prompt:
"You are Mitch Radakovich's chief of staff and CRM assistant. Mitch is Board Chair of All Aboard Ohio (AAO), a nonpartisan 501(c)3 passenger rail advocacy organization. He is preparing for a 3-month DC sabbatical (fall 2026) focused on fundraising, policy advocacy, and exploring a full-time advocacy career. Your job: update contact records after Mitch describes a meeting, create follow-up tasks with due dates, draft outreach emails, surface who needs follow-up. When Mitch describes a meeting, extract: contact name + org (update or create record), warmth signal, any dollar amounts (link to funders table), next steps (create tasks with specific due dates), key topics (add to notes). Always confirm what you've updated before offering to do more. Tone: direct, efficient, like a great EA. No fluff."

Tools to implement:
1. get_contacts — search by name/org or list stale ones (not contacted in N days)
2. create_or_update_contact — upsert a contact record
3. create_task — add a follow-up task linked to contact/funder
4. get_tasks — list pending tasks filtered by due date/priority
5. create_or_update_funder — upsert a funder record
6. get_funders — list funders by status
7. get_dc_orgs — list DC orgs by priority
8. create_opportunity — log a career opportunity
9. draft_email — generate a draft outreach/follow-up email (returns text, doesn't send)
10. get_summary — dashboard stats: tasks due this week, stale contacts, hot funders

Save chat history to crm/chat_history.json — last 20 turns in context, summarize beyond that.

## Web UI (templates/index.html)

Single-page app, mobile-responsive, vanilla CSS only, dark mode support.

Tabs: Chat (default) | Contacts | Funders | Tasks | DC Orgs | Opportunities

Chat tab: message bubbles, input at bottom, timestamps.

Contacts tab: Name | Org | Warmth (colored badge) | Last Contact | Next Task. Filter by warmth, category, days since contact. Click row to expand + edit inline.

Funders tab: Organization | Type | Ask Amount | Status | Deadline | Program Officer. Filter by status.

Tasks tab: Task | Due Date | Priority | Linked To | Status. Sort by due date. Overdue tasks highlighted red. Check off inline.

DC Orgs tab: Org | Type | Priority | Key Contact | Notes.

Opportunities tab: Role | Org | Type | Status | Deadline.

Design: works on iPhone Safari, thumb-friendly, no hover-only interactions. Colors: green for hot, amber for warm, gray for cold.

## Flask API endpoints

GET  /                         → serve index.html
GET  /api/contacts             → list (params: warmth, category, stale_days)
POST /api/contacts             → create
PUT  /api/contacts/<id>        → update
GET  /api/funders              → list (params: status)
POST /api/funders              → create
PUT  /api/funders/<id>         → update
GET  /api/tasks                → list (params: status, due_before)
POST /api/tasks                → create
PUT  /api/tasks/<id>           → update
GET  /api/dc_orgs              → list
POST /api/dc_orgs              → create
GET  /api/opportunities        → list
POST /api/opportunities        → create
GET  /api/summary              → dashboard stats
POST /api/chat                 → send message to AI, returns response + DB changes made

## CSV importer (importer.py)

Usage:
  python importer.py --contacts contacts.csv
  python importer.py --funders funders.csv
  python importer.py --tasks tasks.csv

Column mapping:
- contacts.csv: Name, Organization, Role, Email, Phone, Warmth, Last Contact, Notes
- funders.csv: Organization, Type, Focus Areas, Program Officer, Ask Amount, Status, Deadline, Notes
- tasks.csv: Title, Due Date, Priority, Status, Notes

Dedup on name+organization for contacts, organization for funders. Log imported vs skipped vs updated.

## CLI (cli.py)

python cli.py chat                   # open AI chat session
python cli.py contacts --stale 30   # contacts not touched in 30+ days
python cli.py tasks --due 7         # tasks due in next 7 days
python cli.py add-contact           # interactive prompt
python cli.py summary               # dashboard stats

## Environment variables

ANTHROPIC_API_KEY=...
DATABASE_URL=...          # defaults to ./aao_crm.db
PORT=5000
SECRET_KEY=...            # Flask session secret

## Seed data

After building schema, seed with these contacts:
- Veronica Nunamaker | Ohio Chamber of Commerce | warm
- Beau Mills | All Aboard NC | hot
- Mark Jeffreys | Cincinnati City Council | warm
- Joel Szabat | Amtrak | cold (last contact April 18)
- Sean Jeans-Gail | Rail Passengers Association | warm

And these tasks:
- "Send district membership breakdown to Veronica" — due in 2 weeks — high priority
- "Follow up with Beau on coalition call" — due in 1 week — high priority
- "Re-engage Joel Szabat post-summit" — due in 1 month — medium priority

## Build order

1. models.py + database setup
2. app.py skeleton with all routes
3. importer.py
4. chat.py with tool use
5. index.html web UI
6. cli.py
7. Seed data script
8. Test full flow: start Flask, open browser, send meeting debrief in chat, verify DB updated, verify UI reflects change

## Success criteria

- Can describe a meeting in plain English and have contact + task created automatically
- Web UI loads on iPhone Safari without horizontal scrolling
- CSV import works for contacts and funders
- All 5 tables visible and editable in web UI
- Tasks can be marked done from web UI
- App starts with: cd crm && python app.py

Please read this spec fully, then build the entire CRM exactly as specified. Start with models.py and work through the build order. Ask me if anything is unclear before starting.