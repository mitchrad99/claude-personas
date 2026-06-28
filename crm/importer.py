"""
CSV importer for AAO CRM.

Usage:
  python importer.py --contacts contacts.csv
  python importer.py --funders funders.csv
  python importer.py --tasks tasks.csv

Column mapping:
  contacts.csv : Name, Organization, Role, Email, Phone, Warmth, Last Contact, Notes
  funders.csv  : Organization, Type, Focus Areas, Program Officer, Ask Amount, Status, Deadline, Notes
  tasks.csv    : Title, Due Date, Priority, Status, Notes
"""

import csv
import argparse
from datetime import date

from models import init_db, get_session, Contact, Funder, Task


def _parse_date(s):
    if not s or not s.strip():
        return None
    s = s.strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%B %d, %Y'):
        try:
            from datetime import datetime
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _parse_int(s):
    if not s:
        return None
    cleaned = s.replace('$', '').replace(',', '').strip()
    try:
        return int(float(cleaned))
    except (ValueError, AttributeError):
        return None


def import_contacts(filename):
    session = get_session()
    created = updated = skipped = 0
    try:
        with open(filename, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get('Name') or '').strip()
                org = (row.get('Organization') or '').strip()
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
                c.title = (row.get('Role') or '').strip() or c.title
                c.email = (row.get('Email') or '').strip() or c.email
                c.phone = (row.get('Phone') or '').strip() or c.phone
                warmth = (row.get('Warmth') or '').strip().lower()
                if warmth in ('cold', 'warm', 'hot'):
                    c.warmth = warmth
                lcd = _parse_date(row.get('Last Contact'))
                if lcd:
                    c.last_contact_date = lcd
                notes = (row.get('Notes') or '').strip()
                if notes:
                    c.notes = notes

        session.commit()
        print(f'Contacts: {created} created, {updated} updated, {skipped} skipped.')
    except FileNotFoundError:
        print(f'File not found: {filename}')
    finally:
        session.close()


def import_funders(filename):
    session = get_session()
    created = updated = skipped = 0
    try:
        with open(filename, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                org = (row.get('Organization') or '').strip()
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
                ftype = (row.get('Type') or '').strip().lower()
                if ftype in ('foundation', 'corporate', 'government', 'individual'):
                    fu.type = ftype
                fu.focus_areas = (row.get('Focus Areas') or '').strip() or fu.focus_areas
                fu.program_officer_name = (row.get('Program Officer') or '').strip() or fu.program_officer_name
                amt = _parse_int(row.get('Ask Amount'))
                if amt is not None:
                    fu.ask_amount = amt
                status = (row.get('Status') or '').strip().lower().replace(' ', '_')
                valid_statuses = ('research', 'identified', 'outreach', 'meeting_scheduled',
                                  'proposal_submitted', 'funded', 'declined', 'dormant')
                if status in valid_statuses:
                    fu.status = status
                dl = _parse_date(row.get('Deadline'))
                if dl:
                    fu.deadline = dl
                notes = (row.get('Notes') or '').strip()
                if notes:
                    fu.notes = notes

        session.commit()
        print(f'Funders: {created} created, {updated} updated, {skipped} skipped.')
    except FileNotFoundError:
        print(f'File not found: {filename}')
    finally:
        session.close()


def import_tasks(filename):
    session = get_session()
    created = skipped = 0
    try:
        with open(filename, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = (row.get('Title') or '').strip()
                if not title:
                    skipped += 1
                    continue
                t = Task(
                    title=title,
                    due_date=_parse_date(row.get('Due Date')),
                    priority=(row.get('Priority') or 'medium').strip().lower(),
                    status=(row.get('Status') or 'pending').strip().lower(),
                    description=(row.get('Notes') or '').strip() or None,
                )
                session.add(t)
                created += 1

        session.commit()
        print(f'Tasks: {created} created, {skipped} skipped.')
    except FileNotFoundError:
        print(f'File not found: {filename}')
    finally:
        session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Import CSV data into AAO CRM')
    parser.add_argument('--contacts', metavar='FILE', help='contacts CSV file')
    parser.add_argument('--funders', metavar='FILE', help='funders CSV file')
    parser.add_argument('--tasks', metavar='FILE', help='tasks CSV file')
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
