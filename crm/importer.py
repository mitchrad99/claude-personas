"""
CSV importer for AAO CRM — Google Sheets export format (comma-separated).

Usage:
  python importer.py --contacts contacts.csv
  python importer.py --funders  funders.csv
  python importer.py --tasks    tasks.csv

Contacts columns used:
  contact_id, name, organization, title, category,
  relationship_strength (1=Strong,5=Weak), how_connected, email, key_ask,
  date_last_contacted, next_step, next_step_due_date, network_context

Funders columns used:
  funder_id, name, type, category, geography, typical_grant ($),
  contact_name, status, next_step_due_date, notes

Tasks columns used:
  task, due_date, status, linked_contact_id, linked_funder_id,
  notes, goal_area, success_metric
"""

import csv
import json
import re
import argparse
from datetime import datetime
from pathlib import Path

from models import init_db, get_session, Contact, Funder, Task

BASE_DIR = Path(__file__).parent
ID_MAP_FILE = BASE_DIR / 'import_id_map.json'

# Common mojibake: UTF-8 em-dash and smart quotes read as cp1252
_MOJIBAKE = [
    ('‚Äî', '—'),  # ‚Äî → —
    ('‚Äù', '”'),  # ‚Äù → "
    ('‚Äú', '“'),  # ‚Äú → "
    ('‚Äô', '’'),  # ‚Äô → '
]


def _fix(s):
    if not s:
        return s
    for bad, good in _MOJIBAKE:
        s = s.replace(bad, good)
    return s.strip()


def _normalize_key(s):
    """Strip embedded newlines and trailing unit suffixes from CSV column headers."""
    if not s:
        return ''
    s = s.split('\n')[0].strip()              # drop subtitle after newline
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()  # drop trailing (...)
    return s


def _parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d', '%B %d, %Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _parse_int(s):
    if not s:
        return None
    cleaned = re.sub(r'[^\d.]', '', s.strip())
    try:
        return int(float(cleaned)) if cleaned else None
    except ValueError:
        return None


def _warmth(s):
    try:
        v = int(s.strip())
    except (ValueError, AttributeError, TypeError):
        return 'cold'
    if v <= 2:
        return 'hot'
    if v == 3:
        return 'warm'
    return 'cold'


_CONTACT_CATEGORY = {
    'policy/advocacy': 'advocacy',
    'media/influencer': 'media',
    'career/recruiter': 'other',
    'dc network': 'dc_network',
}

_FUNDER_STATUS = {
    'research': 'research',
    'active': 'outreach',
    'prospecting': 'identified',
}

_TASK_STATUS = {
    'complete': 'done',
    'not started': 'pending',
    'in progress': 'pending',
}


def _concat_notes(*parts):
    joined = ' | '.join(p for p in [_fix(p) for p in parts] if p)
    return joined or None


def _load_id_map():
    if ID_MAP_FILE.exists():
        return json.loads(ID_MAP_FILE.read_text())
    return {'contacts': {}, 'funders': {}}


def _save_id_map(id_map):
    ID_MAP_FILE.write_text(json.dumps(id_map, indent=2))


def _normalized_reader(f):
    reader = csv.DictReader(f)
    _ = reader.fieldnames  # trigger header parse
    reader.fieldnames = [_normalize_key(k) if k else '' for k in (reader.fieldnames or [])]
    return reader


def import_contacts(filename):
    session = get_session()
    id_map = _load_id_map()
    created = updated = skipped = tasks_created = 0

    try:
        with open(filename, newline='', encoding='utf-8-sig', errors='replace') as f:
            reader = _normalized_reader(f)
            for row in reader:
                name = _fix(row.get('name'))
                org = _fix(row.get('organization'))
                if not name:
                    skipped += 1
                    continue

                existing = (session.query(Contact)
                            .filter(Contact.name.ilike(name),
                                    Contact.organization.ilike(org) if org else True)
                            .first())
                if existing:
                    c = existing
                    updated += 1
                else:
                    c = Contact()
                    session.add(c)
                    created += 1

                c.name = name
                c.organization = org or c.organization
                c.title = _fix(row.get('title')) or c.title
                c.email = _fix(row.get('email')) or c.email

                strength = (row.get('relationship_strength') or '').strip()
                if strength:
                    c.warmth = _warmth(strength)

                raw_cat = (row.get('category') or '').strip().lower()
                c.category = _CONTACT_CATEGORY.get(raw_cat, 'other')

                lcd = _parse_date(row.get('date_last_contacted'))
                if lcd:
                    c.last_contact_date = lcd

                notes = _concat_notes(
                    row.get('network_context'),
                    row.get('how_connected'),
                    row.get('key_ask'),
                )
                if notes:
                    c.notes = notes

                session.flush()

                csv_id = (row.get('contact_id') or '').strip()
                if csv_id:
                    id_map['contacts'][csv_id] = c.id

                # Create a pending task from next_step if present
                next_step = _fix(row.get('next_step'))
                if next_step:
                    due = _parse_date(row.get('next_step_due_date'))
                    existing_task = (session.query(Task)
                                     .filter(Task.title == next_step,
                                             Task.linked_contact_id == c.id)
                                     .first())
                    if not existing_task:
                        t = Task(
                            title=next_step,
                            due_date=due,
                            priority='medium',
                            status='pending',
                            linked_contact_id=c.id,
                        )
                        session.add(t)
                        tasks_created += 1

        session.commit()
        _save_id_map(id_map)
        print(f'Contacts: {created} imported, {updated} updated, {skipped} skipped.')
        if tasks_created:
            print(f'Tasks (from next_step): {tasks_created} created.')

    except FileNotFoundError:
        print(f'File not found: {filename}')
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def import_funders(filename):
    session = get_session()
    id_map = _load_id_map()
    created = updated = skipped = 0

    try:
        with open(filename, newline='', encoding='utf-8-sig', errors='replace') as f:
            reader = _normalized_reader(f)
            for row in reader:
                org = _fix(row.get('name'))
                if not org:
                    skipped += 1
                    continue

                existing = session.query(Funder).filter(Funder.organization.ilike(org)).first()
                if existing:
                    fu = existing
                    updated += 1
                else:
                    fu = Funder()
                    session.add(fu)
                    created += 1

                fu.organization = org

                raw_type = (row.get('type') or '').strip().lower()
                fu.type = {'foundation': 'foundation', 'corporate': 'corporate'}.get(raw_type, 'foundation')

                fu.program_officer_name = _fix(row.get('contact_name')) or fu.program_officer_name

                amt = _parse_int(row.get('typical_grant'))
                if amt is not None:
                    fu.ask_amount = amt

                raw_status = (row.get('status') or '').strip().lower()
                fu.status = _FUNDER_STATUS.get(raw_status, 'research')

                dl = _parse_date(row.get('next_step_due_date'))
                if dl:
                    fu.deadline = dl

                notes = _concat_notes(
                    row.get('notes'),
                    row.get('category'),
                    row.get('geography'),
                )
                if notes:
                    fu.notes = notes

                session.flush()

                csv_id = (row.get('funder_id') or '').strip()
                if csv_id:
                    id_map['funders'][csv_id] = fu.id

        session.commit()
        _save_id_map(id_map)
        print(f'Funders: {created} imported, {updated} updated, {skipped} skipped.')

    except FileNotFoundError:
        print(f'File not found: {filename}')
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def import_tasks(filename):
    session = get_session()
    id_map = _load_id_map()
    created = updated = skipped = 0

    try:
        with open(filename, newline='', encoding='utf-8-sig', errors='replace') as f:
            reader = _normalized_reader(f)
            for row in reader:
                title = _fix(row.get('task'))
                if not title:
                    skipped += 1
                    continue

                due = _parse_date(row.get('due_date'))
                # due_date may contain status text like "Complete" — _parse_date returns None safely

                raw_status = (row.get('status') or '').strip().lower()
                status = _TASK_STATUS.get(raw_status, 'pending')

                description = _concat_notes(
                    row.get('notes'),
                    row.get('goal_area'),
                    row.get('success_metric'),
                )

                linked_contact_id = None
                raw_cid = (row.get('linked_contact_id') or '').strip()
                if raw_cid:
                    linked_contact_id = id_map['contacts'].get(raw_cid)

                linked_funder_id = None
                raw_fid = (row.get('linked_funder_id') or '').strip()
                if raw_fid:
                    linked_funder_id = id_map['funders'].get(raw_fid)

                existing = session.query(Task).filter(Task.title == title).first()
                if existing:
                    t = existing
                    updated += 1
                else:
                    t = Task(title=title)
                    session.add(t)
                    created += 1

                t.due_date = due
                t.status = status
                if description:
                    t.description = description
                if not t.priority:
                    t.priority = 'medium'
                if linked_contact_id:
                    t.linked_contact_id = linked_contact_id
                if linked_funder_id:
                    t.linked_funder_id = linked_funder_id

        session.commit()
        print(f'Tasks: {created} imported, {updated} updated, {skipped} skipped.')

    except FileNotFoundError:
        print(f'File not found: {filename}')
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Import CSV data into AAO CRM')
    parser.add_argument('--contacts', metavar='FILE', help='contacts CSV')
    parser.add_argument('--funders', metavar='FILE', help='funders CSV')
    parser.add_argument('--tasks', metavar='FILE', help='tasks CSV')
    args = parser.parse_args()

    if not any([args.contacts, args.funders, args.tasks]):
        parser.print_help()
    else:
        init_db()
        if args.contacts:
            import_contacts(args.contacts)
        if args.funders:
            import_funders(args.funders)
        if args.tasks:
            import_tasks(args.tasks)
