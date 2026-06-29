-- Migration: create interactions table for contact touchpoints (meetings, calls, events)
-- Run once against Supabase via the SQL editor or psql.

CREATE TABLE IF NOT EXISTS interactions (
    id               SERIAL PRIMARY KEY,
    contact_id       INTEGER NOT NULL REFERENCES contacts(id),
    date             DATE    NOT NULL,
    type             VARCHAR(20),   -- meeting / call / event / coffee / text / linkedin
    location         VARCHAR(255),
    notes            TEXT,
    follow_up_needed BOOLEAN DEFAULT FALSE,
    created_at       TIMESTAMP DEFAULT NOW(),
    updated_at       TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id);
CREATE INDEX IF NOT EXISTS idx_interactions_date    ON interactions(date);
