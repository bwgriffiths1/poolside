-- Migration 008: manually-added agenda-item materials
-- Lets a user attach an overlooked memo (by URL or file upload) to a specific
-- agenda item so it feeds that section's summary. These reuse the existing
-- `documents` + `item_documents` plumbing — the summarizer already reads a
-- doc's raw_content / source_url — so the only new state is:
--   1. a `manual` flag to badge them and protect them from scraper churn, and
--   2. a side table holding the uploaded bytes (kept out of `documents` so the
--      hot `SELECT d.*` doc queries never drag a BYTEA blob along).

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS manual BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS document_files (
    document_id INT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    mime_type   TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes  INT NOT NULL,
    data        BYTEA NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
