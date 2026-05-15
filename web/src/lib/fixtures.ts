// Minimal fixtures used only as a fallback when the FastAPI backend is
// unreachable. The prototype-era fictional meetings (101 + 102–111) have
// been removed — we now always render from the real DB, with empty arrays
// as the offline fallback so nothing fake leaks into the UI.

import type { MeetingListItem, IngestJob } from "../types";

export const TODAY = "2026-05-14";

export const MEETINGS: MeetingListItem[] = [];

export const RECENT_INGESTS: IngestJob[] = [];

export interface ScrapedMeeting {
  venue: string;
  committee: string;
  date: string;
  end?: string;
  source: string;
  status: "new" | "exists";
  docs: number;
}

// SCRAPED_MEETINGS is the hardcoded preview table for the Add Meeting screen's
// "auto-scrape" demo. Real scraping flows through POST /api/admin/discover
// when wired up. This stays as a stand-in until the live preview lands.
export const SCRAPED_MEETINGS: ScrapedMeeting[] = [
  { venue: "ISO-NE", committee: "Markets Committee", date: "2026-06-10", end: "2026-06-11", source: "iso-ne.com", status: "new", docs: 0 },
  { venue: "ISO-NE", committee: "Transmission Committee", date: "2026-05-27", source: "iso-ne.com", status: "new", docs: 0 },
  { venue: "ISO-NE", committee: "Reliability Committee", date: "2026-05-20", source: "iso-ne.com", status: "exists", docs: 18 },
  { venue: "NYISO", committee: "Management Committee", date: "2026-05-28", source: "nyiso.com", status: "new", docs: 0 },
  { venue: "NYISO", committee: "Operating Committee", date: "2026-05-22", source: "nyiso.com", status: "exists", docs: 0 },
  { venue: "NYISO", committee: "Business Issues Comm.", date: "2026-06-11", source: "nyiso.com", status: "new", docs: 0 },
];
