-- Migration 017: docket venue/party prefix on the header
--
-- The cover subtitle on a docket briefing starts as the tagline (title).
-- FERC dockets carry no stored venue — ISO-NE is only the filer on some of
-- them — so a short, editor-set prefix lets the header read "ISO-NE: <title>"
-- (or "NextEra: <title>", etc.) without guessing from the filing parties.
--
-- Rendered in pipeline/docket_docx.py and the web docket header; blank/NULL
-- leaves the tagline untouched.

ALTER TABLE dockets ADD COLUMN IF NOT EXISTS party_label TEXT;
