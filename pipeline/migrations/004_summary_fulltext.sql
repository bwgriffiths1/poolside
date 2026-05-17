-- Full-text search across summary bodies.
-- We index the existing `summary_versions.detailed` column as a tsvector,
-- so the command palette can search summary content (not just meeting
-- titles + tags). GIN index lets us query with @@ tsquery cheaply.

-- Generated column produces the tsvector at write time. PG 12+.
ALTER TABLE summary_versions
    ADD COLUMN IF NOT EXISTS detailed_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(one_line, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(detailed, '')), 'B')
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_summary_versions_detailed_tsv
    ON summary_versions USING GIN (detailed_tsv);

-- Helpful covering index for the "find the current version for an entity"
-- predicate that the search query uses.
CREATE INDEX IF NOT EXISTS idx_summary_versions_entity_status
    ON summary_versions (entity_type, entity_id, status);
