"""
Contact deduplication and merge tool.

Scan for duplicates:
    python crm/scripts/merge_contacts.py

Merge two records (keep_id wins, delete_id is removed):
    python crm/scripts/merge_contacts.py <keep_id> <delete_id>

Set DATABASE_URL to target production; defaults to local SQLite.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import func
from models import (
    get_session,
    Contact, Task, Interaction, ContactNote, ContactRelationship,
    DCOrg, Funder, TaskRecommendation,
)

BACKFILL_FIELDS = ["email", "notes", "last_contact_date", "category"]

# Set by main() before any merge function is called
KEEP_ID = None
DUPE_ID = None


# ── display helpers ───────────────────────────────────────────────────────────

def fmt(label, val):
    if val is None:
        return f"  {label:<22} (none)"
    v = str(val)
    if len(v) > 60:
        v = v[:57] + "..."
    return f"  {label:<22} {v}"


def print_side_by_side(keep, dupe):
    fields = [
        ("id",                 "id"),
        ("name",               "name"),
        ("organization",       "organization"),
        ("title",              "title"),
        ("email",              "email"),
        ("phone",              "phone"),
        ("warmth",             "warmth"),
        ("category",           "category"),
        ("last_contact_date",  "last_contact_date"),
        ("last_email_date",    "last_email_date"),
        ("last_email_subject", "last_email_subject"),
        ("slack_user_id",      "slack_user_id"),
        ("created_at",         "created_at"),
        ("updated_at",         "updated_at"),
        ("notes",              "notes"),
    ]
    d_keep = keep.to_dict()
    d_dupe = dupe.to_dict()

    col = 50
    print("=" * (col * 2 + 3))
    print(f"  {'KEEP  (ID ' + str(KEEP_ID) + ')':<{col-2}}  {'DELETE (ID ' + str(DUPE_ID) + ')':<{col-2}}")
    print("=" * (col * 2 + 3))
    for label, key in fields:
        left  = fmt(label, d_keep.get(key))
        right = fmt(label, d_dupe.get(key))
        keep_empty = not d_keep.get(key)
        dupe_has   = bool(d_dupe.get(key))
        if key in BACKFILL_FIELDS and keep_empty and dupe_has:
            marker = " +"   # will be backfilled
        elif d_keep.get(key) != d_dupe.get(key):
            marker = " *"   # differs, canonical value kept
        else:
            marker = "  "
        print(f"{left:<{col}}{marker}{right}")
    print("=" * (col * 2 + 3))
    print("  + = null on canonical, will be copied from duplicate")
    print("  * = fields that differ between the two records (canonical value kept)")


# ── merge helpers ─────────────────────────────────────────────────────────────

def count_refs(session):
    return {
        "tasks":                  session.query(Task).filter_by(linked_contact_id=DUPE_ID).count(),
        "interactions":           session.query(Interaction).filter_by(contact_id=DUPE_ID).count(),
        "contact_notes":          session.query(ContactNote).filter_by(contact_id=DUPE_ID).count(),
        "contact_relationships":  session.query(ContactRelationship).filter(
                                      (ContactRelationship.from_contact_id == DUPE_ID) |
                                      (ContactRelationship.to_contact_id == DUPE_ID)
                                  ).count(),
        "dc_orgs":                session.query(DCOrg).filter_by(key_contact_id=DUPE_ID).count(),
        "funders":                session.query(Funder).filter_by(program_officer_contact_id=DUPE_ID).count(),
        "task_recommendations":   session.query(TaskRecommendation).filter_by(linked_contact_id=DUPE_ID).count(),
    }


def backfill_fields(session, keep, dupe):
    """Copy fields from dupe into keep where keep has null/empty values."""
    copied = {}
    for field in BACKFILL_FIELDS:
        keep_val = getattr(keep, field)
        dupe_val = getattr(dupe, field)
        if not keep_val and dupe_val:
            setattr(keep, field, dupe_val)
            copied[field] = dupe_val
    if copied:
        from datetime import datetime
        keep.updated_at = datetime.utcnow()
        print("  Fields backfilled from ID {} → {}:".format(DUPE_ID, KEEP_ID))
        for f, v in copied.items():
            v_str = str(v)
            if len(v_str) > 60:
                v_str = v_str[:57] + "..."
            print(f"    {f:<22} {v_str}")
    else:
        print("  No fields to backfill — canonical record has values for all checked fields.")
    return copied


def merge(session):
    # ── backfill null/empty fields on canonical record ────────────────────────
    keep = session.query(Contact).filter_by(id=KEEP_ID).first()
    dupe = session.query(Contact).filter_by(id=DUPE_ID).first()
    backfill_fields(session, keep, dupe)
    session.flush()

    # ── simple FK re-points ───────────────────────────────────────────────────
    session.query(Task).filter_by(linked_contact_id=DUPE_ID).update(
        {"linked_contact_id": KEEP_ID}, synchronize_session=False
    )
    session.query(Interaction).filter_by(contact_id=DUPE_ID).update(
        {"contact_id": KEEP_ID}, synchronize_session=False
    )
    session.query(ContactNote).filter_by(contact_id=DUPE_ID).update(
        {"contact_id": KEEP_ID}, synchronize_session=False
    )
    session.query(DCOrg).filter_by(key_contact_id=DUPE_ID).update(
        {"key_contact_id": KEEP_ID}, synchronize_session=False
    )
    session.query(Funder).filter_by(program_officer_contact_id=DUPE_ID).update(
        {"program_officer_contact_id": KEEP_ID}, synchronize_session=False
    )
    session.query(TaskRecommendation).filter_by(linked_contact_id=DUPE_ID).update(
        {"linked_contact_id": KEEP_ID}, synchronize_session=False
    )

    # ── contact_relationships — needs dedup logic ─────────────────────────────
    # UniqueConstraint on (from_contact_id, to_contact_id, type) means re-pointing
    # DUPE_ID → KEEP_ID could violate uniqueness or the self-ref check constraint.
    # For each affected row: delete if it would become a self-ref or already
    # exists on KEEP_ID; otherwise update.

    def existing(frm, to, typ):
        return session.query(ContactRelationship).filter_by(
            from_contact_id=frm, to_contact_id=to, type=typ
        ).first()

    rels_as_from = (session.query(ContactRelationship)
                    .filter_by(from_contact_id=DUPE_ID).all())
    for r in rels_as_from:
        new_from, new_to = KEEP_ID, r.to_contact_id
        if new_from == new_to or existing(new_from, new_to, r.type):
            print(f"  [relationship] dropping redundant row id={r.id} "
                  f"({r.from_contact_id}→{r.to_contact_id}, {r.type})")
            session.delete(r)
        else:
            r.from_contact_id = KEEP_ID

    session.flush()

    rels_as_to = (session.query(ContactRelationship)
                  .filter_by(to_contact_id=DUPE_ID).all())
    for r in rels_as_to:
        new_from, new_to = r.from_contact_id, KEEP_ID
        if new_from == new_to or existing(new_from, new_to, r.type):
            print(f"  [relationship] dropping redundant row id={r.id} "
                  f"({r.from_contact_id}→{r.to_contact_id}, {r.type})")
            session.delete(r)
        else:
            r.to_contact_id = KEEP_ID

    session.flush()

    # ── delete the duplicate ──────────────────────────────────────────────────
    dupe = session.query(Contact).filter_by(id=DUPE_ID).first()
    session.delete(dupe)


# ── scan mode ─────────────────────────────────────────────────────────────────

def scan_duplicates(session):
    """Print groups of contacts that share a name or email."""

    def contact_line(c):
        org   = c.organization or "(no org)"
        email = c.email or "(no email)"
        return f"    ID {c.id:<6}  {c.name:<30}  {org:<35}  {c.warmth:<5}  {email}"

    found_any = False

    # ── same name (case-insensitive) ──────────────────────────────────────────
    name_groups = (
        session.query(func.lower(Contact.name))
        .group_by(func.lower(Contact.name))
        .having(func.count(Contact.id) > 1)
        .all()
    )
    if name_groups:
        found_any = True
        print("── Duplicate names " + "─" * 60)
        for (lower_name,) in name_groups:
            contacts = (session.query(Contact)
                        .filter(func.lower(Contact.name) == lower_name)
                        .order_by(Contact.id)
                        .all())
            print(f'\n  "{contacts[0].name}"')
            for c in contacts:
                print(contact_line(c))

    # ── same email (non-null) ─────────────────────────────────────────────────
    email_groups = (
        session.query(func.lower(Contact.email))
        .filter(Contact.email.isnot(None), Contact.email != "")
        .group_by(func.lower(Contact.email))
        .having(func.count(Contact.id) > 1)
        .all()
    )
    if email_groups:
        found_any = True
        print("\n── Duplicate emails " + "─" * 60)
        for (lower_email,) in email_groups:
            contacts = (session.query(Contact)
                        .filter(func.lower(Contact.email) == lower_email)
                        .order_by(Contact.id)
                        .all())
            print(f'\n  "{contacts[0].email}"')
            for c in contacts:
                print(contact_line(c))

    if not found_any:
        print("No duplicate names or emails found.")
        return

    print()
    print("To merge: python crm/scripts/merge_contacts.py <keep_id> <delete_id>")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    global KEEP_ID, DUPE_ID

    args = sys.argv[1:]

    if len(args) == 0:
        session = get_session()
        try:
            scan_duplicates(session)
        finally:
            session.close()
        return

    if len(args) != 2:
        print("Usage:")
        print("  python crm/scripts/merge_contacts.py                    # scan for duplicates")
        print("  python crm/scripts/merge_contacts.py <keep_id> <delete_id>  # merge two records")
        sys.exit(1)

    try:
        KEEP_ID = int(args[0])
        DUPE_ID = int(args[1])
    except ValueError:
        print("ERROR: both arguments must be integers (contact IDs)")
        sys.exit(1)

    if KEEP_ID == DUPE_ID:
        print("ERROR: keep_id and delete_id must be different")
        sys.exit(1)

    session = get_session()
    try:
        keep = session.query(Contact).filter_by(id=KEEP_ID).first()
        dupe = session.query(Contact).filter_by(id=DUPE_ID).first()

        if not keep:
            print(f"ERROR: contact ID {KEEP_ID} not found.")
            sys.exit(1)
        if not dupe:
            print(f"ERROR: contact ID {DUPE_ID} not found.")
            sys.exit(1)

        print()
        print_side_by_side(keep, dupe)
        print()

        refs = count_refs(session)
        print("References that will be re-pointed from ID {} → {}:".format(DUPE_ID, KEEP_ID))
        for table, n in refs.items():
            print(f"  {table:<28} {n} row(s)")
        print()
        print("After re-pointing, contact ID {} will be deleted.".format(DUPE_ID))
        print()

        answer = input("Proceed with merge? [yes/no]: ").strip().lower()
        if answer != "yes":
            print("Aborted — no changes made.")
            sys.exit(0)

        print()
        merge(session)
        session.commit()
        print("Done. Contact ID {} deleted; all references now point to ID {}.".format(DUPE_ID, KEEP_ID))

    except Exception as e:
        session.rollback()
        print(f"ERROR: {e}")
        print("Transaction rolled back — no changes made.")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
