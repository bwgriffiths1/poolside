-- Docket share links: share_tokens grows a docket_id so the same table
-- (and the same revoke endpoint) covers meeting briefings and FERC
-- dockets. A token points at exactly one of the two.

ALTER TABLE share_tokens
    ALTER COLUMN meeting_id DROP NOT NULL;

ALTER TABLE share_tokens
    ADD COLUMN IF NOT EXISTS docket_id INT REFERENCES dockets(id) ON DELETE CASCADE;

ALTER TABLE share_tokens
    DROP CONSTRAINT IF EXISTS share_tokens_one_target;
ALTER TABLE share_tokens
    ADD CONSTRAINT share_tokens_one_target
    CHECK ((meeting_id IS NULL) <> (docket_id IS NULL));

CREATE INDEX IF NOT EXISTS idx_share_tokens_docket ON share_tokens (docket_id);
