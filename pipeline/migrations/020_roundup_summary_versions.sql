-- Roundup report bodies move into summary_versions (entity_type 'roundup',
-- entity_id = monthly_roundups.id) so the rich-text editor, version history,
-- and restore work on roundups exactly as they do on briefings and docket
-- states of play. Generation now writes an approved version instead of
-- monthly_roundups.report_md; report_md stays as a legacy read fallback and
-- is no longer written.
--
-- Backfill: every completed roundup with a body becomes version 1, approved.
-- Idempotent — re-running skips roundups that already have any version.
INSERT INTO summary_versions
    (entity_type, entity_id, version, one_line, detailed,
     model_id, is_manual, status, created_by)
SELECT 'roundup', r.id, 1, NULL, r.report_md,
       r.model_id, false, 'approved', COALESCE(r.created_by, 'system')
  FROM monthly_roundups r
 WHERE r.status = 'complete'
   AND COALESCE(r.report_md, '') <> ''
   AND NOT EXISTS (
       SELECT 1 FROM summary_versions sv
        WHERE sv.entity_type = 'roundup' AND sv.entity_id = r.id
   );
