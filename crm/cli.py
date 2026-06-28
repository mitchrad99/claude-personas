"""
AAO CRM CLI

Usage:
  python cli.py chat                  # interactive AI chat session
  python cli.py contacts --stale 30  # contacts not touched in 30+ days
  python cli.py tasks --due 7        # tasks due in next 7 days
  python cli.py add-contact          # interactive prompt
  python cli.py summary              # dashboard stats
"""

import argparse
import sys
from datetime import date, timedelta

from models import init_db, get_session, Contact, Funder, Task


def cmd_chat():
    from chat import ChatEngine
    print("AAO CRM Chat — type 'exit' or Ctrl-C to quit.\n")
    engine = ChatEngine()
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if user_input.lower() in ('exit', 'quit', 'q'):
            break
        if not user_input:
            continue
        print("Thinking…")
        response, changes = engine.chat(user_input)
        print(f"\nAssistant: {response}")
        if changes:
            print("  Changes:", ", ".join(
                f"{c['action']} {c['type']}: {c.get('name') or c.get('title') or c.get('org','')}"
                for c in changes
            ))
        print()


def cmd_contacts(stale=None):
    session = get_session()
    try:
        q = session.query(Contact)
        if stale:
            cutoff = date.today() - timedelta(days=stale)
            q = q.filter((Contact.last_contact_date <= cutoff) | (Contact.last_contact_date == None))
        contacts = q.order_by(Contact.name).all()
        if not contacts:
            print("No contacts found.")
            return
        header = f"{'Name':<25} {'Org':<30} {'Warmth':<8} {'Last Contact':<14}"
        print(header)
        print('-' * len(header))
        for c in contacts:
            lcd = c.last_contact_date.isoformat() if c.last_contact_date else 'never'
            print(f"{c.name:<25} {(c.organization or ''):<30} {(c.warmth or ''):<8} {lcd:<14}")
    finally:
        session.close()


def cmd_tasks(due=None):
    session = get_session()
    try:
        q = session.query(Task).filter(Task.status == 'pending')
        if due:
            cutoff = date.today() + timedelta(days=due)
            q = q.filter(Task.due_date <= cutoff)
        tasks = q.order_by(Task.due_date).all()
        if not tasks:
            print("No tasks found.")
            return
        today = date.today()
        header = f"{'Title':<40} {'Due':<12} {'Priority':<10} {'Linked To'}"
        print(header)
        print('-' * 80)
        for t in tasks:
            due_str = t.due_date.isoformat() if t.due_date else '—'
            overdue = ' !' if (t.due_date and t.due_date < today) else ''
            linked = t.contact.name if t.contact else (t.funder.organization if t.funder else '—')
            print(f"{t.title:<40} {due_str + overdue:<12} {(t.priority or ''):<10} {linked}")
    finally:
        session.close()


def cmd_add_contact():
    print("Add Contact — press Enter to skip optional fields.\n")
    name = input("Name (required): ").strip()
    if not name:
        print("Name is required. Aborting.")
        return
    org = input("Organization: ").strip()
    title = input("Title/Role: ").strip()
    email = input("Email: ").strip()
    phone = input("Phone: ").strip()
    warmth = input("Warmth [cold/warm/hot] (default: cold): ").strip().lower() or 'cold'
    if warmth not in ('cold', 'warm', 'hot'):
        warmth = 'cold'
    category = input("Category [advocacy/funder/government/media/peer_org/dc_network/other] (default: other): ").strip().lower() or 'other'
    notes = input("Notes: ").strip()

    session = get_session()
    try:
        c = Contact(
            name=name,
            organization=org or None,
            title=title or None,
            email=email or None,
            phone=phone or None,
            warmth=warmth,
            category=category,
            notes=notes or None,
        )
        session.add(c)
        session.commit()
        print(f"\nCreated contact: {c.name} (id={c.id})")
    finally:
        session.close()


def cmd_summary():
    session = get_session()
    try:
        today = date.today()
        week_end = today + timedelta(days=7)
        stale_cutoff = today - timedelta(days=30)

        tasks_week = session.query(Task).filter(
            Task.status == 'pending', Task.due_date >= today, Task.due_date <= week_end
        ).count()
        overdue = session.query(Task).filter(
            Task.status == 'pending', Task.due_date < today
        ).count()
        stale = session.query(Contact).filter(
            (Contact.last_contact_date <= stale_cutoff) | (Contact.last_contact_date == None)
        ).count()
        total_contacts = session.query(Contact).count()
        total_pending = session.query(Task).filter(Task.status == 'pending').count()
        hot = session.query(Contact).filter(Contact.warmth == 'hot').count()
        warm = session.query(Contact).filter(Contact.warmth == 'warm').count()

        print(f"\n── AAO CRM Summary ({today}) ──────────────────")
        print(f"  Contacts total:       {total_contacts}  (hot: {hot}, warm: {warm})")
        print(f"  Tasks pending:        {total_pending}")
        print(f"  Tasks due this week:  {tasks_week}")
        print(f"  Overdue tasks:        {overdue}")
        print(f"  Stale contacts (30d): {stale}")
        print()
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description='AAO CRM CLI')
    sub = parser.add_subparsers(dest='command')

    sub.add_parser('chat', help='Open AI chat session')

    p_contacts = sub.add_parser('contacts', help='List contacts')
    p_contacts.add_argument('--stale', type=int, metavar='DAYS',
                            help='Only contacts not touched in N days')

    p_tasks = sub.add_parser('tasks', help='List pending tasks')
    p_tasks.add_argument('--due', type=int, metavar='DAYS',
                         help='Only tasks due in next N days')

    sub.add_parser('add-contact', help='Add a contact interactively')
    sub.add_parser('summary', help='Dashboard stats')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    init_db()

    if args.command == 'chat':
        cmd_chat()
    elif args.command == 'contacts':
        cmd_contacts(args.stale)
    elif args.command == 'tasks':
        cmd_tasks(args.due)
    elif args.command == 'add-contact':
        cmd_add_contact()
    elif args.command == 'summary':
        cmd_summary()


if __name__ == '__main__':
    main()
