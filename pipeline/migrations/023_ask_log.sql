-- Migration 023: Ask Poolside log.
--
-- Every question asked of Ask Poolside, with the sources it retrieved and
-- the answer it gave, stored server-side. Unlike audit_log (016) this
-- keeps bodies on purpose: Ask carries no secrets, and the point is to
-- revisit, compare and re-run answers across sessions and devices instead
-- of losing them with the browser tab. Rows are never updated.

CREATE TABLE IF NOT EXISTS ask_log (
    id             BIGSERIAL PRIMARY KEY,
    user_id        INT REFERENCES app_users(id) ON DELETE SET NULL,
    user_email     TEXT NOT NULL,
    question       TEXT NOT NULL,
    scope          JSONB NOT NULL DEFAULT '{}',   -- corpus/depth/dockets as resolved
    model_id       TEXT,                          -- null on the no-results path
    effort         TEXT,
    detail         TEXT NOT NULL DEFAULT 'standard',
    sources        JSONB NOT NULL DEFAULT '[]',   -- the serialized [n] source list
    answer_md      TEXT NOT NULL,
    input_tokens   INT,
    output_tokens  INT,
    cost_usd       NUMERIC(10, 5),
    duration_ms    INT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ask_log_user    ON ask_log (user_email, id DESC);
CREATE INDEX IF NOT EXISTS idx_ask_log_created ON ask_log (created_at DESC);
