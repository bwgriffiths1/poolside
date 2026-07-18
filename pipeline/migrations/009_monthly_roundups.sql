-- Migration 009: monthly cross-committee roundups
-- One "state of play" report per venue per calendar month, synthesized from
-- every committee briefing in that month (MC + TC + RC + ...), with the prior
-- month's roundup fed back in as continuity context. Status lives on the row
-- itself (deep_dive_reports pattern) — no jobs table; the UI polls the row.
--   month is always the first day of the month; UNIQUE(venue_id, month) means
--   regeneration overwrites in place rather than versioning.

CREATE TABLE IF NOT EXISTS monthly_roundups (
    id            SERIAL PRIMARY KEY,
    venue_id      INT NOT NULL REFERENCES venues(id),
    month         DATE NOT NULL,                  -- first day of the month
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft | generating | complete | error
    model_id      TEXT,
    report_md     TEXT,
    error_message TEXT,
    progress_text TEXT,
    input_tokens  INT,
    output_tokens INT,
    cost_usd      NUMERIC(10, 4),
    created_by    TEXT DEFAULT 'system',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (venue_id, month)
);

-- Provenance: which meetings' briefings fed the roundup (refreshed each run).
CREATE TABLE IF NOT EXISTS roundup_meetings (
    roundup_id INT NOT NULL REFERENCES monthly_roundups(id) ON DELETE CASCADE,
    meeting_id INT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    PRIMARY KEY (roundup_id, meeting_id)
);

CREATE INDEX IF NOT EXISTS idx_monthly_roundups_month ON monthly_roundups (venue_id, month DESC);
CREATE INDEX IF NOT EXISTS idx_roundup_meetings_meeting ON roundup_meetings (meeting_id);
