# CLAUDE.md — AAO CRM

## Project context

This is a personal CRM for Mitch Radakovich, board chair of All Aboard Ohio (AAO), a nonpartisan 501(c)3 passenger rail advocacy nonprofit. It was built to support a three-month DC advocacy sabbatical in fall 2026 focused on congressional outreach, nonprofit fundraising, and building a national rail coalition. It is intentionally single-user — one login, one person's network — not a multi-tenant product. The app tracks contacts, grant prospects, follow-up tasks, DC organizations, and career opportunities; automatically syncs email activity from Gmail; uses Claude Haiku to scan the inbox for unknown senders and queue AI-powered recommendations for human review; and includes a natural-language chat interface (Claude Sonnet with tool use) for logging meetings in plain English.

---

## Architecture overview

| Layer | What's here |
|---|---|
| Web framework | Flask 2.3+ (`app.py`), served by gunicorn |
| Database | SQLAlchemy ORM (`models.py`) — SQLite locally, Supabase (PostgreSQL) in production |
| Auth | HTTP Basic Auth on every route via `flask-httpauth` + `werkzeug.security`; `@app.before_request` hook in `app.py` |
| AI — chat | `claude-sonnet-4-6` with tool use (`chat.py`); lazy-loaded on first `/api/chat` request |
| AI — inbox scan | `claude-haiku-4-5-20251001` in `inbox_scan.py`; ≤20 calls/run, ~$0.001 each |
| Email | Gmail API OAuth 2.0 (readonly scope); token stored as base64 env var |
| Hosting | Render.com — root directory = `crm/`, auto-deploy from `main`, `Procfile` = `web: gunicorn app:app` |
| Automation | GitHub Actions cron (`0 */6 * * *`) running `gmail_sync.py` then `inbox_scan.py` |
| Frontend | Single HTML file (`templates/index.html`), vanilla JS, `fetch()`, no framework or build step |

**Database URL resolution** (`models.py`, import time):
- Defaults to `sqlite:///crm/aao_crm.db` if `DATABASE_URL` is not set
- Replaces `postgres://` → `postgresql://` because Render injects the legacy prefix SQLAlchemy 1.4+ rejects
- `check_same_thread=False` only applies to SQLite

**App startup guard**: `app.py` raises `RuntimeError` immediately at import if `CRM_PASSWORD` is not set. This is intentional — surfaces missing secrets in Render deploy logs within seconds rather than failing silently at request time.

**Chat engine lazy-load**: `ChatEngine` (which imports `anthropic`) is instantiated on the first `/api/chat` request, not at startup. This lets the app start cleanly on Render before `ANTHROPIC_API_KEY` is configured.

---

## Development conventions

### Models (`models.py`)

- All models inherit from `Base = declarative_base()`
- All models have `created_at` and `updated_at` columns with `default=datetime.utcnow`
- All models implement `to_dict()` returning a plain `dict` with ISO-formatted date strings (`.isoformat()` for all date/datetime fields, `None` if null)
- FKs: `Column(Integer, ForeignKey('table.id'))` — no cascade, no `ondelete`
- Relationships defined on both sides; `foreign_keys=[...]` specified explicitly where ambiguous
- `init_db()` calls `Base.metadata.create_all(engine)` — creates missing tables, **does not add missing columns to existing tables**

### Routes (`app.py`)

- Every endpoint: `session = get_session()` → `try:` block → `finally: session.close()`
- Never reuse sessions across requests
- Error responses: `return bad(msg, code)` where `bad()` is a module-level helper returning `jsonify({'error': msg}), code`
- Date strings from request JSON: `_parse_date(s)` helper (returns `None` for empty/null strings)
- All routes are protected by the `@app.before_request` hook — no per-route auth decorator needed

### Frontend (`templates/index.html`)

**Tab system:**
- Nav buttons: `<button class="tab-btn" data-tab="name">` — tab name matches pane ID prefix
- Pane IDs: `id="pane-{name}"` — must match `data-tab` value
- `loadTab(tab)` dispatches to the corresponding `load{TabName}()` function — add new tabs here
- Tab is activated by adding/removing `active` class on both button and pane

**API calls:**
- All requests go through `async function api(method, path, body)` — throws `Error` on non-2xx responses
- Pattern: `const data = await api('GET', '/api/thing?' + params)` / `await api('POST', '/api/thing', {key: val})`

**Rendering pattern:**
- Load function: `async function loadFoo()` — fetches from API, calls `renderFoo(tbody, data)` or writes `innerHTML` directly
- Empty state: `<tr><td colspan="N" class="empty-state">No items found</td></tr>`
- Error state: same pattern with "Error loading X"
- User-provided strings: always wrap in `esc(str)` (HTML-escape helper defined in template)

**Mobile responsiveness:**
- `.hide-sm` class hides columns on screens ≤640px
- Applied to secondary columns (Org, Warmth, Next Task in Contacts; subject text in email cell)

**CSS conventions:**
- Always use CSS variables (`var(--accent)`, `var(--bg2)`, etc.) — never hardcode colors
- Primary accent (header, active tab underline, primary buttons, user chat bubbles): `--accent: #166534` (dark green, light mode) / `#4ade80` (dark mode)
- Warmth/status badge classes: `badge-hot`, `badge-warm`, `badge-cold` — also `badge-high`, `badge-medium`, `badge-low` for priority
- Status colors via JS helper functions: `statusColor(s)` for funders, `oppColor(s)` for opportunities, `emailStatus(c)` for contact email status
- Full color palette defined at top of `<style>` block; dark mode overrides in `@media (prefers-color-scheme: dark)`

**UTC datetime handling:**
- Python returns naive UTC datetimes as ISO strings without a timezone suffix
- JS appends `'Z'` before constructing `Date` objects so the browser treats them as UTC, not local time: `new Date(isoStr + 'Z')`

### Anthropic tool use (`chat.py`)

**Tool definition structure** — all tools in the `TOOLS` list follow this shape exactly:
```python
{
    "name": "snake_case_name",
    "description": "...",
    "input_schema": {
        "type": "object",
        "properties": {
            "field_name": {"type": "string", "description": "..."},
            "enum_field": {"type": "string", "enum": ["a", "b", "c"]},
        },
        "required": ["field_name"],   # omit if no required fields
    },
}
```

**Handler pattern** in `ChatEngine`:
- Method name: `_<tool_name>(self, inp: dict) -> dict`
- `inp` is the raw `input` dict from the tool use block
- Always return a plain dict (will be `json.dumps()`-ed as the tool result)
- Register in `_dispatch` handlers dict: `'tool_name': self._tool_name`
- If the tool modifies data, append to `self._changes`: `{'type': 'contact', 'action': 'created', 'id': c.id, 'name': c.name}`

**Tool loop** (`chat()` method): `while True` → create message → check `stop_reason` → if `tool_use`, collect all `tool_use` blocks, call `_dispatch` for each, append `tool_result` message → loop; if `end_turn`, extract text, save history, return `(text, changes)`.

**History management**: Rolling window of 20 turns saved in `chat_history.json` (gitignored). Beyond 20 turns, a stub summary message is prepended rather than truncating context hard.

---

## Key rules

### Database — additive only
**Never modify or drop existing columns.** The production database on Supabase has live data. All schema changes must be additive: add new nullable columns only. Write a migration SQL file in `crm/migrations/` (pattern: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`). The user runs it once in the Supabase SQL editor. `init_db()` / `create_all` does not apply column-level migrations automatically.

### gmail_sync.py and inbox_scan.py — hands off
**Never modify `gmail_sync.py` or `inbox_scan.py` unless explicitly asked.** These scripts run unattended in GitHub Actions every 6 hours against the production database. Silent bugs in them corrupt contact data or cause surprise API costs. If a change to them seems implied by a task, stop and ask first.

### UI — follow existing patterns
When adding or modifying the frontend:
- Use `var(--accent)` for primary interactive elements, not the hex value `#166534`
- Follow the `loadFoo()` / `renderFoo()` split for new tabs
- Wrap all user-provided strings in `esc()` — no raw interpolation into `innerHTML`
- Add `.hide-sm` to secondary columns that would crowd small screens
- Match the existing empty-state and error-state `<td>` patterns

### Anthropic tools — consistent structure
When adding a new chat tool to `chat.py`:
- Follow the exact tool definition shape above (name, description, input_schema with type/properties/required)
- Add a corresponding `_<name>` handler method that returns a dict
- Register it in `_dispatch`
- If it writes data, append to `self._changes` with the standard shape

---

## README auto-update

After completing any task that modifies the codebase — new feature, schema change, new endpoint, changed workflow — **update `crm/README.md` as the final step**. Integrate changes into the existing structure (update the relevant sections: schema, endpoints, UI tabs, workflows, env vars). Do not append a changelog section or a "what changed" block. The README should always reflect current state, not history.

---

## Environment variables

### Render.com (web app)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | Supabase `postgresql://` connection string |
| `CRM_PASSWORD` | Yes | — | HTTP Basic Auth password; app raises `RuntimeError` at startup if missing |
| `CRM_USERNAME` | No | `admin` | HTTP Basic Auth username |
| `SECRET_KEY` | No | `dev-secret-change-me` | Flask session secret; generate with `openssl rand -base64 32` |
| `ANTHROPIC_API_KEY` | For chat tab | — | Chat tab errors gracefully if missing; other tabs work without it |

### GitHub Actions secrets (Settings → Secrets → Actions)

| Secret | Used by | Notes |
|---|---|---|
| `SUPABASE_URL` | `gmail_sync.py`, `inbox_scan.py` | Same PostgreSQL URL as `DATABASE_URL` on Render |
| `GMAIL_TOKEN_JSON` | `gmail_sync.py`, `inbox_scan.py` | Base64-encoded `token.json`; run `auth_gmail.py` once locally to generate |
| `ANTHROPIC_API_KEY` | `inbox_scan.py` | Same key as on Render |

---

## Common tasks

### Adding a new database column

1. Add the `Column(...)` to the relevant model in `models.py`
2. Add the field to that model's `to_dict()` return dict (with `.isoformat()` for date/datetime, else raw value)
3. Write a migration file in `crm/migrations/`:
   ```sql
   ALTER TABLE table_name
     ADD COLUMN IF NOT EXISTS new_col VARCHAR(255);
   ```
4. Run the migration once in the Supabase SQL editor (Supabase → SQL Editor → paste and run)
5. `init_db()` / `create_all` handles SQLite locally — no local migration step needed

### Adding a new Anthropic chat tool

1. Add the tool definition to the `TOOLS` list in `chat.py`:
   ```python
   {
       "name": "my_tool",
       "description": "What it does and when to use it.",
       "input_schema": {
           "type": "object",
           "properties": {
               "param": {"type": "string", "description": "..."},
           },
           "required": ["param"],
       },
   }
   ```
2. Add a handler method to `ChatEngine`:
   ```python
   def _my_tool(self, inp):
       session = get_session()
       try:
           # ... do work ...
           self._changes.append({'type': 'thing', 'action': 'created', 'id': obj.id, 'name': obj.name})
           return {'result': obj.to_dict(), 'action': 'created'}
       finally:
           session.close()
   ```
3. Register in `_dispatch`:
   ```python
   handlers = {
       ...
       'my_tool': self._my_tool,
   }
   ```

### Adding a new UI tab

1. Add a nav button in `templates/index.html`:
   ```html
   <button class="tab-btn" data-tab="my-tab">My Tab</button>
   ```
2. Add a pane div (pane ID must be `pane-{data-tab value}`):
   ```html
   <div id="pane-my-tab" class="tab-pane">
     <!-- content -->
   </div>
   ```
3. Add a `loadMyTab()` function following the load/render split pattern
4. Register in `loadTab()`:
   ```js
   if (tab === 'my-tab') loadMyTab();
   ```
5. If the tab needs data, add a GET endpoint in `app.py` following the `get_session()` / `try` / `finally: session.close()` pattern
6. Use `var(--accent)`, `var(--bg2)`, etc. — no hardcoded colors
7. Update `crm/README.md`: add the tab to the "UI tabs" section

### Running the Gmail sync manually

**Via GitHub Actions** (runs against production Supabase — recommended):
- Go to Actions → Gmail Sync → Run workflow → Run workflow

**Locally against production Supabase**:
```bash
source crm/venv/bin/activate
SUPABASE_URL=postgresql://... GMAIL_TOKEN_JSON=<base64-token> python3 crm/gmail_sync.py
# then optionally:
SUPABASE_URL=postgresql://... GMAIL_TOKEN_JSON=<base64-token> ANTHROPIC_API_KEY=sk-ant-... python3 crm/inbox_scan.py
```
Both scripts validate env vars before doing anything and exit with a clear error message if they're missing.

**Re-generating the Gmail OAuth token** (when expired or revoked):
1. Ensure `crm/credentials.json` is present (download from Google Cloud Console)
2. `source crm/venv/bin/activate && python3 crm/auth_gmail.py`
3. Browser opens — sign in and grant access
4. Copy the printed base64 value and update the `GMAIL_TOKEN_JSON` secret in GitHub Settings → Secrets → Actions
