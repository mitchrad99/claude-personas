-- Migration: create contact_notes table for append-only timestamped notes per contact
-- Run once against Supabase via the SQL editor or psql.

CREATE TABLE IF NOT EXISTS contact_notes (
    id         SERIAL  PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    note       TEXT    NOT NULL,
    source     VARCHAR(20) DEFAULT 'manual',   -- manual / chat_debrief / ai_generated
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contact_notes_contact ON contact_notes(contact_id);
