-- Migration 018: PJM venue + CIFP-RBP committee
--
-- First non-ISO-NE venue with a live scraper (pipeline/pjm_scraper.py).
-- active=true is load-bearing: discovery._create_discovered_meeting resolves
-- venues via db.get_venues(), whose default is active_only=True — an
-- inactive seed would make every PJM discover fail with "Unknown venue".
--
-- PJM meetings are hidden from the main web UI for now (the NYISO-style
-- filter in web/src/lib/api.ts); the demo surface is /pjm.

INSERT INTO venues (name, short_name, website, active) VALUES
    ('PJM Interconnection', 'PJM', 'https://www.pjm.com', true)
ON CONFLICT (short_name) DO NOTHING;

INSERT INTO meeting_types (venue_id, name, short_name) VALUES
    ((SELECT id FROM venues WHERE short_name = 'PJM'),
     'Critical Issue Fast Path - Reliability Backstop Procurement', 'CIFP-RBP')
ON CONFLICT (venue_id, short_name) DO NOTHING;
