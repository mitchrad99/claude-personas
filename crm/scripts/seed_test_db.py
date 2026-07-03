#!/usr/bin/env python3
"""
Seed the local test database (crm/local_test.db) with realistic fake data.
Safe to run repeatedly — drops and recreates all tables on each run.

Usage (from the crm/ directory):
    TEST_MODE=true python scripts/seed_test_db.py
"""
import os
import sys
from datetime import date, timedelta

# Must be set before importing models so it picks up local_test.db
os.environ.setdefault('TEST_MODE', 'true')
# CRM_PASSWORD must be set; it's checked at import time by app.py but
# we only import models here so this is just a safeguard.
os.environ.setdefault('CRM_PASSWORD', 'test')

# Allow importing models from the crm/ parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (  # noqa: E402
    Base, engine, SessionLocal,
    Contact, Funder, Task, DCOrg, Opportunity,
    Interaction, ContactNote, ContactRelationship,
)

print("Dropping all tables...")
Base.metadata.drop_all(engine)
print("Creating schema...")
Base.metadata.create_all(engine)

session = SessionLocal()
today = date.today()

# ── Contacts ──────────────────────────────────────────────────────────────────
print("Seeding contacts...")
contacts = [
    Contact(
        name="Sarah Chen",
        organization="Rail Advocates Network",
        title="Executive Director",
        email="schen@railadvocates.org",
        phone="202-555-0101",
        warmth="hot",
        category="advocacy",
        last_contact_date=today - timedelta(days=5),
        notes="Met at Transportation for America conference. Strong ally on Amtrak funding.",
    ),
    Contact(
        name="Marcus Williams",
        organization="Senate Commerce Committee",
        title="Senior Policy Advisor",
        email="mwilliams@commerce.senate.gov",
        warmth="warm",
        category="government",
        last_contact_date=today - timedelta(days=18),
        notes="Key staffer for rail provisions. Intro'd by James Thornton at APTA Hill Day.",
    ),
    Contact(
        name="Diane Foster",
        organization="Midwest Rail Coalition",
        title="Policy Director",
        email="dfoster@midwestrail.org",
        warmth="warm",
        category="peer_org",
        last_contact_date=today - timedelta(days=45),
        notes="Peer org focused on Chicago Hub. Potential coalition partner.",
    ),
    Contact(
        name="Robert Okafor",
        organization="Ford Foundation",
        title="Program Officer, Democracy",
        email="rokafor@fordfoundation.org",
        phone="212-555-0142",
        warmth="cold",
        category="funder",
        last_contact_date=None,
        notes="Oversees civic infrastructure grants. Warm intro needed via Sarah Chen.",
    ),
    Contact(
        name="Lisa Park",
        organization="Politico",
        title="Transportation Reporter",
        email="lpark@politico.com",
        warmth="warm",
        category="media",
        last_contact_date=today - timedelta(days=12),
        notes="Covers Amtrak and surface transportation. Good relationship — responsive to background.",
    ),
    Contact(
        name="James Thornton",
        organization="APTA",
        title="Director of Government Affairs",
        email="jthornton@apta.com",
        warmth="hot",
        category="dc_network",
        last_contact_date=today - timedelta(days=3),
        notes="Strong connection at APTA. Can open doors on Capitol Hill. Offered Hill mtg with Sen. Wicker staff.",
    ),
]
session.add_all(contacts)
session.flush()

# ── Funders ───────────────────────────────────────────────────────────────────
print("Seeding funders...")
funders = [
    Funder(
        organization="Kresge Foundation",
        type="foundation",
        focus_areas="Urban mobility, climate, Detroit revitalization",
        program_officer_name="Angela Torres",
        program_officer_contact_id=None,
        ask_amount=150000,
        status="meeting_scheduled",
        deadline=today + timedelta(days=45),
        notes="RFP aligned well with DC advocacy work. Meeting scheduled for next month.",
    ),
    Funder(
        organization="Bloomberg Philanthropies",
        type="foundation",
        focus_areas="Cities, infrastructure, public health",
        program_officer_name="David Kim",
        ask_amount=200000,
        status="outreach",
        deadline=today + timedelta(days=90),
        notes="Initial email sent. Waiting on response from program officer.",
    ),
    Funder(
        organization="US DOT RAISE Grant",
        type="government",
        focus_areas="Multimodal transportation infrastructure",
        ask_amount=500000,
        status="research",
        deadline=today + timedelta(days=120),
        notes="Competitive federal grant. Need to assess AAO's eligibility.",
    ),
]
session.add_all(funders)
session.flush()

# ── Tasks ─────────────────────────────────────────────────────────────────────
print("Seeding tasks...")
tasks = [
    Task(
        title="Follow up with Marcus Williams re: Amtrak reauth markup timeline",
        description="He mentioned a markup is scheduled for late September. Nail down the date.",
        priority="high",
        status="pending",
        category="outreach",
        due_date=today + timedelta(days=3),
        linked_contact_id=contacts[1].id,
    ),
    Task(
        title="Submit Kresge LOI",
        description="Letter of inquiry for $150k advocacy grant. Angela Torres is the PO.",
        priority="high",
        status="pending",
        category="fundraising",
        due_date=today + timedelta(days=14),
        linked_funder_id=funders[0].id,
    ),
    Task(
        title="Ask Sarah Chen for intro to Robert Okafor (Ford Foundation)",
        description="Sarah offered to make the intro. Follow through.",
        priority="medium",
        status="pending",
        category="intro_followup",
        due_date=today + timedelta(days=7),
        linked_contact_id=contacts[0].id,
    ),
    Task(
        title="Review RAISE Grant eligibility criteria",
        priority="low",
        status="pending",
        category="fundraising",
        due_date=today + timedelta(days=30),
        linked_funder_id=funders[2].id,
    ),
    Task(
        title="Schedule coalition call with Diane Foster",
        priority="medium",
        status="pending",
        category="outreach",
        due_date=today + timedelta(days=10),
        linked_contact_id=contacts[2].id,
    ),
]
session.add_all(tasks)
session.flush()

# ── DC Orgs ───────────────────────────────────────────────────────────────────
print("Seeding DC orgs...")
dc_orgs = [
    DCOrg(
        name="Transportation for America",
        type="advocacy",
        priority="high",
        key_contact_id=contacts[0].id,
        notes="Strong ally. Co-sign opportunities on Amtrak reauthorization.",
    ),
    DCOrg(
        name="APTA",
        type="advocacy",
        priority="high",
        key_contact_id=contacts[5].id,
        notes="National transit trade association. Good lobbying muscle on Capitol Hill.",
    ),
    DCOrg(
        name="Senate Commerce Committee",
        type="congressional",
        priority="high",
        key_contact_id=contacts[1].id,
        notes="Jurisdiction over Amtrak and intercity rail. Key committee for reauthorization.",
    ),
]
session.add_all(dc_orgs)
session.flush()

# ── Opportunities ─────────────────────────────────────────────────────────────
print("Seeding opportunities...")
opps = [
    Opportunity(
        title="Policy Director, National Rail Coalition",
        organization="National Rail Coalition",
        type="job",
        status="identified",
        salary_range="$110,000–$130,000",
        deadline=today + timedelta(days=60),
        notes="Would be a natural fit post-sabbatical. Watch for posting.",
    ),
]
session.add_all(opps)
session.flush()

# ── Interactions ──────────────────────────────────────────────────────────────
print("Seeding interactions...")
interactions = [
    Interaction(
        contact_id=contacts[0].id,
        date=today - timedelta(days=5),
        type="meeting",
        location="Rail Advocates Network office, DC",
        notes="Caught up on coalition priorities for fall. Sarah offered intro to Robert Okafor at Ford.",
        follow_up_needed=True,
    ),
    Interaction(
        contact_id=contacts[5].id,
        date=today - timedelta(days=3),
        type="coffee",
        location="Compass Coffee, Capitol Hill",
        notes="James offered to arrange Hill meeting with Sen. Wicker's staff. Very positive.",
        follow_up_needed=True,
    ),
    Interaction(
        contact_id=contacts[4].id,
        date=today - timedelta(days=12),
        type="call",
        notes="Lisa asked for background on AAO's stance on the NEC Infrastructure bill. Provided talking points.",
        follow_up_needed=False,
    ),
]
session.add_all(interactions)
session.flush()

# ── Contact Notes ─────────────────────────────────────────────────────────────
print("Seeding contact notes...")
notes = [
    ContactNote(
        contact_id=contacts[0].id,
        note="Best connector in the rail advocacy space. Keep relationship warm — she opens doors.",
        source="manual",
    ),
    ContactNote(
        contact_id=contacts[1].id,
        note="Policy-focused, not political. Lead with data and specific bill language.",
        source="chat_debrief",
    ),
    ContactNote(
        contact_id=contacts[5].id,
        note="Direct relationships with multiple committee chairs. High-priority relationship to maintain.",
        source="manual",
    ),
]
session.add_all(notes)
session.flush()

# ── Contact Relationships ─────────────────────────────────────────────────────
print("Seeding relationships...")
rels = [
    ContactRelationship(
        from_contact_id=contacts[0].id,   # Sarah Chen
        to_contact_id=contacts[3].id,     # Robert Okafor
        type="wants_to_connect",
        status="pending",
        notes="Sarah offered to intro Mitch to Robert Okafor at Ford Foundation.",
    ),
    ContactRelationship(
        from_contact_id=contacts[5].id,   # James Thornton
        to_contact_id=contacts[1].id,     # Marcus Williams
        type="introduced_by",
        status="completed",
        notes="James introduced Mitch to Marcus at APTA Hill Day.",
    ),
]
session.add_all(rels)
session.commit()
session.close()

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'local_test.db')
print(f"\nDone. Seeded into {db_path}:")
print(f"  {len(contacts)} contacts")
print(f"  {len(funders)} funders")
print(f"  {len(tasks)} tasks")
print(f"  {len(dc_orgs)} DC orgs")
print(f"  {len(opps)} opportunities")
print(f"  {len(interactions)} interactions")
print(f"  {len(notes)} contact notes")
print(f"  {len(rels)} contact relationships")
print(f"\nRun the app: TEST_MODE=true CRM_PASSWORD=<password> python app.py")
