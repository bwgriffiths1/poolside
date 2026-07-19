-- Migration 013: per-user email notification preferences
-- JSONB keys (all default-off; absence = false):
--   briefing_ready  — email when a watched meeting's briefing is approved
--   weekly_digest   — Monday week-ahead digest
ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS email_prefs JSONB NOT NULL DEFAULT '{}';
