-- =============================================================================
-- Meeting Summarization Tool — Database Schema
-- =============================================================================
-- Apply with:
--   psql -U meeting_user -d meeting_summaries -f pipeline/schema.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Venues  (ISO-NE, PJM, FERC, ...)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS venues (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    short_name TEXT NOT NULL UNIQUE,   -- "ISO-NE", "PJM", "FERC"
    website    TEXT,
    active     BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 2. Meeting types per venue  (MC, NPC, RC, TC, ...)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meeting_types (
    id          SERIAL PRIMARY KEY,
    venue_id    INT NOT NULL REFERENCES venues(id),
    name        TEXT NOT NULL,         -- "Markets Committee"
    short_name  TEXT NOT NULL,         -- "MC"
    description TEXT,
    active      BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (venue_id, short_name)
);

-- -----------------------------------------------------------------------------
-- 3. Meetings  — specific dated instances
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meetings (
    id              SERIAL PRIMARY KEY,
    meeting_type_id INT NOT NULL REFERENCES meeting_types(id),
    external_id     TEXT,              -- venue-specific event ID (e.g. ISO-NE "160091")
    title           TEXT,              -- optional display override
    meeting_date    DATE NOT NULL,
    end_date        DATE,              -- last day for multi-day meetings (null = single day)
    meeting_number  TEXT,              -- "10th MC Mtg"
    location        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
                                       -- pending | processing | complete
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (meeting_type_id, external_id)
);

-- -----------------------------------------------------------------------------
-- 4. Agenda items  — flat self-referencing tree
--    depth: 0 = top-level section, 1 = agenda item, 2 = sub-item, ...
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agenda_items (
    id          SERIAL PRIMARY KEY,
    meeting_id  INT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    parent_id   INT REFERENCES agenda_items(id) ON DELETE SET NULL,
    item_id     TEXT,                  -- dot-notation "7.1.b" (null allowed for auto-sub)
    prefix      TEXT,                  -- filename prefix "a07.1.b" for doc matching
    title       TEXT NOT NULL,
    depth       INT NOT NULL DEFAULT 0,
    seq         INT NOT NULL,          -- ordering within same parent
    auto_sub    BOOLEAN NOT NULL DEFAULT false,
    -- extracted metadata
    presenter   TEXT,
    org         TEXT,
    vote_status TEXT,
    wmpp_id     TEXT,
    time_slot   TEXT,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 5. Documents  — files attached to a meeting
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id           SERIAL PRIMARY KEY,
    meeting_id   INT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    file_type    TEXT,                 -- ".pdf", ".docx", ".pptx", ...
    source_url   TEXT,
    file_hash    TEXT,                 -- SHA-256 for dedup / change detection
    ceii_skipped BOOLEAN NOT NULL DEFAULT false,
    ignored      BOOLEAN NOT NULL DEFAULT false,  -- intentionally unassigned; skip warning
    raw_content  TEXT,                 -- extracted text (populated by pipeline)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (meeting_id, filename)
);

-- -----------------------------------------------------------------------------
-- 5b. Document images  — extracted images from PDF/PPTX documents
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_images (
    id            SERIAL PRIMARY KEY,
    document_id   INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,           -- "report_slide3_img0.png"
    page_or_slide INT NOT NULL,            -- 1-indexed page/slide number
    img_index     INT NOT NULL DEFAULT 0,  -- 0-indexed image within page/slide
    width         INT,
    height        INT,
    file_path     TEXT,                    -- relative path from storage_root
    image_b64     TEXT,                    -- base64-encoded PNG for LLM/UI use
    description   TEXT,                    -- Claude-generated description
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, page_or_slide, img_index)
);

-- -----------------------------------------------------------------------------
-- 6. Item–document assignments  (many-to-many)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_documents (
    item_id     INT NOT NULL REFERENCES agenda_items(id) ON DELETE CASCADE,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, document_id)
);

-- -----------------------------------------------------------------------------
-- 7. Tags  — unified system: initiatives, topics, regulatory areas, etc.
--    tag_type: "initiative" | "topic" | "regulatory" | "custom"
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tags (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,  -- "CAR-SA", "GISWG", "capacity markets"
    tag_type    TEXT NOT NULL DEFAULT 'custom',
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- 8. Entity tags  — polymorphic; attaches tags to any entity
--    entity_type: "meeting" | "agenda_item" | "document"
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entity_tags (
    id          SERIAL PRIMARY KEY,
    tag_id      INT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id   INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tag_id, entity_type, entity_id)
);

-- -----------------------------------------------------------------------------
-- 9. Summary versions  — versioned summaries for any entity
--    entity_type: "meeting" | "agenda_item" | "document"
--    status:      "stub" | "draft" | "approved" | "superseded"
--    version:     increments per (entity_type, entity_id); starts at 1
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS summary_versions (
    id          SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   INT NOT NULL,
    version     INT NOT NULL,
    one_line    TEXT,                  -- single-sentence summary for scanning
    detailed    TEXT,                  -- full narrative summary
    model_id    TEXT,                  -- null for manual entries
    is_manual   BOOLEAN NOT NULL DEFAULT false,
    status      TEXT NOT NULL DEFAULT 'stub',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  TEXT,                  -- user label or "system"
    UNIQUE (entity_type, entity_id, version)
);

-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_agenda_items_meeting    ON agenda_items (meeting_id);
CREATE INDEX IF NOT EXISTS idx_agenda_items_parent     ON agenda_items (parent_id);
CREATE INDEX IF NOT EXISTS idx_documents_meeting       ON documents (meeting_id);
CREATE INDEX IF NOT EXISTS idx_document_images_doc     ON document_images (document_id);
CREATE INDEX IF NOT EXISTS idx_entity_tags_entity      ON entity_tags (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_summary_versions_entity ON summary_versions (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_meetings_type           ON meetings (meeting_type_id);
CREATE INDEX IF NOT EXISTS idx_meetings_date           ON meetings (meeting_date);

-- -----------------------------------------------------------------------------
-- Seed data  — ISO-NE venue and committees
-- (INSERT ... ON CONFLICT DO NOTHING is safe to re-run)
-- -----------------------------------------------------------------------------
INSERT INTO venues (name, short_name, website) VALUES
    ('ISO New England', 'ISO-NE', 'https://www.iso-ne.com')
ON CONFLICT (short_name) DO NOTHING;

INSERT INTO meeting_types (venue_id, name, short_name) VALUES
    ((SELECT id FROM venues WHERE short_name = 'ISO-NE'), 'Markets Committee',      'MC'),
    ((SELECT id FROM venues WHERE short_name = 'ISO-NE'), 'Participants Committee', 'NPC'),
    ((SELECT id FROM venues WHERE short_name = 'ISO-NE'), 'Reliability Committee',  'RC'),
    ((SELECT id FROM venues WHERE short_name = 'ISO-NE'), 'Transmission Committee', 'TC')
ON CONFLICT (venue_id, short_name) DO NOTHING;

-- NYISO venue
INSERT INTO venues (name, short_name, website, active) VALUES
    ('NYISO', 'NYISO', 'https://www.nyiso.com', false)
ON CONFLICT (short_name) DO NOTHING;

-- NYISO meeting types
INSERT INTO meeting_types (venue_id, name, short_name) VALUES
    ((SELECT id FROM venues WHERE short_name = 'NYISO'), 'Business Issues Committee', 'BIC'),
    ((SELECT id FROM venues WHERE short_name = 'NYISO'), 'Management Committee',      'MC'),
    ((SELECT id FROM venues WHERE short_name = 'NYISO'), 'Operating Committee',       'OC')
ON CONFLICT (venue_id, short_name) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 10. App users  — local authentication accounts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_users (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    password_hash TEXT,                          -- NULL for Google-only users
    auth_provider TEXT NOT NULL DEFAULT 'local', -- 'local' | 'google'
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_app_users_email ON app_users (email);

-- -----------------------------------------------------------------------------
-- 11. Deep dive reports  — cross-meeting special reports
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deep_dive_reports (
    id            SERIAL PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft | generating | complete | error
    prompt_slug   TEXT,
    model_id      TEXT,
    config        JSONB NOT NULL DEFAULT '{}',    -- {max_images, comparison_mode, ...}
    report_md     TEXT,
    error_message TEXT,
    created_by    TEXT DEFAULT 'system',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deep_dive_reports_status  ON deep_dive_reports (status);
CREATE INDEX IF NOT EXISTS idx_deep_dive_reports_created ON deep_dive_reports (created_at DESC);

-- -----------------------------------------------------------------------------
-- 12. Deep dive documents  — junction: report → source documents
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deep_dive_documents (
    report_id   INT NOT NULL REFERENCES deep_dive_reports(id) ON DELETE CASCADE,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq         INT NOT NULL DEFAULT 0,
    PRIMARY KEY (report_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_deep_dive_docs_report ON deep_dive_documents (report_id);
