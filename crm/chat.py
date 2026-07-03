import os
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic
from sqlalchemy import or_

from models import get_session, Contact, Funder, Task, DCOrg, Opportunity, Interaction, ContactNote, ContactRelationship

HISTORY_FILE = Path(__file__).parent / 'chat_history.json'
MAX_TURNS = 20   # number of recent turns to keep in context; older turns are summarized
_TEST_MODE = os.environ.get('TEST_MODE', '').lower() in ('1', 'true', 'yes')

def _build_system_prompt() -> str:
    today = date.today().isoformat()
    return (
        f"Today's date is {today}.\n\n"
        "You are Mitch Radakovich's CRM assistant — sharp, efficient, precise. Mitch is Board Chair "
        "of All Aboard Ohio (AAO), a 501(c)3 passenger rail advocacy org, preparing for a DC sabbatical "
        "(fall 2026) focused on fundraising, policy advocacy, and exploring a full-time advocacy career.\n\n"
        "**Core job**: After Mitch debriefs a past meeting or call, extract the contact, warmth signal, "
        "discussion topics, dollar amounts, and next steps — then log everything accurately. For every "
        "debrief, always call both `log_interaction` (touchpoint record) and `add_contact_note` "
        "(source=chat_debrief) to capture key points as a persistent note. "
        "A mention of a future or upcoming meeting is not a debrief — do not log it, do not ask for "
        "its goal or agenda, and do not offer to prep for it. Acknowledge only.\n\n"
        "**New contacts**: If a contact is mentioned by first name only and no existing record is found, "
        "ask for their last name before creating. When `create_or_update_contact` returns `{duplicate: true}`, "
        "use the existing contact's id — do not create a new record. When it returns "
        "`{possible_duplicates: [...]}`, pause and confirm with the user before proceeding.\n\n"
        "**Relationship language — two distinct cases**:\n"
        "  (1) Relationship between two other contacts (e.g. 'John introduced me to Sarah,' 'Dave and Lisa "
        "are peers at another rail org') → call `log_relationship`. This never modifies either contact's own fields.\n"
        "  (2) How a contact relates to Mitch specifically (e.g. 'Becky is mentoring me,' 'she's been "
        "advising me during the sabbatical') → do NOT call `log_relationship`. Reflect it as a `category` "
        "value on that contact's own record (e.g. category=mentor). If no existing category fits, flag it "
        "rather than guessing. Never touch `title`, `organization`, or other descriptive fields as a side "
        "effect of a relationship statement — only update those when Mitch explicitly states a factual "
        "correction about that field (e.g. 'her title is now X' or 'she works at Y now').\n"
        "For introductions: completed → type=introduced_by, status=completed; offered but not yet made → "
        "ask for the other person's name and org first, use type=wants_to_connect, status=pending, then "
        "create a follow-up task.\n\n"
        "**Tool use**: Collect all required fields in the same turn before calling a tool — never call "
        "a tool and then ask for missing info in a follow-up.\n\n"
        "**Written drafts**: Only produce emails or written content when explicitly asked — never offer proactively.\n\n"
        "**No re-runs**: The conversation history shows what was already logged. If a prior response "
        "confirms 'created task X' or 'logged interaction with Y', those records exist in the database — "
        "do not call the tool again. Only create a new record when the user's current message explicitly "
        "requests it or introduces genuinely new information not present in the prior turn.\n\n"
        "**After responding**: Confirm what was logged in one or two sentences, then stop — no offers, "
        "no suggestions, no questions. Informational statements are not requests for help. "
        "Only suggest or ask for anything additional when the user's message explicitly requests it.\n"
        "  • DON'T — User: 'I'm meeting with Ryan James Wednesday at 5pm.' "
        "→ You: 'What's the goal of the meeting?' or 'Want me to create a prep task?'\n"
        "  • DO — User: 'I'm meeting with Ryan James Wednesday at 5pm.' → You: 'Got it — let me know how it goes.'\n\n"
        f"**Dates**: All date fields must use YYYY-MM-DD (e.g. {today}). Resolve relative terms to actual dates before passing to tools.\n\n"
        "**Errors**: Describe what went wrong in plain English — never show raw error output."
    )

TOOLS = [
    {
        "name": "get_contacts",
        "description": (
            "Search contacts by name or organization, or list contacts not contacted in N days. "
            "Use this before create_or_update_contact to check if a contact already exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Name or org substring to search"},
                "warmth": {"type": "string", "enum": ["cold", "warm", "hot"]},
                "category": {"type": "string"},
                "stale_days": {"type": "integer", "description": "Contacts not touched in N days"},
            },
        },
    },
    {
        "name": "create_or_update_contact",
        "description": (
            "Upsert a contact record. If id is provided, updates that record. "
            "Otherwise searches by name+org; creates if not found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Contact ID to update"},
                "name": {"type": "string"},
                "organization": {"type": "string"},
                "title": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "warmth": {"type": "string", "enum": ["cold", "warm", "hot"]},
                "category": {"type": "string", "enum": [
                    "advocacy", "funder", "government", "media", "peer_org", "dc_network", "mentor", "other"
                ]},
                "last_contact_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "notes": {"type": "string"},
                "append_notes": {"type": "boolean", "description": "Append to existing notes instead of replacing"},
            },
        },
    },
    {
        "name": "create_task",
        "description": "Create a follow-up task, optionally linked to a contact or funder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "linked_contact_id": {"type": "integer"},
                "linked_funder_id": {"type": "integer"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_tasks",
        "description": "List tasks filtered by status, due date window, or priority.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "done"]},
                "due_within_days": {"type": "integer", "description": "Tasks due in next N days"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "overdue_only": {"type": "boolean"},
            },
        },
    },
    {
        "name": "create_or_update_funder",
        "description": "Upsert a funder record. Searches by organization name if no id given.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "organization": {"type": "string"},
                "type": {"type": "string", "enum": ["foundation", "corporate", "government", "individual"]},
                "focus_areas": {"type": "string"},
                "program_officer_name": {"type": "string"},
                "program_officer_contact_id": {"type": "integer"},
                "ask_amount": {"type": "integer"},
                "status": {"type": "string", "enum": [
                    "research", "identified", "outreach", "meeting_scheduled",
                    "proposal_submitted", "funded", "declined", "dormant"
                ]},
                "deadline": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "notes": {"type": "string"},
                "append_notes": {"type": "boolean"},
            },
        },
    },
    {
        "name": "get_funders",
        "description": "List funders, optionally filtered by status or minimum ask amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "min_ask": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_dc_orgs",
        "description": "List DC organizations by priority or type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "type": {"type": "string"},
            },
        },
    },
    {
        "name": "create_opportunity",
        "description": "Log a career or engagement opportunity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "organization": {"type": "string"},
                "type": {"type": "string", "enum": ["job", "fellowship", "board", "consulting", "speaking"]},
                "status": {"type": "string", "enum": [
                    "identified", "applied", "interviewing", "offer", "declined", "closed"
                ]},
                "deadline": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "salary_range": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "draft_email",
        "description": (
            "Look up a contact and return their info so Claude can draft a professional "
            "outreach or follow-up email. Returns context only — Claude writes the draft."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer"},
                "contact_name": {"type": "string"},
                "purpose": {"type": "string", "description": "e.g. 'follow up after meeting', 'intro ask'"},
            },
        },
    },
    {
        "name": "get_summary",
        "description": "Dashboard stats: tasks due this week, overdue tasks, stale contacts, hot funders.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "log_interaction",
        "description": (
            "Record a touchpoint with a contact (meeting, call, event, etc.). "
            "Always call this when Mitch debriefs a meeting or call, even if create_or_update_contact is also called."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID of the contact involved"},
                "date": {"type": "string", "description": "ISO date YYYY-MM-DD of the interaction"},
                "type": {"type": "string", "enum": ["meeting", "call", "event", "coffee", "text", "linkedin"]},
                "notes": {"type": "string", "description": "What was discussed, outcomes, impressions"},
                "location": {"type": "string", "description": "Where it happened (optional)"},
                "follow_up_needed": {"type": "boolean", "description": "True if Mitch needs to follow up"},
            },
            "required": ["contact_id", "date", "type", "notes"],
        },
    },
    {
        "name": "add_contact_note",
        "description": (
            "Append a timestamped note to a contact record. "
            "Use source=chat_debrief when logging meeting summaries via the chat interface. "
            "These notes are append-only and never overwrite existing notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID of the contact"},
                "note": {"type": "string", "description": "The note to append"},
            },
            "required": ["contact_id", "note"],
        },
    },
    {
        "name": "log_relationship",
        "description": (
            "Record a relationship edge between two contacts. "
            "Use type=introduced_by + status=completed when someone made an introduction that already happened. "
            "Use type=wants_to_connect + status=pending when someone offered or promised to make an introduction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_contact_id": {"type": "integer", "description": "Contact who initiated or made the introduction"},
                "to_contact_id": {"type": "integer", "description": "Contact who was introduced or connected to"},
                "type": {"type": "string", "enum": ["introduced_by", "wants_to_connect", "peer", "mentor", "referred_funder"]},
                "status": {"type": "string", "enum": ["completed", "pending"]},
                "notes": {"type": "string", "description": "Context about the relationship or introduction"},
            },
            "required": ["from_contact_id", "to_contact_id", "type", "status"],
        },
    },
]


def _parse_date(s: str) -> date:
    """Parse a date string flexibly, resolving 'today'/'yesterday' and common formats."""
    if not s:
        raise ValueError("Date string is empty")
    s = s.strip()
    lower = s.lower()
    if lower == 'today':
        return date.today()
    if lower == 'yesterday':
        return date.today() - timedelta(days=1)
    # Try ISO format (YYYY-MM-DD) first
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    # Try common English formats
    for fmt in ('%B %d, %Y', '%b %d, %Y', '%m/%d/%Y', '%m-%d-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date '{s}' — use YYYY-MM-DD format")


class ChatEngine:
    def __init__(self):
        if not _TEST_MODE:
            self.client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
        self.history = self._load_history()
        self._changes = []

    # ── history management ────────────────────────────────────────────────────

    def _load_history(self):
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_history(self):
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.history = self.history[-MAX_TURNS * 2:]
        with open(HISTORY_FILE, 'w') as f:
            json.dump(self.history, f, indent=2)

    def reset(self):
        self.history = []
        self._changes = []
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()

    def _build_messages(self):
        """
        Return the messages list to send to the API.
        If history exceeds MAX_TURNS, prepend a condensed summary block
        so the model has context without blowing up the token budget.
        """
        turns = self.history
        if len(turns) <= MAX_TURNS * 2:
            return list(turns)

        older = turns[:-(MAX_TURNS * 2)]
        recent = turns[-(MAX_TURNS * 2):]
        summary = (
            f"[Prior conversation summary: {len(older)} messages exchanged. "
            "Key context: Mitch has been logging meetings, tasks, and contacts via this assistant.]"
        )
        return [
            {"role": "user", "content": summary},
            {"role": "assistant", "content": "Understood — continuing from summary."},
            *recent,
        ]

    # ── main entry point ──────────────────────────────────────────────────────

    def chat(self, user_message: str):
        self._changes = []
        if _TEST_MODE:
            return (
                "[TEST MODE] Chat is mocked — no Anthropic API calls are made. "
                f'Your message: "{user_message}"\n\n'
                "In production this would be processed by Claude Sonnet 4.6 with full CRM tool access."
            ), []
        self.history.append({"role": "user", "content": user_message})
        messages = self._build_messages()

        while True:
            response = self.client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=4096,
                temperature=0,
                system=_build_system_prompt(),
                tools=TOOLS,
                messages=messages,
            )

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            if response.stop_reason == 'end_turn':
                text = next((b.text for b in assistant_content if hasattr(b, 'text')), '')
                self.history.append({"role": "assistant", "content": text})
                self._save_history()
                return text, self._changes

            if response.stop_reason == 'tool_use':
                tool_results = []
                for block in assistant_content:
                    if block.type == 'tool_use':
                        result = self._dispatch(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                text = next((b.text for b in assistant_content if hasattr(b, 'text')), '')
                self.history.append({"role": "assistant", "content": text})
                self._save_history()
                return text, self._changes

    # ── tool dispatcher ───────────────────────────────────────────────────────

    def _dispatch(self, name: str, inputs: dict):
        handlers = {
            'get_contacts': self._get_contacts,
            'create_or_update_contact': self._create_or_update_contact,
            'create_task': self._create_task,
            'get_tasks': self._get_tasks,
            'create_or_update_funder': self._create_or_update_funder,
            'get_funders': self._get_funders,
            'get_dc_orgs': self._get_dc_orgs,
            'create_opportunity': self._create_opportunity,
            'draft_email': self._draft_email,
            'get_summary': self._get_summary,
            'log_interaction': self._log_interaction,
            'add_contact_note': self._add_contact_note,
            'log_relationship': self._log_relationship,
        }
        handler = handlers.get(name)
        if not handler:
            return {'error': f'Unknown tool: {name}'}
        try:
            return handler(inputs)
        except Exception as e:
            return {'error': str(e)}

    # ── tool handlers ─────────────────────────────────────────────────────────

    def _get_contacts(self, inp):
        session = get_session()
        try:
            q = session.query(Contact)
            if inp.get('search'):
                term = f"%{inp['search']}%"
                q = q.filter((Contact.name.ilike(term)) | (Contact.organization.ilike(term)))
            if inp.get('warmth'):
                q = q.filter(Contact.warmth == inp['warmth'])
            if inp.get('category'):
                q = q.filter(Contact.category == inp['category'])
            if inp.get('stale_days'):
                cutoff = date.today() - timedelta(days=int(inp['stale_days']))
                q = q.filter((Contact.last_contact_date <= cutoff) | (Contact.last_contact_date == None))
            contacts = q.order_by(Contact.name).limit(50).all()
            return {'contacts': [c.to_dict() for c in contacts], 'count': len(contacts)}
        finally:
            session.close()

    def _create_or_update_contact(self, inp):
        session = get_session()
        try:
            if inp.get('id'):
                c = session.query(Contact).filter_by(id=inp['id']).first()
                if not c:
                    return {'error': f"Contact id={inp['id']} not found"}
                action = 'updated'
            else:
                name = inp.get('name', '')
                q = session.query(Contact).filter(Contact.name.ilike(name))
                if inp.get('organization'):
                    q = q.filter(Contact.organization.ilike(inp['organization']))
                c = q.first()
                if c:
                    action = 'updated'
                else:
                    if not name:
                        return {'error': 'name is required to create a contact'}

                    email = inp.get('email')

                    # Exact email match
                    if email:
                        existing = session.query(Contact).filter(Contact.email.ilike(email)).first()
                        if existing:
                            return {
                                'duplicate': True,
                                'existing_contact': existing.to_dict(),
                                'message': 'A contact with this email already exists',
                            }

                    # Fuzzy name match (only when no email provided)
                    if not email:
                        tokens = [t for t in name.split() if len(t) > 1]
                        if tokens:
                            candidates = (session.query(Contact)
                                          .filter(or_(*[Contact.name.ilike(f'%{t}%') for t in tokens]))
                                          .limit(5).all())
                            if candidates:
                                return {
                                    'possible_duplicates': [c.to_dict() for c in candidates],
                                    'message': 'Similar contacts found — confirm before creating',
                                }

                    c = Contact()
                    session.add(c)
                    action = 'created'

            for field in ['name', 'organization', 'title', 'email', 'phone', 'warmth', 'category']:
                if field in inp:
                    setattr(c, field, inp[field])

            if inp.get('last_contact_date'):
                c.last_contact_date = _parse_date(inp['last_contact_date'])

            if 'notes' in inp and inp['notes']:
                if inp.get('append_notes') and c.notes:
                    c.notes = c.notes.rstrip() + '\n\n' + inp['notes']
                else:
                    c.notes = inp['notes'] if not inp.get('append_notes') else (
                        ((c.notes or '').rstrip() + '\n\n' + inp['notes']).strip()
                    )

            c.updated_at = datetime.utcnow()
            session.commit()
            self._changes.append({'type': 'contact', 'action': action, 'id': c.id, 'name': c.name})
            return {'contact': c.to_dict(), 'action': action}
        finally:
            session.close()

    def _create_task(self, inp):
        session = get_session()
        try:
            t = Task(
                title=inp['title'],
                description=inp.get('description'),
                priority=inp.get('priority', 'medium'),
                status='pending',
                linked_contact_id=inp.get('linked_contact_id'),
                linked_funder_id=inp.get('linked_funder_id'),
            )
            if inp.get('due_date'):
                t.due_date = _parse_date(inp['due_date'])
            session.add(t)
            session.commit()
            self._changes.append({'type': 'task', 'action': 'created', 'id': t.id, 'title': t.title})
            return {'task': t.to_dict(), 'action': 'created'}
        finally:
            session.close()

    def _get_tasks(self, inp):
        session = get_session()
        try:
            status = inp.get('status', 'pending')
            q = session.query(Task).filter(Task.status == status)
            if inp.get('due_within_days'):
                cutoff = date.today() + timedelta(days=int(inp['due_within_days']))
                q = q.filter(Task.due_date <= cutoff)
            if inp.get('priority'):
                q = q.filter(Task.priority == inp['priority'])
            if inp.get('overdue_only'):
                q = q.filter(Task.due_date < date.today())
            tasks = q.order_by(Task.due_date).all()
            return {'tasks': [t.to_dict() for t in tasks], 'count': len(tasks)}
        finally:
            session.close()

    def _create_or_update_funder(self, inp):
        session = get_session()
        try:
            if inp.get('id'):
                f = session.query(Funder).filter_by(id=inp['id']).first()
                if not f:
                    return {'error': f"Funder id={inp['id']} not found"}
                action = 'updated'
            else:
                org = inp.get('organization', '')
                f = session.query(Funder).filter(Funder.organization.ilike(org)).first()
                if f:
                    action = 'updated'
                else:
                    if not org:
                        return {'error': 'organization is required'}
                    f = Funder()
                    session.add(f)
                    action = 'created'

            for field in ['organization', 'type', 'focus_areas', 'program_officer_name',
                           'program_officer_contact_id', 'ask_amount', 'status']:
                if field in inp:
                    setattr(f, field, inp[field])

            if inp.get('deadline'):
                f.deadline = _parse_date(inp['deadline'])

            if 'notes' in inp and inp['notes']:
                if inp.get('append_notes') and f.notes:
                    f.notes = f.notes.rstrip() + '\n\n' + inp['notes']
                else:
                    f.notes = inp['notes']

            f.updated_at = datetime.utcnow()
            session.commit()
            self._changes.append({'type': 'funder', 'action': action, 'id': f.id, 'org': f.organization})
            return {'funder': f.to_dict(), 'action': action}
        finally:
            session.close()

    def _get_funders(self, inp):
        session = get_session()
        try:
            q = session.query(Funder)
            if inp.get('status'):
                q = q.filter(Funder.status == inp['status'])
            if inp.get('min_ask'):
                q = q.filter(Funder.ask_amount >= inp['min_ask'])
            funders = q.order_by(Funder.organization).all()
            return {'funders': [f.to_dict() for f in funders], 'count': len(funders)}
        finally:
            session.close()

    def _get_dc_orgs(self, inp):
        session = get_session()
        try:
            q = session.query(DCOrg)
            if inp.get('priority'):
                q = q.filter(DCOrg.priority == inp['priority'])
            if inp.get('type'):
                q = q.filter(DCOrg.type == inp['type'])
            orgs = q.order_by(DCOrg.name).all()
            return {'dc_orgs': [o.to_dict() for o in orgs], 'count': len(orgs)}
        finally:
            session.close()

    def _create_opportunity(self, inp):
        session = get_session()
        try:
            o = Opportunity(
                title=inp['title'],
                organization=inp.get('organization'),
                type=inp.get('type'),
                status=inp.get('status', 'identified'),
                salary_range=inp.get('salary_range'),
                notes=inp.get('notes'),
            )
            if inp.get('deadline'):
                o.deadline = _parse_date(inp['deadline'])
            session.add(o)
            session.commit()
            self._changes.append({'type': 'opportunity', 'action': 'created', 'id': o.id, 'title': o.title})
            return {'opportunity': o.to_dict(), 'action': 'created'}
        finally:
            session.close()

    def _draft_email(self, inp):
        session = get_session()
        try:
            contact = None
            if inp.get('contact_id'):
                contact = session.query(Contact).filter_by(id=inp['contact_id']).first()
            elif inp.get('contact_name'):
                contact = session.query(Contact).filter(
                    Contact.name.ilike(f"%{inp['contact_name']}%")
                ).first()

            if contact:
                return {
                    'instruction': 'Draft a professional email using this contact info and purpose.',
                    'contact': contact.to_dict(),
                    'purpose': inp.get('purpose', 'follow up'),
                }
            return {
                'instruction': 'Draft a professional email. Contact not found in CRM.',
                'contact_name': inp.get('contact_name'),
                'purpose': inp.get('purpose', 'follow up'),
            }
        finally:
            session.close()

    def _get_summary(self, inp):
        session = get_session()
        try:
            today = date.today()
            week_end = today + timedelta(days=7)
            stale_cutoff = today - timedelta(days=30)

            tasks_week = session.query(Task).filter(
                Task.status == 'pending', Task.due_date >= today, Task.due_date <= week_end
            ).all()
            overdue = session.query(Task).filter(
                Task.status == 'pending', Task.due_date < today
            ).all()
            stale = session.query(Contact).filter(
                (Contact.last_contact_date <= stale_cutoff) | (Contact.last_contact_date == None)
            ).all()
            hot_funders = session.query(Funder).filter(
                Funder.status.in_(['outreach', 'meeting_scheduled', 'proposal_submitted'])
            ).all()

            return {
                'today': today.isoformat(),
                'tasks_due_this_week': [t.to_dict() for t in tasks_week],
                'overdue_tasks': [t.to_dict() for t in overdue],
                'stale_contacts_30d': [c.to_dict() for c in stale],
                'hot_funders': [f.to_dict() for f in hot_funders],
                'counts': {
                    'tasks_this_week': len(tasks_week),
                    'overdue': len(overdue),
                    'stale_contacts': len(stale),
                    'hot_funders': len(hot_funders),
                },
            }
        finally:
            session.close()

    def _log_interaction(self, inp):
        session = get_session()
        try:
            i = Interaction(
                contact_id=inp['contact_id'],
                date=_parse_date(inp['date']),
                type=inp['type'],
                notes=inp['notes'],
                location=inp.get('location'),
                follow_up_needed=inp.get('follow_up_needed', False),
            )
            session.add(i)
            session.commit()
            self._changes.append({'type': 'interaction', 'action': 'created', 'id': i.id, 'contact_id': i.contact_id})
            return {'interaction': i.to_dict(), 'action': 'created'}
        finally:
            session.close()

    def _add_contact_note(self, inp):
        session = get_session()
        try:
            n = ContactNote(
                contact_id=inp['contact_id'],
                note=inp['note'],
                source='chat_debrief',
            )
            session.add(n)
            session.commit()
            self._changes.append({'type': 'contact_note', 'action': 'created', 'id': n.id, 'contact_id': n.contact_id})
            return {'contact_note': n.to_dict(), 'action': 'created'}
        finally:
            session.close()

    def _log_relationship(self, inp):
        session = get_session()
        try:
            if inp['from_contact_id'] == inp['to_contact_id']:
                return {'error': 'from_contact_id and to_contact_id must be different'}
            r = ContactRelationship(
                from_contact_id=inp['from_contact_id'],
                to_contact_id=inp['to_contact_id'],
                type=inp['type'],
                status=inp['status'],
                notes=inp.get('notes'),
            )
            session.add(r)
            session.commit()
            self._changes.append({'type': 'contact_relationship', 'action': 'created', 'id': r.id})
            return {'contact_relationship': r.to_dict(), 'action': 'created'}
        finally:
            session.close()
