"""Contacts API — CRUD, dupe detection, related-resource sub-routes."""
from datetime import date, timedelta

import models


# ── GET /api/contacts ─────────────────────────────────────────────────────────

def test_list_empty(client, auth):
    resp = client.get("/api/contacts", headers=auth)
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_returns_all(client, auth, make_contact):
    make_contact(name="Zebra Last")
    make_contact(name="Alpha First")
    data = client.get("/api/contacts", headers=auth).get_json()
    assert len(data) == 2
    assert data[0]["name"] == "Alpha First"   # sorted by name


def test_filter_warmth(client, auth, make_contact):
    make_contact(name="Hot Person", warmth="hot")
    make_contact(name="Cold Person", warmth="cold")
    data = client.get("/api/contacts?warmth=hot", headers=auth).get_json()
    assert len(data) == 1
    assert data[0]["name"] == "Hot Person"


def test_filter_category(client, auth, make_contact):
    make_contact(name="Gov Person", category="government")
    make_contact(name="Other Person", category="other")
    data = client.get("/api/contacts?category=government", headers=auth).get_json()
    assert len(data) == 1
    assert data[0]["name"] == "Gov Person"


def test_filter_stale(client, auth, make_contact):
    stale_date = date.today() - timedelta(days=60)
    fresh_date = date.today() - timedelta(days=5)
    make_contact(name="Stale", last_contact_date=stale_date)
    make_contact(name="Fresh", last_contact_date=fresh_date)
    data = client.get("/api/contacts?stale_days=30", headers=auth).get_json()
    names = [d["name"] for d in data]
    assert "Stale" in names
    assert "Fresh" not in names


def test_list_includes_next_task(client, auth, make_contact, make_task):
    c = make_contact(name="Alice")
    make_task(title="Follow up", linked_contact_id=c.id, status="pending")
    data = client.get("/api/contacts", headers=auth).get_json()
    assert data[0]["next_task"] == "Follow up"


# ── POST /api/contacts ────────────────────────────────────────────────────────

def test_create_returns_201(client, auth):
    resp = client.post("/api/contacts", headers=auth,
                       json={"name": "New Person", "organization": "Test Org", "warmth": "warm"})
    assert resp.status_code == 201
    d = resp.get_json()
    assert d["name"] == "New Person"
    assert d["warmth"] == "warm"


def test_create_requires_name(client, auth):
    resp = client.post("/api/contacts", headers=auth, json={"organization": "Org"})
    assert resp.status_code == 400


def test_create_dupe_email_returns_409(client, auth, make_contact):
    make_contact(name="Existing", email="test@example.com")
    resp = client.post("/api/contacts", headers=auth,
                       json={"name": "New", "email": "test@example.com"})
    assert resp.status_code == 409
    d = resp.get_json()
    assert d["duplicate"] is True
    assert "existing_contact" in d


def test_create_dupe_email_case_insensitive(client, auth, make_contact):
    make_contact(name="Existing", email="Test@Example.com")
    resp = client.post("/api/contacts", headers=auth,
                       json={"name": "New", "email": "test@example.com"})
    assert resp.status_code == 409


def test_create_fuzzy_name_match_returns_200(client, auth, make_contact):
    make_contact(name="John Smith")
    resp = client.post("/api/contacts", headers=auth, json={"name": "John Smith"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert "possible_duplicates" in d
    assert len(d["possible_duplicates"]) >= 1


def test_create_unique_name_bypasses_fuzzy(client, auth, make_contact):
    make_contact(name="John Smith")
    resp = client.post("/api/contacts", headers=auth, json={"name": "Jane Doe"})
    assert resp.status_code == 201


def test_create_with_email_skips_fuzzy_check(client, auth, make_contact):
    make_contact(name="John Smith")
    resp = client.post("/api/contacts", headers=auth,
                       json={"name": "John Smith", "email": "other@example.com"})
    # email provided and unique → goes straight to creation
    assert resp.status_code == 201


def test_create_stores_last_contact_date(client, auth):
    resp = client.post("/api/contacts", headers=auth,
                       json={"name": "Dated", "last_contact_date": "2026-09-15"})
    assert resp.status_code == 201
    assert resp.get_json()["last_contact_date"] == "2026-09-15"


# ── PUT /api/contacts/<id> ────────────────────────────────────────────────────

def test_update_contact(client, auth, make_contact):
    c = make_contact(name="Old Name", warmth="cold")
    resp = client.put(f"/api/contacts/{c.id}", headers=auth,
                      json={"name": "New Name", "warmth": "hot"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["name"] == "New Name"
    assert d["warmth"] == "hot"


def test_update_contact_not_found(client, auth):
    resp = client.put("/api/contacts/99999", headers=auth, json={"name": "X"})
    assert resp.status_code == 404


# ── DELETE /api/contacts/<id> ─────────────────────────────────────────────────

def test_delete_contact(client, auth, make_contact):
    c = make_contact(name="To Delete")
    resp = client.delete(f"/api/contacts/{c.id}", headers=auth)
    assert resp.status_code == 200
    remaining = client.get("/api/contacts", headers=auth).get_json()
    assert not any(x["id"] == c.id for x in remaining)


def test_delete_contact_not_found(client, auth):
    resp = client.delete("/api/contacts/99999", headers=auth)
    assert resp.status_code == 404


# ── GET /api/contacts/<id>/interactions ──────────────────────────────────────

def test_interactions_empty(client, auth, make_contact):
    c = make_contact(name="Alice")
    resp = client.get(f"/api/contacts/{c.id}/interactions", headers=auth)
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_interactions_returns_data(client, auth, make_contact, make_interaction):
    c = make_contact(name="Alice")
    make_interaction(contact_id=c.id, type="meeting", notes="Great talk")
    data = client.get(f"/api/contacts/{c.id}/interactions", headers=auth).get_json()
    assert len(data) == 1
    assert data[0]["type"] == "meeting"
    assert data[0]["notes"] == "Great talk"


# ── GET /api/contacts/<id>/notes ─────────────────────────────────────────────

def test_notes_empty(client, auth, make_contact):
    c = make_contact(name="Bob")
    resp = client.get(f"/api/contacts/{c.id}/notes", headers=auth)
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_notes_returns_data(client, auth, make_contact, db):
    c = make_contact(name="Carol")
    n = models.ContactNote(contact_id=c.id, note="Important", source="manual")
    db.add(n)
    db.commit()
    data = client.get(f"/api/contacts/{c.id}/notes", headers=auth).get_json()
    assert len(data) == 1
    assert data[0]["note"] == "Important"


# ── GET /api/contacts/<id>/relationships ─────────────────────────────────────

def test_relationships_empty(client, auth, make_contact):
    c = make_contact(name="Dana")
    resp = client.get(f"/api/contacts/{c.id}/relationships", headers=auth)
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_relationships_returns_data(client, auth, make_contact, db):
    a = make_contact(name="Intro Maker")
    b = make_contact(name="Intro Receiver")
    rel = models.ContactRelationship(
        from_contact_id=a.id, to_contact_id=b.id,
        type="introduced_by", status="completed"
    )
    db.add(rel)
    db.commit()
    data = client.get(f"/api/contacts/{a.id}/relationships", headers=auth).get_json()
    assert len(data) >= 1
    assert any(r["type"] == "introduced_by" for r in data)


# ── auth guard ────────────────────────────────────────────────────────────────

def test_auth_required(client):
    assert client.get("/api/contacts").status_code == 401
