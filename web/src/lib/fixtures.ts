// Empty fallback fixtures used by api.ts when the FastAPI backend is
// unreachable. Real data always comes from the API; these arrays make the
// UI render gracefully (as "no meetings") when the backend is offline.

import type { MeetingListItem, IngestJob } from "../types";

export const MEETINGS: MeetingListItem[] = [];

export const RECENT_INGESTS: IngestJob[] = [];
