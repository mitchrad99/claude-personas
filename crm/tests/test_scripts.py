"""
Tests for scripts/merge_contacts.py and scripts/match_slack_users.py.

Both scripts are imported by name because conftest.py adds crm/scripts/ to
sys.path. Their module-level env-var guards are satisfied by the env defaults
set in conftest.py (SUPABASE_URL, SLACK_BOT_TOKEN).

merge_contacts: tested by calling its pure functions with the test db session
directly — no subprocess needed.

match_slack_users: only fetch_all_slack_users() is tested (main() calls
sys.exit on any error, making it unsuitable for unit testing without full
integration setup).
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import models
import merge_contacts
import match_slack_users


# ── helpers ───────────────────────────────────────────────────────────────────

def _set_ids(keep_id, dupe_id):
    merge_contacts.KEEP_ID = keep_id
    merge_contacts.DUPE_ID = dupe_id


# ── merge_contacts: backfill_fields ──────────────────────────────────────────

def test_backfill_copies_null_fields(db):
    keep = models.Contact(name="Keep", warmth="hot")
    dupe = models.Contact(name="Dupe", email="dupe@example.com", notes="has notes")
    db.add_all([keep, dupe])
    db.commit()

    _set_ids(keep.id, dupe.id)
    copied = merge_contacts.backfill_fields(db, keep, dupe)

    assert copied.get("email") == "dupe@example.com"
    assert copied.get("notes") == "has notes"
    assert keep.email == "dupe@example.com"
    assert keep.notes == "has notes"


def test_backfill_does_not_overwrite_existing(db):
    keep = models.Contact(name="Keep", email="keep@example.com", notes="original")
    dupe = models.Contact(name="Dupe", email="dupe@example.com", notes="dupe notes")
    db.add_all([keep, dupe])
    db.commit()

    _set_ids(keep.id, dupe.id)
    copied = merge_contacts.backfill_fields(db, keep, dupe)

    assert "email" not in copied
    assert "notes" not in copied
    assert keep.email == "keep@example.com"
    assert keep.notes == "original"


def test_backfill_partial_copy(db):
    keep = models.Contact(name="Keep", email="keep@example.com")   # has email, no notes
    dupe = models.Contact(name="Dupe", email="dupe@example.com", notes="dupe notes")
    db.add_all([keep, dupe])
    db.commit()

    _set_ids(keep.id, dupe.id)
    copied = merge_contacts.backfill_fields(db, keep, dupe)

    assert "email" not in copied          # keep already had email
    assert copied.get("notes") == "dupe notes"  # copied from dupe
    assert keep.notes == "dupe notes"


# ── merge_contacts: count_refs ────────────────────────────────────────────────

def test_count_refs_tasks_and_interactions(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    db.add_all([keep, dupe])
    db.commit()

    db.add(models.Task(title="T", linked_contact_id=dupe.id))
    db.add(models.Interaction(contact_id=dupe.id, date=date.today(), type="call", notes="x"))
    db.commit()

    _set_ids(keep.id, dupe.id)
    refs = merge_contacts.count_refs(db)

    assert refs["tasks"] == 1
    assert refs["interactions"] == 1
    assert refs["contact_notes"] == 0
    assert refs["dc_orgs"] == 0
    assert refs["funders"] == 0


def test_count_refs_notes(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    db.add_all([keep, dupe])
    db.commit()

    db.add(models.ContactNote(contact_id=dupe.id, note="note on dupe"))
    db.commit()

    _set_ids(keep.id, dupe.id)
    refs = merge_contacts.count_refs(db)
    assert refs["contact_notes"] == 1


# ── merge_contacts: merge ────────────────────────────────────────────────────

def test_merge_repoints_tasks(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    db.add_all([keep, dupe])
    db.commit()

    t = models.Task(title="T", linked_contact_id=dupe.id)
    db.add(t)
    db.commit()

    _set_ids(keep.id, dupe.id)
    merge_contacts.merge(db)
    db.commit()

    db.expire(t)
    assert t.linked_contact_id == keep.id
    assert db.query(models.Contact).filter_by(id=dupe.id).first() is None


def test_merge_repoints_interactions(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    db.add_all([keep, dupe])
    db.commit()

    i = models.Interaction(contact_id=dupe.id, date=date.today(), type="meeting", notes="x")
    db.add(i)
    db.commit()

    _set_ids(keep.id, dupe.id)
    merge_contacts.merge(db)
    db.commit()

    db.expire(i)
    assert i.contact_id == keep.id


def test_merge_repoints_contact_notes(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    db.add_all([keep, dupe])
    db.commit()

    n = models.ContactNote(contact_id=dupe.id, note="old note")
    db.add(n)
    db.commit()

    _set_ids(keep.id, dupe.id)
    merge_contacts.merge(db)
    db.commit()

    db.expire(n)
    assert n.contact_id == keep.id


def test_merge_deletes_dupe(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    db.add_all([keep, dupe])
    db.commit()
    dupe_id = dupe.id

    _set_ids(keep.id, dupe_id)
    merge_contacts.merge(db)
    db.commit()

    assert db.query(models.Contact).filter_by(id=dupe_id).first() is None
    assert db.query(models.Contact).filter_by(id=keep.id).first() is not None


def test_merge_drops_would_be_self_ref_relationship(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    db.add_all([keep, dupe])
    db.commit()

    # dupe → keep: after merge this would be keep → keep (self-ref — must be dropped)
    r = models.ContactRelationship(
        from_contact_id=dupe.id, to_contact_id=keep.id,
        type="peer", status="completed"
    )
    db.add(r)
    db.commit()

    _set_ids(keep.id, dupe.id)
    merge_contacts.merge(db)
    db.commit()

    remaining = db.query(models.ContactRelationship).all()
    assert not any(rel.from_contact_id == keep.id and rel.to_contact_id == keep.id
                   for rel in remaining)


def test_merge_repoints_valid_relationship(db):
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    third = models.Contact(name="Third")
    db.add_all([keep, dupe, third])
    db.commit()

    r = models.ContactRelationship(
        from_contact_id=dupe.id, to_contact_id=third.id,
        type="peer", status="completed"
    )
    db.add(r)
    db.commit()

    _set_ids(keep.id, dupe.id)
    merge_contacts.merge(db)
    db.commit()

    db.expire(r)
    assert r.from_contact_id == keep.id
    assert r.to_contact_id == third.id


def test_merge_drops_duplicate_relationship(db):
    """Relationship that already exists on keep-side is deleted, not duplicated."""
    keep = models.Contact(name="Keep")
    dupe = models.Contact(name="Dupe")
    third = models.Contact(name="Third")
    db.add_all([keep, dupe, third])
    db.commit()

    # Both keep and dupe have 'peer' relationship with third
    r_keep = models.ContactRelationship(
        from_contact_id=keep.id, to_contact_id=third.id,
        type="peer", status="completed"
    )
    r_dupe = models.ContactRelationship(
        from_contact_id=dupe.id, to_contact_id=third.id,
        type="peer", status="completed"
    )
    db.add_all([r_keep, r_dupe])
    db.commit()

    _set_ids(keep.id, dupe.id)
    merge_contacts.merge(db)
    db.commit()

    # Only one (keep→third) should remain
    rels = db.query(models.ContactRelationship).filter_by(
        from_contact_id=keep.id, to_contact_id=third.id, type="peer"
    ).all()
    assert len(rels) == 1


# ── match_slack_users: fetch_all_slack_users ─────────────────────────────────

def _make_member(id_, email, name="User", deleted=False, is_bot=False):
    return {
        "id": id_,
        "name": name,
        "deleted": deleted,
        "is_bot": is_bot,
        "profile": {"email": email, "real_name": name},
    }


def _mock_client(members, cursor=""):
    client = MagicMock()
    client.users_list.return_value = {
        "members": members,
        "response_metadata": {"next_cursor": cursor},
    }
    return client


def test_fetch_basic(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    mc = _mock_client([_make_member("U001", "alice@example.com", "Alice")])
    users = match_slack_users.fetch_all_slack_users(mc)
    assert len(users) == 1
    assert users[0]["email"] == "alice@example.com"
    assert users[0]["id"] == "U001"
    assert users[0]["name"] == "Alice"


def test_fetch_skips_deleted(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    mc = _mock_client([
        _make_member("U001", "alice@example.com"),
        _make_member("U002", "gone@example.com", deleted=True),
    ])
    users = match_slack_users.fetch_all_slack_users(mc)
    assert len(users) == 1
    assert users[0]["id"] == "U001"


def test_fetch_skips_bots(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    mc = _mock_client([
        _make_member("U001", "human@example.com"),
        _make_member("UBOT", "bot@example.com", is_bot=True),
    ])
    users = match_slack_users.fetch_all_slack_users(mc)
    assert len(users) == 1
    assert users[0]["id"] == "U001"


def test_fetch_skips_slackbot(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    mc = _mock_client([_make_member("USLACKBOT", "slackbot@slack.com")])
    users = match_slack_users.fetch_all_slack_users(mc)
    assert len(users) == 0


def test_fetch_skips_no_email(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    member = {"id": "U001", "name": "noemail", "deleted": False, "is_bot": False,
              "profile": {"email": "", "real_name": "No Email"}}
    mc = _mock_client([member])
    users = match_slack_users.fetch_all_slack_users(mc)
    assert len(users) == 0


def test_fetch_normalizes_email_to_lowercase(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    mc = _mock_client([_make_member("U001", "Alice@Example.COM")])
    users = match_slack_users.fetch_all_slack_users(mc)
    assert users[0]["email"] == "alice@example.com"


def test_fetch_pagination(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    mock_client = MagicMock()
    mock_client.users_list.side_effect = [
        {
            "members": [_make_member("U001", "page1@example.com")],
            "response_metadata": {"next_cursor": "cursor123"},
        },
        {
            "members": [_make_member("U002", "page2@example.com")],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    users = match_slack_users.fetch_all_slack_users(mock_client)
    assert len(users) == 2
    emails = {u["email"] for u in users}
    assert "page1@example.com" in emails
    assert "page2@example.com" in emails
