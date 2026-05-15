// Thin REST client for the FastAPI backend.
// Falls back to fixtures for list/aggregate endpoints (empty lists when API
// is down). Per-id detail endpoints (meeting/:id, briefing/:id) propagate
// errors up to react-query so the route component renders its own
// "not found" / "no briefing" empty state — no fictional data ever leaks in.

import type {
  Briefing,
  CurrentUser,
  IngestJob,
  MeetingDetail,
  MeetingListItem,
} from "../types";
import { MEETINGS, RECENT_INGESTS } from "./fixtures";

const BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES === "true";

async function get<T>(path: string, fallback?: () => T): Promise<T> {
  if (USE_FIXTURES && fallback) return fallback();
  try {
    const res = await fetch(`${BASE}${path}`, { credentials: "include" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch (err) {
    if (fallback) {
      console.warn(`[api] ${path} failed, using fixture:`, err);
      return fallback();
    }
    throw err;
  }
}

export const api = {
  me: () =>
    get<CurrentUser>("/me", () => ({
      name: "Ben Griffiths",
      email: "ben@poolside.io",
      initials: "BG",
    })),

  meetings: (params?: { past_days?: number; future_days?: number; venue?: string }) => {
    const qs = new URLSearchParams();
    if (params?.past_days != null) qs.set("past_days", String(params.past_days));
    if (params?.future_days != null) qs.set("future_days", String(params.future_days));
    if (params?.venue) qs.set("venue", params.venue);
    const tail = qs.toString() ? `?${qs}` : "";
    return get<MeetingListItem[]>(`/meetings${tail}`, () => MEETINGS);
  },

  // No fallback — if the API can't return a specific meeting / briefing,
  // the error propagates and the route component renders its empty state.
  meeting: (id: number) => get<MeetingDetail>(`/meetings/${id}`),

  briefing: (id: number) => get<Briefing>(`/meetings/${id}/briefing`),

  ingestJobs: () =>
    get<IngestJob[]>(`/ingest/jobs`, () => RECENT_INGESTS),

  meetingDocuments: (id: number) =>
    get<MeetingDocuments>(`/meetings/${id}/documents`, () => ({
      unassigned: [],
      by_item: {},
      ignored: [],
    })),

  assignDoc: (item_id: number, doc_id: number) =>
    mutate(`/agenda-items/${item_id}/documents/${doc_id}`, "POST"),

  reassignDoc: (doc_id: number, item_id: number, meeting_id: number) =>
    mutate(`/documents/${doc_id}/item`, "PATCH", { item_id, meeting_id }),

  unassignDoc: (item_id: number, doc_id: number, meeting_id: number) =>
    mutate(
      `/agenda-items/${item_id}/documents/${doc_id}?meeting_id=${meeting_id}`,
      "DELETE"
    ),

  setDocIgnored: (doc_id: number, ignored: boolean) =>
    mutate(`/documents/${doc_id}`, "PATCH", { ignored }),

  refreshMeeting: (meeting_id: number) =>
    mutate(`/admin/refresh-materials/${meeting_id}`, "POST"),

  bumpLifecycle: (meeting_id: number) =>
    mutate(`/admin/bump-lifecycle/${meeting_id}`, "POST"),

  venues: () =>
    get<VenueWithScrape[]>("/admin/venues", () => [
      { id: 1, short_name: "ISO-NE", name: "ISO New England", last_scraped_at: null },
      { id: 2, short_name: "NYISO", name: "New York ISO", last_scraped_at: null },
    ]),

  schedulerStatus: () =>
    get<SchedulerStatus>("/admin/scheduler", () => ({ running: false, jobs: [] })),

  triggerDiscover: () => mutate(`/admin/discover`, "POST"),

  createAgendaItem: (
    meeting_id: number,
    body: {
      title: string;
      item_id?: string;
      presenter?: string;
      org?: string;
      time_slot?: string;
      vote_status?: string;
      seq?: number;
    }
  ) => mutate(`/meetings/${meeting_id}/agenda-items`, "POST", body),

  updateAgendaItem: (
    row_id: number,
    body: {
      title?: string;
      item_id?: string;
      presenter?: string;
      org?: string;
      time_slot?: string;
      vote_status?: string;
    }
  ) => mutate(`/agenda-items/${row_id}`, "PATCH", body),

  deleteAgendaItem: (row_id: number) =>
    mutate(`/agenda-items/${row_id}`, "DELETE"),

  // ── Prompt library ───────────────────────────────────────────────────────
  prompts: () => get<PromptIndex>(`/prompts`),
  prompt: (slug: string) => get<PromptContent>(`/prompts/${slug}`),
  savePrompt: (slug: string, content: string) =>
    mutate(`/prompts/${slug}`, "PUT", { content }),
  modelConfig: () => get<ModelConfig>(`/model-config`),
  saveModelConfig: (cfg: Partial<ModelConfig>) =>
    mutate(`/model-config`, "PUT", cfg),

  // ── Rich-text summary editor ────────────────────────────────────────────
  getSummary: (entity_type: "meeting" | "agenda_item", entity_id: number) =>
    get<SummaryPayload>(`/summaries/${entity_type}/${entity_id}`),
  saveSummary: (
    entity_type: "meeting" | "agenda_item",
    entity_id: number,
    body: { one_line?: string; detailed: string }
  ) => mutate(`/summaries/${entity_type}/${entity_id}`, "PUT", body),

  uploadEditorImage: async (body: {
    entity_type: "meeting" | "agenda_item";
    entity_id: number;
    image_b64: string;
    mime_type?: string;
    filename?: string;
  }): Promise<{ id: number; url: string; size: number }> => {
    const res = await fetch(`${BASE}/editor-images`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
};

export interface SummaryPayload {
  entity_type: "meeting" | "agenda_item";
  entity_id: number;
  meeting_id: number;
  parent_label: string;
  one_line: string;
  detailed: string;
  version: number | null;
  status: string | null;
  is_manual: boolean;
  created_at: string | null;
  created_by: string | null;
}

export interface PromptMeta {
  slug: string;
  exists: boolean;
  size: number;
  modified: string | null;
  label?: string;
  hint?: string;
}

export interface VenueCommitteePrompts {
  short_name: string;
  name: string;
  briefing: PromptMeta;
  briefing_detailed: PromptMeta;
  agenda_item: PromptMeta;
}

export interface PromptIndex {
  shared: PromptMeta[];
  pipeline: PromptMeta[];
  venues: {
    venue_short: string;
    venue_name: string;
    venue_slug: string;
    committees: VenueCommitteePrompts[];
  }[];
  extras: PromptMeta[];
}

export interface PromptContent {
  slug: string;
  exists: boolean;
  content: string;
  size?: number;
  modified?: string;
}

export interface ModelConfig {
  document_model: string;
  item_model: string;
  meeting_model: string;
  document_max_tokens: number;
  item_max_tokens: number;
  meeting_max_tokens: number;
}

export interface VenueWithScrape {
  id: number;
  short_name: string;
  name: string;
  last_scraped_at: string | null;
}

export interface SchedulerStatus {
  running: boolean;
  jobs: { id: string; next_run_time: string | null }[];
}

async function mutate(path: string, method: string, body?: unknown): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
}

export interface MeetingDocuments {
  unassigned: DocAssignment[];
  by_item: Record<number, DocAssignment[]>;
  ignored: DocAssignment[];
}

export interface DocAssignment {
  id: number;
  filename: string;
  type: string;
  ignored: boolean;
}
