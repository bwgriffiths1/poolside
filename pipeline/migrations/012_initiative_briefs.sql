-- Migration 012: initiative briefs ("the story so far")
-- One cached synthesized narrative per initiative tag, generated from the
-- tagged agenda items' summaries. Status lives on the row itself
-- (monthly_roundups pattern) — no jobs table; the UI polls the row while
-- status = 'generating'. Regeneration overwrites in place.
--   source_item_count / source_latest_meeting_date snapshot the inputs that
--   fed the brief so the UI can flag staleness once new tagged items land.

CREATE TABLE IF NOT EXISTS initiative_briefs (
    tag_id        INT PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft | generating | complete | error
    brief_md      TEXT,
    error_message TEXT,
    model_id      TEXT,
    input_tokens  INT,
    output_tokens INT,
    cost_usd      NUMERIC(10, 4),
    source_item_count          INT,
    source_latest_meeting_date DATE,
    created_by    TEXT DEFAULT 'system',
    generated_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
