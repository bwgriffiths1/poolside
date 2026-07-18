-- One active summarize job per meeting, enforced by the database.
--
-- The API's admission check was SELECT-then-INSERT, so two concurrent
-- POSTs could both start daemon threads on the same meeting (double LLM
-- spend, competing summary_versions writes). A partial unique index over
-- the "active" statuses lets the route claim atomically with
-- INSERT ... ON CONFLICT DO NOTHING.

-- Settle any historical duplicate active rows so the index can build:
-- keep the newest active row per meeting, fail the rest.
UPDATE summarize_jobs
   SET status = 'failed',
       error = COALESCE(error, 'superseded by a newer job for this meeting'),
       finished_at = NOW()
 WHERE status IN ('queued', 'running', 'cancelling')
   AND id NOT IN (
       SELECT MAX(id) FROM summarize_jobs
        WHERE status IN ('queued', 'running', 'cancelling')
        GROUP BY meeting_id
   );

CREATE UNIQUE INDEX IF NOT EXISTS uq_summarize_jobs_one_active
    ON summarize_jobs (meeting_id)
    WHERE status IN ('queued', 'running', 'cancelling');
