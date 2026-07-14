-- Migration 007: meeting_attachments
-- User-uploaded files attached to a meeting from the web "Files" portal —
-- hand-written briefings, scanned notes, ad-hoc reference docs. Distinct from
-- `documents` (scraped meeting materials) and `editor_images` (inline pastes):
-- these are arbitrary files a user uploads and can download back verbatim.

CREATE TABLE IF NOT EXISTS meeting_attachments (
    id          SERIAL PRIMARY KEY,
    meeting_id  INT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    mime_type   TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes  INT NOT NULL,
    note        TEXT,                       -- optional user caption
    data        BYTEA NOT NULL,
    uploaded_by TEXT,                       -- app_users.email at upload time
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meeting_attachments_meeting
    ON meeting_attachments (meeting_id, created_at DESC);
