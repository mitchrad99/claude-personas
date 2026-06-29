#!/usr/bin/env python3
"""
One-time migration: local SQLite → Supabase Postgres

Usage:
    export SUPABASE_URL='postgresql://postgres.jwkejbugjdnunkhuesyj:[PASSWORD]@aws-1-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require'
    python3 crm/migrate_to_supabase.py
"""
import os
import sys
import sqlite3

SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'aao_crm.db')
TABLES = ['contacts', 'funders', 'tasks', 'dc_orgs', 'opportunities']

# ── Resolve Supabase URL ──────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
if not SUPABASE_URL:
    print("ERROR: SUPABASE_URL is not set.\n")
    print("Export it first:")
    print("  export SUPABASE_URL='postgresql://postgres.jwkejbugjdnunkhuesyj:[PASSWORD]"
          "@aws-1-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require'")
    sys.exit(1)

if SUPABASE_URL.startswith('postgres://'):
    SUPABASE_URL = SUPABASE_URL.replace('postgres://', 'postgresql://', 1)

# ── Read all rows from SQLite ─────────────────────────────────────────────────

print(f"\nReading from: {SQLITE_PATH}")

sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row

source_data = {}
for table in TABLES:
    rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
    source_data[table] = [dict(r) for r in rows]

sqlite_conn.close()

print("\n=== SQLite row counts ===")
total_source = 0
for table in TABLES:
    n = len(source_data[table])
    total_source += n
    print(f"  {table:<20} {n:>4}")
print(f"  {'TOTAL':<20} {total_source:>4}")

# ── Confirm before writing ────────────────────────────────────────────────────

host = SUPABASE_URL.split('@')[-1].split('/')[0] if '@' in SUPABASE_URL else SUPABASE_URL[:60]
print(f"\nTarget: {host}")
answer = input("\nProceed with migration? [y/N] ").strip().lower()
if answer != 'y':
    print("Aborted.")
    sys.exit(0)

# ── Connect to Postgres via app models ───────────────────────────────────────
# Set DATABASE_URL before importing models so the engine is created with the
# Supabase URL, not the SQLite default.

os.environ['DATABASE_URL'] = SUPABASE_URL
sys.path.insert(0, os.path.dirname(__file__))

print("\nConnecting to Supabase...")
from models import Base, engine as pg_engine, init_db  # noqa: E402

print("Creating tables if they don't exist...")
init_db()

# ── Insert rows, skipping any that already exist ──────────────────────────────

from sqlalchemy import text                                    # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

results = {}

with pg_engine.begin() as pg_conn:
    for table in TABLES:
        rows = source_data[table]
        tbl = Base.metadata.tables[table]

        if not rows:
            results[table] = (0, 0)
            continue

        before = pg_conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()

        stmt = pg_insert(tbl).values(rows).on_conflict_do_nothing()
        pg_conn.execute(stmt)

        after = pg_conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        results[table] = (len(rows), after - before)

    # Advance each table's sequence past the highest migrated ID so future
    # inserts don't conflict with rows we just wrote.
    for table in TABLES:
        pg_conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE(MAX(id), 1), true) FROM {table}"
        ))

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n=== Migration results ===")
total_inserted = 0
for table in TABLES:
    source_n, inserted_n = results[table]
    skipped = source_n - inserted_n
    total_inserted += inserted_n
    skip_note = f"  ({skipped} already existed, skipped)" if skipped else ""
    print(f"  {table:<20} {inserted_n:>4}/{source_n} inserted{skip_note}")

print(f"  {'TOTAL':<20} {total_inserted:>4}/{total_source}")
print("\nDone. Sequences reset — new rows will auto-increment safely.")
