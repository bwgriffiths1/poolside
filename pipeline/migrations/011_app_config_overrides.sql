-- Migration 011: runtime-editable config moves off the container filesystem.
--
-- UI edits to prompts, model_config.json, and config.yaml used to be written
-- to the container FS, which Railway discards on every deploy — prod would
-- silently revert to the repo copy. These tables hold OVERRIDES; the repo
-- files remain the defaults, readers merge DB over file (pipeline/appconfig.py),
-- and the nightly pg_dump now covers config edits.

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS prompt_overrides (
    slug       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);
