-- Migration: add category column to tasks table
-- Run once against Supabase via the SQL editor or psql.
-- Values: outreach / intro_followup / fundraising / policy / admin / career / sabbatical_prep

ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS category VARCHAR(30);
