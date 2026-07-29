-- Migration 019: track every venue event ID belonging to a meeting.
--
-- ISO-NE posts one calendar event per DAY of a multi-day meeting, each day
-- with its own eventId, all answering the documents API with the same
-- materials. Discovery matched on the single external_id (the first day's
-- event), so a mid-meeting scrape — whose first day had already passed and
-- been filtered out — produced a "new" primary event ID and a duplicate
-- meeting row (July 7–9 2026 MC became rows 7–9, 8–9 and 9, event IDs
-- 160098/160099/160100). Storing the full ID set lets discovery recognize
-- any day's event as the same meeting.

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS external_ids TEXT[] NOT NULL DEFAULT '{}';

UPDATE meetings
   SET external_ids = ARRAY[external_id]
 WHERE external_id IS NOT NULL
   AND external_ids = '{}';

CREATE INDEX IF NOT EXISTS idx_meetings_external_ids
    ON meetings USING GIN (external_ids);
