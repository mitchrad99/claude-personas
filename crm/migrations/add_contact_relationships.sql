-- Migration: create contact_relationships table (social graph edges between contacts)
-- Run once against Supabase via the SQL editor or psql.

CREATE TABLE IF NOT EXISTS contact_relationships (
    id              SERIAL  PRIMARY KEY,
    from_contact_id INTEGER NOT NULL REFERENCES contacts(id),
    to_contact_id   INTEGER NOT NULL REFERENCES contacts(id),
    type            VARCHAR(30),   -- introduced_by / wants_to_connect / peer / mentor / referred_funder
    status          VARCHAR(20) DEFAULT 'completed',   -- completed / pending
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    CONSTRAINT ck_contact_rel_no_self CHECK (from_contact_id != to_contact_id),
    CONSTRAINT uq_contact_rel         UNIQUE (from_contact_id, to_contact_id, type)
);

CREATE INDEX IF NOT EXISTS idx_contact_rel_from ON contact_relationships(from_contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_rel_to   ON contact_relationships(to_contact_id);
