-- Migration: add fuzzy-match columns to inbox_recommendations
-- Run once against Supabase via the SQL editor.

ALTER TABLE inbox_recommendations
  ADD COLUMN IF NOT EXISTS possible_contact_id INTEGER REFERENCES contacts(id),
  ADD COLUMN IF NOT EXISTS match_confidence     FLOAT;
