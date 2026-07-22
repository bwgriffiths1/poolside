-- Migration 014: FERC eLibrary docket tracking
--
-- One `dockets` row per user-tracked FERC docket family ("ER26-925" — the
-- bare number; eLibrary's search returns every sub-docket under it). Each
-- eLibrary accession becomes a `docket_filings` row carrying the FERC
-- taxonomy verbatim plus the enrichment pulled from GetDocInfoFromP8;
-- its files (from GetFileListFromP8) land in `docket_filing_files` with
-- extracted text cached in raw_content — FERC re-fetches run 40-60s per
-- file, so unlike ISO-NE documents the text cache IS the working copy
-- (NPC virtual-doc precedent).
--
-- Summaries do NOT get new tables: per-filing summaries live in
-- summary_versions(entity_type='docket_filing') and the docket-level
-- "state of play" in summary_versions(entity_type='docket'), so the
-- editor / version-history / approval machinery applies unchanged.
--
-- `docket_jobs` clones summarize_jobs (003 + 010): status lifecycle
-- queued | running | cancelling | complete | failed | cancelled, updated
-- in place by a daemon thread, polled from the frontend, with a partial
-- unique index so admission is a race-free INSERT ... ON CONFLICT.
-- mode 'sync' = crawl + enrich + summarize new filings (auto-chains the
-- state-of-play when new summaries landed); mode 'brief' = regenerate
-- the state-of-play alone.

CREATE TABLE IF NOT EXISTS dockets (
    id              SERIAL PRIMARY KEY,
    docket_number   TEXT NOT NULL UNIQUE,   -- normalized "ER26-925"
    title           TEXT,                   -- user label; falls back to root-filing description
    notes           TEXT,
    auto_refresh    BOOLEAN NOT NULL DEFAULT true,  -- include in the scheduled new-filing check
    last_crawled_at TIMESTAMPTZ,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS docket_filings (
    id                SERIAL PRIMARY KEY,
    docket_id         INT NOT NULL REFERENCES dockets(id) ON DELETE CASCADE,
    accession_number  TEXT NOT NULL,        -- "20251230-5436" — eLibrary's natural key
    category          TEXT,                 -- Submittal | Issuance
    document_class    TEXT,                 -- "Comments/Protest", "Order/Opinion", ...
    document_type     TEXT,                 -- "Comment on Filing", ...
    description       TEXT,
    sub_docket        TEXT,                 -- "ER26-925-000" as labeled on the filing
    filed_date        DATE,
    issued_date       DATE,
    posted_date       DATE,
    comments_due_date DATE,                 -- from GetDocInfoFromP8 (often null)
    response_due_date DATE,
    ferc_cite         TEXT,                 -- "194 FERC ¶ 61,249" on orders
    fed_reg_num       TEXT,
    filing_parties    JSONB,                -- eLcAffiliation rows: AUTHOR + AGENT kept
    treatment         TEXT NOT NULL DEFAULT 'brief',  -- full | brief | skip (class treatment map)
    is_docless        BOOLEAN NOT NULL DEFAULT false, -- "(doc-less)" interventions: roster only
    raw_hit           JSONB,                -- the AdvancedSearch hit, verbatim
    raw_docinfo       JSONB,                -- the GetDocInfoFromP8 row, verbatim
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (docket_id, accession_number)
);

CREATE TABLE IF NOT EXISTS docket_filing_files (
    id              SERIAL PRIMARY KEY,
    filing_id       INT NOT NULL REFERENCES docket_filings(id) ON DELETE CASCADE,
    file_id         TEXT NOT NULL,          -- eLibrary GUID; feeds DownloadP8File
    file_desc       TEXT,                   -- "Transmittal Letter", "Attachment E - Marked Tariff"
    orig_file_name  TEXT,
    file_type       TEXT,                   -- PDF | DOCX | TXT (as reported)
    file_size       BIGINT,
    page_count      INT,
    file_list_order INT,
    included        BOOLEAN NOT NULL DEFAULT true,  -- false = tariff sheet / redline etc.
    raw_content     TEXT,                   -- extracted text cache (see header comment)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (filing_id, file_id)
);

CREATE TABLE IF NOT EXISTS docket_jobs (
    id                 SERIAL PRIMARY KEY,
    docket_id          INT NOT NULL REFERENCES dockets(id) ON DELETE CASCADE,
    mode               TEXT NOT NULL DEFAULT 'sync',   -- sync | brief
    status             TEXT NOT NULL DEFAULT 'queued', -- queued | running | cancelling
                                                       --   | complete | failed | cancelled
    progress_text      TEXT NOT NULL DEFAULT '',
    filings_found      INT NOT NULL DEFAULT 0,         -- new filings discovered this run
    filings_summarized INT NOT NULL DEFAULT 0,
    input_tokens       BIGINT NOT NULL DEFAULT 0,
    output_tokens      BIGINT NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(10, 4) NOT NULL DEFAULT 0,
    error              TEXT,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at        TIMESTAMPTZ,
    created_by         TEXT
);

CREATE INDEX IF NOT EXISTS idx_docket_filings_docket
    ON docket_filings (docket_id, filed_date DESC);
CREATE INDEX IF NOT EXISTS idx_docket_filing_files_filing
    ON docket_filing_files (filing_id);
CREATE INDEX IF NOT EXISTS idx_docket_jobs_docket_status
    ON docket_jobs (docket_id, status);

-- One active job per docket, enforced by the database (010 pattern):
-- lets the service claim atomically with INSERT ... ON CONFLICT DO NOTHING.
CREATE UNIQUE INDEX IF NOT EXISTS uq_docket_jobs_one_active
    ON docket_jobs (docket_id)
    WHERE status IN ('queued', 'running', 'cancelling');
