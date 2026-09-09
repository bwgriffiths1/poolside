-- Migration 024: asynchronous Ask jobs + follow-up threading.
--
-- A deep memo can outlive an HTTP request (browser/edge drop it around
-- five minutes; Fable at xhigh over 50 filings can run twenty). Ask now
-- runs as a job in the docket_jobs/summarize_jobs mould: the route claims
-- a row and returns 202, a daemon thread does the work and writes the
-- ask_log row, and the page polls the job until it's terminal. Cancel is
-- cooperative (checked before the model call).
--
-- ask_log.parent_id links a follow-up question to the exchange it builds
-- on: the follow-up re-uses the parent's sources (same [n] numbering)
-- instead of retrieving afresh, and the prompt carries the prior Q&A.

CREATE TABLE IF NOT EXISTS ask_jobs (
    id             BIGSERIAL PRIMARY KEY,
    user_id        INT REFERENCES app_users(id) ON DELETE SET NULL,
    user_email     TEXT NOT NULL,
    request        JSONB NOT NULL,                 -- the AskBody as submitted
    status         TEXT NOT NULL DEFAULT 'queued', -- queued | running | cancelling
                                                   --   | complete | failed | cancelled
    progress_text  TEXT NOT NULL DEFAULT '',
    ask_log_id     BIGINT REFERENCES ask_log(id) ON DELETE SET NULL,
    error          TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ask_jobs_user_status ON ask_jobs (user_email, status);

ALTER TABLE ask_log ADD COLUMN IF NOT EXISTS parent_id BIGINT REFERENCES ask_log(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_ask_log_parent ON ask_log (parent_id);
