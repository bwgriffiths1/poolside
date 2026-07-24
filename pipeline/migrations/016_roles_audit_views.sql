-- Migration 016: user roles + audit log + page views.
--
-- Roles: app_users.role ∈ ('admin','editor','viewer').
--   * Existing users become admins — this preserves today's behavior, where
--     every logged-in user had full access. The ADD ... DEFAULT 'admin'
--     followed by SET DEFAULT 'viewer' two-step makes the backfill safe:
--     on a fresh install schema.sql already created the column (default
--     'viewer'), so the 'admin' default here never applies; on an existing
--     DB the backfill happens exactly once.
--   * Invites carry the role the admin chose. Invites issued before this
--     migration accept as 'viewer' (safe default; re-issue if a higher
--     role was intended).
--
-- audit_log / page_views ship here but are written by later PRs (audit
-- middleware, read-analytics beacon). Neither table has FKs to the entities
-- it references: the log is history and must survive entity deletion
-- (user_id keeps a SET NULL FK so a deleted account doesn't orphan rows;
-- user_email is a snapshot for display). No retention policy for now —
-- rows are small; the weekly prune cron can adopt these tables if volume
-- ever matters.

-- ── Roles ───────────────────────────────────────────────────────────────
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'admin';
ALTER TABLE app_users ALTER COLUMN role SET DEFAULT 'viewer';
ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_role_check;
ALTER TABLE app_users ADD CONSTRAINT app_users_role_check
    CHECK (role IN ('admin', 'editor', 'viewer'));

ALTER TABLE user_tokens ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'viewer';
ALTER TABLE user_tokens DROP CONSTRAINT IF EXISTS user_tokens_role_check;
ALTER TABLE user_tokens ADD CONSTRAINT user_tokens_role_check
    CHECK (role IN ('admin', 'editor', 'viewer'));

-- ── Audit log — auto-captured non-GET API actions ───────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INT REFERENCES app_users(id) ON DELETE SET NULL,
    user_email   TEXT NOT NULL,               -- snapshot at action time
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,               -- concrete: /api/meetings/42/briefing/approve
    route        TEXT,                        -- template: /api/meetings/{meeting_id}/briefing/approve
    path_params  JSONB NOT NULL DEFAULT '{}',
    query        TEXT,
    status       INT NOT NULL,                -- 4xx/5xx rows kept, flagged in UI
    duration_ms  INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user    ON audit_log (user_email, created_at DESC);

-- ── Page views — read analytics ─────────────────────────────────────────
-- One row per (user, entity) per dedupe window; deduped at write time by
-- db.record_page_view so refetches and React StrictMode double-mounts
-- don't inflate counts.
CREATE TABLE IF NOT EXISTS page_views (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INT REFERENCES app_users(id) ON DELETE SET NULL,
    user_email   TEXT NOT NULL,
    entity_type  TEXT NOT NULL,               -- meeting|briefing|docket|roundup|deep_dive
    entity_id    INT NOT NULL,
    viewed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_page_views_entity ON page_views (entity_type, entity_id, viewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_page_views_user   ON page_views (user_id, viewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_page_views_time   ON page_views (viewed_at DESC);
