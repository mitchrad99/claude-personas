"""Model creation, relationships, and DB constraints for all 8 tables."""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

import models


# ── Contact ───────────────────────────────────────────────────────────────────

def test_contact_create(db):
    c = models.Contact(name="Jane Doe", organization="ACME", warmth="hot", category="advocacy")
    db.add(c)
    db.commit()
    assert c.id is not None
    assert c.warmth == "hot"
    assert c.created_at is not None


def test_contact_defaults(db):
    c = models.Contact(name="Minimal")
    db.add(c)
    db.commit()
    assert c.warmth == "cold"
    assert c.category == "other"


def test_contact_to_dict(db):
    c = models.Contact(name="Bob Jones", email="bob@example.com")
    db.add(c)
    db.commit()
    d = c.to_dict()
    assert d["name"] == "Bob Jones"
    assert d["email"] == "bob@example.com"
    assert "id" in d
    assert "created_at" in d


# ── Funder ────────────────────────────────────────────────────────────────────

def test_funder_create(db):
    f = models.Funder(organization="Grant Corp", status="outreach", ask_amount=50000)
    db.add(f)
    db.commit()
    assert f.id is not None
    assert f.status == "outreach"


def test_funder_to_dict(db):
    f = models.Funder(organization="Big Fund", ask_amount=100000, status="funded")
    db.add(f)
    db.commit()
    d = f.to_dict()
    assert d["organization"] == "Big Fund"
    assert d["ask_amount"] == 100000


def test_funder_linked_to_contact(db):
    c = models.Contact(name="Program Officer")
    db.add(c)
    db.commit()
    f = models.Funder(organization="Foundation X", program_officer_contact_id=c.id)
    db.add(f)
    db.commit()
    db.refresh(f)
    assert f.program_officer.name == "Program Officer"


# ── Task ─────────────────────────────────────────────────────────────────────

def test_task_create(db):
    t = models.Task(title="Call senator", priority="high", status="pending")
    db.add(t)
    db.commit()
    assert t.id is not None
    assert t.priority == "high"


def test_task_linked_to_contact(db):
    c = models.Contact(name="Sen. Smith")
    db.add(c)
    db.commit()
    t = models.Task(title="Follow up", linked_contact_id=c.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.contact.name == "Sen. Smith"
    assert t.to_dict()["contact_name"] == "Sen. Smith"


def test_task_to_dict_no_contact(db):
    t = models.Task(title="Standalone task")
    db.add(t)
    db.commit()
    d = t.to_dict()
    assert d["contact_name"] is None
    assert d["funder_name"] is None


# ── DCOrg ────────────────────────────────────────────────────────────────────

def test_dc_org_create(db):
    o = models.DCOrg(name="Rail Caucus", type="congressional", priority="high")
    db.add(o)
    db.commit()
    assert o.id is not None


def test_dc_org_key_contact(db):
    c = models.Contact(name="Liaison")
    db.add(c)
    db.commit()
    o = models.DCOrg(name="Org", key_contact_id=c.id)
    db.add(o)
    db.commit()
    db.refresh(o)
    assert o.key_contact.name == "Liaison"
    assert o.to_dict()["key_contact_name"] == "Liaison"


# ── Opportunity ───────────────────────────────────────────────────────────────

def test_opportunity_create(db):
    op = models.Opportunity(title="Policy Director", organization="DOT", type="job", status="applied")
    db.add(op)
    db.commit()
    assert op.id is not None
    d = op.to_dict()
    assert d["title"] == "Policy Director"
    assert d["status"] == "applied"


def test_opportunity_deadline_to_dict(db):
    op = models.Opportunity(title="Fellowship", deadline=date(2026, 10, 1))
    db.add(op)
    db.commit()
    assert op.to_dict()["deadline"] == "2026-10-01"


# ── Interaction ───────────────────────────────────────────────────────────────

def test_interaction_create(db):
    c = models.Contact(name="Meeting Partner")
    db.add(c)
    db.commit()
    i = models.Interaction(contact_id=c.id, date=date.today(), type="meeting", notes="Good chat")
    db.add(i)
    db.commit()
    assert i.id is not None
    assert i.follow_up_needed is False


def test_interaction_to_dict(db):
    c = models.Contact(name="Cindy")
    db.add(c)
    db.commit()
    i = models.Interaction(contact_id=c.id, date=date(2026, 9, 15), type="call", notes="Brief")
    db.add(i)
    db.commit()
    d = i.to_dict()
    assert d["date"] == "2026-09-15"
    assert d["contact_name"] == "Cindy"
    assert d["type"] == "call"


def test_interaction_follow_up_flag(db):
    c = models.Contact(name="Alice")
    db.add(c)
    db.commit()
    i = models.Interaction(contact_id=c.id, date=date.today(), type="meeting",
                            notes="x", follow_up_needed=True)
    db.add(i)
    db.commit()
    assert i.follow_up_needed is True


# ── ContactNote ───────────────────────────────────────────────────────────────

def test_contact_note_create(db):
    c = models.Contact(name="Noted Contact")
    db.add(c)
    db.commit()
    n = models.ContactNote(contact_id=c.id, note="Great ally", source="chat_debrief")
    db.add(n)
    db.commit()
    assert n.id is not None
    d = n.to_dict()
    assert d["note"] == "Great ally"
    assert d["source"] == "chat_debrief"
    assert d["contact_name"] == "Noted Contact"


def test_contact_note_default_source(db):
    c = models.Contact(name="X")
    db.add(c)
    db.commit()
    n = models.ContactNote(contact_id=c.id, note="Quick note")
    db.add(n)
    db.commit()
    assert n.source == "manual"


# ── ContactRelationship ───────────────────────────────────────────────────────

def test_contact_relationship_create(db):
    a = models.Contact(name="Alice")
    b = models.Contact(name="Bob")
    db.add_all([a, b])
    db.commit()
    r = models.ContactRelationship(
        from_contact_id=a.id, to_contact_id=b.id, type="introduced_by", status="completed"
    )
    db.add(r)
    db.commit()
    assert r.id is not None
    d = r.to_dict()
    assert d["type"] == "introduced_by"
    assert d["from_contact_name"] == "Alice"
    assert d["to_contact_name"] == "Bob"


def test_contact_relationship_self_ref_rejected(db):
    c = models.Contact(name="Solo")
    db.add(c)
    db.commit()
    r = models.ContactRelationship(
        from_contact_id=c.id, to_contact_id=c.id, type="peer", status="completed"
    )
    db.add(r)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_contact_relationship_unique_constraint(db):
    a = models.Contact(name="A")
    b = models.Contact(name="B")
    db.add_all([a, b])
    db.commit()
    r1 = models.ContactRelationship(
        from_contact_id=a.id, to_contact_id=b.id, type="peer", status="completed"
    )
    db.add(r1)
    db.commit()
    r2 = models.ContactRelationship(
        from_contact_id=a.id, to_contact_id=b.id, type="peer", status="pending"
    )
    db.add(r2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ── InboxRecommendation ───────────────────────────────────────────────────────

def test_inbox_recommendation_create(db):
    r = models.InboxRecommendation(
        sender_name="Unknown Sender",
        sender_email="unknown@example.com",
        recommendation_type="new_contact",
        recommendation_json='{"name": "Unknown Sender"}',
        status="pending",
    )
    db.add(r)
    db.commit()
    assert r.id is not None
    d = r.to_dict()
    assert d["status"] == "pending"
    assert d["recommendation_type"] == "new_contact"
    assert d["recommendation_json"] == '{"name": "Unknown Sender"}'


# ── TaskRecommendation ────────────────────────────────────────────────────────

def test_task_recommendation_create(db):
    tr = models.TaskRecommendation(
        title="Follow up with senator",
        priority="high",
        source="gmail",
        status="pending",
    )
    db.add(tr)
    db.commit()
    assert tr.id is not None
    d = tr.to_dict()
    assert d["title"] == "Follow up with senator"
    assert d["source"] == "gmail"
    assert d["status"] == "pending"


def test_task_recommendation_linked_contact(db):
    c = models.Contact(name="Senator")
    db.add(c)
    db.commit()
    tr = models.TaskRecommendation(title="Call back", linked_contact_id=c.id, source="slack")
    db.add(tr)
    db.commit()
    db.refresh(tr)
    assert tr.linked_contact.name == "Senator"
    assert tr.to_dict()["linked_contact_name"] == "Senator"


# ── relationship collections ──────────────────────────────────────────────────

def test_contact_collections(db):
    c = models.Contact(name="Hub")
    db.add(c)
    db.commit()
    db.add(models.Interaction(contact_id=c.id, date=date.today(), type="call", notes="x"))
    db.add(models.ContactNote(contact_id=c.id, note="y"))
    db.commit()
    db.refresh(c)
    assert len(c.interactions) == 1
    assert len(c.contact_notes) == 1
