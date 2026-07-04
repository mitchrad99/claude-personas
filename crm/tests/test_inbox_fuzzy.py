"""
Tests for fuzzy name matching (inbox_scan.py) and the link-to-existing route.

inbox_scan.py has module-level sys.exit() guards for SUPABASE_URL,
GMAIL_TOKEN_JSON, and ANTHROPIC_API_KEY. conftest.py satisfies the first and
third; we patch the second before importing the module under test.
"""
import importlib
import json
import sys
from unittest.mock import patch

import pytest

import models


# ── import inbox_scan with env guards satisfied ───────────────────────────────

@pytest.fixture(scope='module')
def inbox_scan():
    with patch.dict('os.environ', {'GMAIL_TOKEN_JSON': 'dGVzdA=='}):  # base64 'test'
        # Force a fresh import so patched env is visible to module top-level guard
        if 'inbox_scan' in sys.modules:
            del sys.modules['inbox_scan']
        import inbox_scan as _m
        yield _m
        # Clean up so other test modules get a fresh import if needed
        if 'inbox_scan' in sys.modules:
            del sys.modules['inbox_scan']


# ── fuzzy_name_match: true positives ─────────────────────────────────────────

def test_middle_initial_matches(inbox_scan):
    contacts = [{'id': 1, 'name': 'Derrick James'}]
    cid, score = inbox_scan.fuzzy_name_match('Derrick L. James', contacts)
    assert cid == 1
    assert score >= inbox_scan.FUZZY_NAME_THRESHOLD


def test_reversed_middle_initial(inbox_scan):
    """Contact has middle initial, sender doesn't."""
    contacts = [{'id': 2, 'name': 'Maria A. Rodriguez'}]
    cid, score = inbox_scan.fuzzy_name_match('Maria Rodriguez', contacts)
    assert cid == 2
    assert score >= inbox_scan.FUZZY_NAME_THRESHOLD


def test_exact_match(inbox_scan):
    contacts = [{'id': 3, 'name': 'Jane Doe'}]
    cid, score = inbox_scan.fuzzy_name_match('Jane Doe', contacts)
    assert cid == 3
    assert score == 1.0


# ── fuzzy_name_match: true negatives ─────────────────────────────────────────

def test_shared_first_name_different_last(inbox_scan):
    """Two people who share only a first name must not match."""
    contacts = [{'id': 4, 'name': 'John Davis'}]
    cid, score = inbox_scan.fuzzy_name_match('John Smith', contacts)
    assert cid is None


def test_shared_last_name_different_first(inbox_scan):
    contacts = [{'id': 5, 'name': 'Robert Johnson'}]
    cid, score = inbox_scan.fuzzy_name_match('Emily Johnson', contacts)
    assert cid is None


def test_empty_sender_name(inbox_scan):
    contacts = [{'id': 6, 'name': 'Anyone'}]
    cid, score = inbox_scan.fuzzy_name_match('', contacts)
    assert cid is None


def test_empty_contacts_list(inbox_scan):
    cid, score = inbox_scan.fuzzy_name_match('Someone Real', [])
    assert cid is None


def test_picks_best_match(inbox_scan):
    """With multiple contacts, returns the highest-scoring one."""
    contacts = [
        {'id': 10, 'name': 'Derrick James'},
        {'id': 11, 'name': 'Derek James'},   # typo variant, lower score
    ]
    cid, score = inbox_scan.fuzzy_name_match('Derrick James', contacts)
    assert cid == 10


# ── /api/inbox/<rid>/link route ───────────────────────────────────────────────

@pytest.fixture
def make_inbox_rec(db):
    def _make(**kwargs):
        kwargs.setdefault('sender_name', 'Test Sender')
        kwargs.setdefault('sender_email', 'test@example.com')
        kwargs.setdefault('recommendation_type', 'new_contact')
        kwargs.setdefault('recommendation_json', '{}')
        kwargs.setdefault('status', 'pending')
        r = models.InboxRecommendation(**kwargs)
        db.add(r)
        db.commit()
        db.refresh(r)
        return r
    return _make


def test_link_merges_fields_into_contact(client, auth, make_contact, make_inbox_rec):
    contact = make_contact(name='Derrick James', email=None, title=None, organization=None, notes=None)
    rec = make_inbox_rec(
        sender_name='Derrick L. James',
        possible_contact_id=contact.id,
        match_confidence=0.93,
        recommendation_json=json.dumps({
            'title': 'Policy Director',
            'organization': 'Rail Coalition',
            'email': 'derrick@rail.org',
            'notes': 'Met at DC summit',
        }),
    )

    resp = client.post(f'/api/inbox/{rec.id}/link', headers=auth, json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['contact']['title'] == 'Policy Director'
    assert data['contact']['organization'] == 'Rail Coalition'
    assert data['contact']['email'] == 'derrick@rail.org'
    assert data['contact']['notes'] == 'Met at DC summit'


def test_link_does_not_overwrite_existing_fields(client, auth, make_contact, make_inbox_rec):
    contact = make_contact(
        name='Derrick James',
        title='Senior Director',
        organization='Existing Org',
        email='existing@email.com',
        notes='original notes',
    )
    rec = make_inbox_rec(
        possible_contact_id=contact.id,
        match_confidence=0.93,
        recommendation_json=json.dumps({
            'title': 'New Title',
            'organization': 'New Org',
            'email': 'newemail@example.com',
            'notes': 'new notes',
        }),
    )

    resp = client.post(f'/api/inbox/{rec.id}/link', headers=auth, json={})
    assert resp.status_code == 200
    data = resp.get_json()['contact']
    assert data['title'] == 'Senior Director'
    assert data['organization'] == 'Existing Org'
    assert data['email'] == 'existing@email.com'
    assert data['notes'] == 'original notes'


def test_link_marks_recommendation_accepted(client, auth, make_contact, make_inbox_rec, db):
    contact = make_contact(name='Someone')
    rec = make_inbox_rec(possible_contact_id=contact.id, match_confidence=0.85)

    client.post(f'/api/inbox/{rec.id}/link', headers=auth, json={})

    db.expire(rec)
    assert rec.status == 'accepted'


def test_link_returns_404_for_unknown_rec(client, auth):
    resp = client.post('/api/inbox/99999/link', headers=auth, json={})
    assert resp.status_code == 404


def test_link_returns_400_when_no_possible_contact(client, auth, make_inbox_rec):
    rec = make_inbox_rec(possible_contact_id=None)
    resp = client.post(f'/api/inbox/{rec.id}/link', headers=auth, json={})
    assert resp.status_code == 400


def test_link_returns_404_when_already_accepted(client, auth, make_contact, make_inbox_rec):
    contact = make_contact(name='Already Done')
    rec = make_inbox_rec(possible_contact_id=contact.id, status='accepted')
    resp = client.post(f'/api/inbox/{rec.id}/link', headers=auth, json={})
    assert resp.status_code == 404
