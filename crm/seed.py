"""
Seed the AAO CRM with initial contacts and tasks.
Run once: python seed.py
"""

from datetime import date
from models import init_db, get_session, Contact, Task

CONTACTS = [
    dict(name='Veronica Nunamaker', organization='Ohio Chamber of Commerce',
         warmth='warm', category='advocacy'),
    dict(name='Beau Mills', organization='All Aboard NC',
         warmth='hot', category='peer_org'),
    dict(name='Mark Jeffreys', organization='Cincinnati City Council',
         warmth='warm', category='government'),
    dict(name='Joel Szabat', organization='Amtrak',
         warmth='cold', category='government',
         last_contact_date=date(2026, 4, 18)),
    dict(name='Sean Jeans-Gail', organization='Rail Passengers Association',
         warmth='warm', category='advocacy'),
]

TASKS = [
    dict(title='Send district membership breakdown to Veronica',
         due_date=date(2026, 7, 12), priority='high',
         contact_name='Veronica Nunamaker'),
    dict(title='Follow up with Beau on coalition call',
         due_date=date(2026, 7, 5), priority='high',
         contact_name='Beau Mills'),
    dict(title='Re-engage Joel Szabat post-summit',
         due_date=date(2026, 7, 28), priority='medium',
         contact_name='Joel Szabat'),
]


def seed():
    init_db()
    session = get_session()
    try:
        contact_map = {}

        for data in CONTACTS:
            existing = session.query(Contact).filter(Contact.name == data['name']).first()
            if existing:
                print(f"  skip (exists): {data['name']}")
                contact_map[data['name']] = existing
                continue
            c = Contact(**data)
            session.add(c)
            session.flush()
            contact_map[data['name']] = c
            print(f"  created contact: {data['name']}")

        for data in TASKS:
            contact_name = data.pop('contact_name', None)
            existing = session.query(Task).filter(Task.title == data['title']).first()
            if existing:
                print(f"  skip (exists): {data['title']}")
                data['contact_name'] = contact_name
                continue
            t = Task(**data, status='pending')
            if contact_name and contact_name in contact_map:
                t.linked_contact_id = contact_map[contact_name].id
            session.add(t)
            print(f"  created task: {data['title']}")

        session.commit()
        print("\nSeed complete.")
    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
        raise
    finally:
        session.close()


if __name__ == '__main__':
    seed()
