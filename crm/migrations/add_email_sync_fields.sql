-- Migration: add Gmail sync fields to contacts table
-- Run once against Supabase via the SQL editor or psql.
-- All columns are nullable — safe on a live table with existing rows.

ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS last_email_date      TIMESTAMP,
  ADD COLUMN IF NOT EXISTS last_email_subject   VARCHAR(500),
  ADD COLUMN IF NOT EXISTS last_email_direction VARCHAR(10),
  ADD COLUMN IF NOT EXISTS last_synced_at       TIMESTAMP;
