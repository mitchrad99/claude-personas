# Claude Persona CLI

Multi-persona Claude chat with persistent memory — built for AAO workflows.

## Setup

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python chat.py
```

## How it works

Each persona lives in `personas/` as a JSON file containing:
- `system_prompt` — the persona's role, context, and instructions
- `history` — recent conversation turns (last 20 kept in live context)
- `memory_summary` — a compressed summary of older conversations

When history exceeds 15 turns, older messages are automatically summarized
and stored as `memory_summary`. This means the persona "remembers" across
sessions without blowing through the context window.

## Commands

| Command    | What it does                          |
|------------|---------------------------------------|
| `/switch`  | Save current persona, pick a new one  |
| `/history` | Show the last 10 messages             |
| `/clear`   | Wipe history and memory for this persona |
| `/save`    | Manually save (auto-saves after every turn) |
| `/quit`    | Save and exit                         |

## Adding a new persona

Create a new file in `personas/` — e.g. `personas/sabbbatical.json`:

```json
{
  "name": "DC Sabbatical Strategy",
  "description": "Contact strategy, career pivot, DC network",
  "system_prompt": "You are a strategic advisor helping Mitch...",
  "history": [],
  "memory_summary": ""
}
```

The app picks it up automatically — no code changes needed.

## Upgrading to a Python script that pulls live CRM data

In `chat.py`, modify `build_messages()` to inject live context:

```python
import gspread

def get_crm_context() -> str:
    gc = gspread.service_account(filename="service_account.json")
    sheet = gc.open_by_key("YOUR_SHEET_ID").worksheet("Contacts")
    rows = sheet.get_all_records()
    stale = [r for r in rows if r.get("Days Since Contact", 0) > 60]
    return f"Stale contacts needing outreach: {stale[:5]}"

# Then prepend to system_prompt in chat_turn():
live_context = get_crm_context()
full_system = persona["system_prompt"] + "\n\nLIVE CRM DATA:\n" + live_context
```

## File structure

```
claude_personas/
├── chat.py              # main app
├── README.md
└── personas/
    ├── fundraising.json  # AAO Fundraising persona
    ├── policy.json       # Policy Research persona
    └── linkedin.json     # LinkedIn Content persona
```
