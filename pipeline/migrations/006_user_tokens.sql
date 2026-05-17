-- Phase 4: user invites + password-reset tokens.
--
-- One table, two purposes — we don't yet have email infrastructure, so
-- an admin generates a token, copies the resulting URL, and forwards it
-- to the user (or, for the user's own reset, they ask the admin).
-- Same shape and lifecycle for both kinds; just a different `purpose`.

CREATE TABLE IF NOT EXISTS user_tokens (
    id           SERIAL PRIMARY KEY,
    token        TEXT NOT NULL UNIQUE,    -- url-safe random
    purpose      TEXT NOT NULL,           -- 'invite' | 'password_reset'
    email        TEXT NOT NULL,           -- invites: target; resets: existing user email
    name         TEXT,                    -- invites: pre-filled display name
    created_by   INT REFERENCES app_users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ,
    used_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_user_tokens_purpose ON user_tokens (purpose, used_at);
CREATE INDEX IF NOT EXISTS idx_user_tokens_email   ON user_tokens (email);
