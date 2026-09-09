-- Migration 022: full-text search over UNDERLYING document text.
--
-- Ask Poolside's second tier ("search the documents, not just the
-- summaries") needs a ranked full-text index over the extracted text we
-- already cache: documents.raw_content (ISO-NE / PJM meeting materials)
-- and docket_filing_files.raw_content (FERC eLibrary filing files).
-- Same shape as migration 004 (stored generated tsvector + GIN) so the
-- query side can reuse the summary-search machinery.
--
-- Two guards, because raw_content is untrusted extraction output:
--   * capped at 250k chars — tsvector positions stop at word 16,383
--     anyway, and a tariff attachment full of unique numbers could
--     otherwise push a single tsvector past Postgres' 1MB limit;
--   * wrapped in a function whose EXCEPTION block falls back to a much
--     shorter prefix, so one pathological row can never fail the table
--     rewrite (which would fail the boot — api/migrate.py propagates).
--
-- Adding a STORED generated column rewrites both tables once; the text
-- volume is modest (tens of MB) so this is seconds, not minutes.

CREATE OR REPLACE FUNCTION raw_content_tsv(t TEXT) RETURNS tsvector
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
BEGIN
    RETURN to_tsvector('english', left(coalesce(t, ''), 250000));
EXCEPTION WHEN OTHERS THEN
    RETURN to_tsvector('english', left(coalesce(t, ''), 40000));
END
$$;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS raw_tsv tsvector
    GENERATED ALWAYS AS (raw_content_tsv(raw_content)) STORED;

CREATE INDEX IF NOT EXISTS idx_documents_raw_tsv
    ON documents USING GIN (raw_tsv);

ALTER TABLE docket_filing_files
    ADD COLUMN IF NOT EXISTS raw_tsv tsvector
    GENERATED ALWAYS AS (raw_content_tsv(raw_content)) STORED;

CREATE INDEX IF NOT EXISTS idx_docket_filing_files_raw_tsv
    ON docket_filing_files USING GIN (raw_tsv);
