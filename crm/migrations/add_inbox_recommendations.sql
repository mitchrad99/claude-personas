-- Migration: create inbox_recommendations table
-- Run once against Supabase via the SQL editor or psql.

CREATE TABLE IF NOT EXISTS inbox_recommendations (
    id                     SERIAL PRIMARY KEY,
    sender_name            VARCHAR(255),
    sender_email           VARCHAR(255),
    email_subject          VARCHAR(500),
    email_date             TIMESTAMP,
    email_snippet          TEXT,
    recommendation_type    VARCHAR(20),
    recommendation_json    TEXT,
    recommendation_summary TEXT,
    status                 VARCHAR(20) DEFAULT 'pending',
    created_at             TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inbox_sender ON inbox_recommendations(sender_email);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_recommendations(status);
