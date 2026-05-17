-- Phase 3: approval state, watches, in-app notifications, share tokens.

-- ── Approval bookkeeping on summary_versions ────────────────────────────
-- The lifecycle_status enum already includes 'approved' but nothing today
-- stamps who approved or when. These columns make the transition real.
ALTER TABLE summary_versions
    ADD COLUMN IF NOT EXISTS approved_by  TEXT,
    ADD COLUMN IF NOT EXISTS approved_at  TIMESTAMPTZ;


-- ── Meeting watches ─────────────────────────────────────────────────────
-- A user "watches" a meeting → they get a notification when it transitions
-- to summarized or its briefing is approved.
CREATE TABLE IF NOT EXISTS meeting_watches (
    id           SERIAL PRIMARY KEY,
    user_id      INT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    meeting_id   INT NOT NULL REFERENCES meetings(id)  ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, meeting_id)
);

CREATE INDEX IF NOT EXISTS idx_meeting_watches_user ON meeting_watches (user_id);
CREATE INDEX IF NOT EXISTS idx_meeting_watches_meeting ON meeting_watches (meeting_id);


-- ── Notifications ───────────────────────────────────────────────────────
-- One row per event delivered to one user. Shared shape for: meeting
-- approval pings (from watched-meeting transitions), drift alarms, future
-- system messages.
CREATE TABLE IF NOT EXISTS notifications (
    id           SERIAL PRIMARY KEY,
    user_id      INT  REFERENCES app_users(id) ON DELETE CASCADE,  -- NULL → broadcast (drift alarms etc.)
    kind         TEXT NOT NULL,                                    -- 'briefing_approved' | 'briefing_published' | 'drift_alarm' | ...
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,               -- denormalized for fast read; { meeting_id, title, ... }
    meeting_id   INT  REFERENCES meetings(id) ON DELETE CASCADE,   -- nullable: e.g. drift_alarm has no meeting
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications (user_id, read_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_kind_created
    ON notifications (kind, created_at DESC);


-- ── Share tokens ────────────────────────────────────────────────────────
-- A token grants public, read-only access to a meeting's briefing without
-- requiring login. Tokens are random opaque strings; the route resolves
-- token → meeting_id and renders the briefing.
CREATE TABLE IF NOT EXISTS share_tokens (
    id           SERIAL PRIMARY KEY,
    token        TEXT NOT NULL UNIQUE,        -- url-safe random string
    meeting_id   INT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    created_by   INT REFERENCES app_users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ,                  -- NULL = no expiry
    revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_share_tokens_meeting ON share_tokens (meeting_id);
