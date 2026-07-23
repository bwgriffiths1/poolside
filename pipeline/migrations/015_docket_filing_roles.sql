-- Migration 015: docket filing roles — the two anchor documents
--
-- A docket's story has two load-bearing documents: the INITIAL filing
-- (the proposal everything else reacts to) and FERC's ORDERS (how the
-- Commission decides). Both get flagged so they can carry dedicated,
-- deeper prompts and richer treatment in the UI / Word export / state
-- of play; the responsive middle (comments, answers) stays on the
-- shorter generic prompts.
--
--   role: 'initial' | 'order' | NULL (responsive/procedural)
--
-- Assigned deterministically on every sync (pipeline/docket_ingest.py
-- _assign_roles): orders by documentClass, initial = the earliest-filed
-- Application/Petition/Request on the docket.

ALTER TABLE docket_filings ADD COLUMN IF NOT EXISTS role TEXT;

CREATE INDEX IF NOT EXISTS idx_docket_filings_role
    ON docket_filings (docket_id, role) WHERE role IS NOT NULL;
