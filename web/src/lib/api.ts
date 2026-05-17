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
  me: () => get<CurrentUser>("/me"),

  login: async (email: string, password: string): Promise<CurrentUser> => {
    const res = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      let detail = "Invalid email or password.";
      try {
        const data = await res.json();
        if (typeof data?.detail === "string") detail = data.detail;
      } catch { /* leave default */ }
      throw new Error(detail);
    }
    return (await res.json()) as CurrentUser;
  },

  logout: () => mutate(`/auth/logout`, "POST"),

  meetings: async (params?: { past_days?: number; future_days?: number; venue?: string }) => {
    const qs = new URLSearchParams();
    if (params?.past_days != null) qs.set("past_days", String(params.past_days));
    if (params?.future_days != null) qs.set("future_days", String(params.future_days));
    if (params?.venue) qs.set("venue", params.venue);
    const tail = qs.toString() ? `?${qs}` : "";
    const all = await get<MeetingListItem[]>(`/meetings${tail}`, () => MEETINGS);
    // NYISO is intentionally hidden from the Vite UI for now (see plan).
    return all.filter((m) => m.venue !== "NYISO");
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

  cleanupZipExpansion: async (
    meeting_id: number
  ): Promise<{
    meeting_id: number;
    deleted_children: number;
    un_ignored_zips: number;
  }> => {
    const res = await fetch(
      `${BASE}/admin/cleanup-zip-expansion/${meeting_id}`,
      { method: "POST", credentials: "include" },
    );
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  refreshAll: async (): Promise<{
    count: number;
    refreshed: Array<{ meeting_id: number; error?: string }>;
  }> => {
    const res = await fetch(`${BASE}/admin/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  bumpLifecycle: (meeting_id: number) =>
    mutate(`/admin/bump-lifecycle/${meeting_id}`, "POST"),

  venues: async () => {
    const all = await get<VenueWithScrape[]>("/admin/venues", () => [
      { id: 1, short_name: "ISO-NE", name: "ISO New England", last_scraped_at: null },
    ]);
    return all.filter((v) => v.short_name !== "NYISO");
  },

  schedulerStatus: () =>
    get<SchedulerStatus>("/admin/scheduler", () => ({ running: false, jobs: [] })),

  searchSummaries: async (q: string): Promise<SummarySearchHit[]> => {
    if (!q.trim()) return [];
    const res = await fetch(
      `${BASE}/search/summaries?q=${encodeURIComponent(q.trim())}`,
      { credentials: "include" },
    );
    if (!res.ok) return [];
    return res.json();
  },

  usageDashboard: () =>
    get<UsageDashboard>("/admin/usage", () => ({
      this_month: { cost_usd: 0, input_tokens: 0, output_tokens: 0, jobs: 0 },
      last_month: { cost_usd: 0, input_tokens: 0, output_tokens: 0, jobs: 0 },
      by_committee_this_month: [],
      trailing_six_months: [],
      month_label: "",
    })),

  triggerDiscover: async (): Promise<{ discovered: Record<string, number> }> => {
    const res = await fetch(`${BASE}/admin/discover`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  ingestByUrl: async (
    body: { url: string; committee_short?: string }
  ): Promise<IngestByUrlResult> => {
    const res = await fetch(`${BASE}/admin/ingest-by-url`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const data = await res.json();
        if (typeof data?.detail === "string") detail = data.detail;
      } catch { /* keep default */ }
      throw new Error(detail);
    }
    return res.json();
  },

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

  resummarizeAgendaItem: async (
    row_id: number
  ): Promise<{ ok: boolean; model?: string; n_inputs?: number; reason?: string | null }> => {
    const res = await fetch(`${BASE}/agenda-items/${row_id}/resummarize`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  startSummarize: async (
    meeting_id: number
  ): Promise<{
    job_id: number;
    already_running: boolean;
    estimated_cost_usd: number | null;
    estimated_input_tokens: number | null;
    estimated_output_tokens: number | null;
  }> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}/summarize`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const data = await res.json();
        if (typeof data?.detail === "string") detail = data.detail;
      } catch { /* keep default */ }
      throw new Error(detail);
    }
    return res.json();
  },

  estimateSummarize: (meeting_id: number) =>
    get<SummarizeEstimate>(`/meetings/${meeting_id}/summarize/estimate`),

  getJob: (job_id: number) => get<SummarizeJob>(`/jobs/${job_id}`),

  getActiveJob: (meeting_id: number) =>
    get<SummarizeJob | null>(`/meetings/${meeting_id}/active-job`),

  cancelJob: async (
    job_id: number,
  ): Promise<{ job_id: number; status: string; changed: boolean }> => {
    const res = await fetch(`${BASE}/jobs/${job_id}/cancel`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  // ── Notifications ────────────────────────────────────────────────────
  listNotifications: (include_read = false) =>
    get<NotificationRow[]>(
      `/notifications?limit=30&include_read=${include_read}`,
      () => [],
    ),
  unreadCount: () =>
    get<{ count: number }>(`/notifications/unread-count`, () => ({ count: 0 })),
  markNotificationsRead: async (ids?: number[]): Promise<{ marked_read: number }> => {
    const res = await fetch(`${BASE}/notifications/mark-read`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ids && ids.length ? { ids } : {}),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  // ── Watches ──────────────────────────────────────────────────────────
  isWatching: (meeting_id: number) =>
    get<{ watching: boolean }>(`/watches/by-meeting/${meeting_id}`, () => ({
      watching: false,
    })),
  watchMeeting: async (meeting_id: number): Promise<{ watching: boolean }> => {
    const res = await fetch(`${BASE}/watches/by-meeting/${meeting_id}`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  unwatchMeeting: async (meeting_id: number): Promise<{ watching: boolean }> => {
    const res = await fetch(`${BASE}/watches/by-meeting/${meeting_id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  // ── Approval ─────────────────────────────────────────────────────────
  getApproval: (meeting_id: number) =>
    get<BriefingApproval>(`/meetings/${meeting_id}/briefing/approval`),
  approveBriefing: async (meeting_id: number): Promise<BriefingApproval> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}/briefing/approve`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  unapproveBriefing: async (meeting_id: number): Promise<BriefingApproval> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}/briefing/unapprove`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  // ── Share links ──────────────────────────────────────────────────────
  listShareLinks: (meeting_id: number) =>
    get<ShareToken[]>(`/meetings/${meeting_id}/share`, () => []),
  createShareLink: async (
    meeting_id: number,
    expires_days?: number | null,
  ): Promise<ShareToken> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}/share`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expires_days: expires_days ?? null }),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  revokeShareLink: async (token_id: number): Promise<{ revoked: boolean }> => {
    const res = await fetch(`${BASE}/share-tokens/${token_id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  publicShare: async (token: string): Promise<PublicShareResponse> => {
    const res = await fetch(`${BASE}/public/share/${encodeURIComponent(token)}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  // ── Prompt library ───────────────────────────────────────────────────────
  prompts: () => get<PromptIndex>(`/prompts`),
  prompt: (slug: string) => get<PromptContent>(`/prompts/${slug}`),
  savePrompt: (slug: string, content: string) =>
    mutate(`/prompts/${slug}`, "PUT", { content }),
  modelConfig: () => get<ModelConfig>(`/model-config`),
  saveModelConfig: (cfg: Partial<ModelConfig>) =>
    mutate(`/model-config`, "PUT", cfg),

  // ── App settings (config.yaml) ──────────────────────────────────────────
  getConfig: () => get<AppConfig>(`/admin/config`),
  saveConfig: async (payload: AppConfig): Promise<AppConfig> => {
    const res = await fetch(`${BASE}/admin/config`, {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const data = await res.json();
        if (typeof data?.detail === "string") detail = data.detail;
      } catch { /* keep default */ }
      throw new Error(detail);
    }
    return res.json();
  },

  // ── Rich-text summary editor ────────────────────────────────────────────
  getSummary: (entity_type: "meeting" | "agenda_item", entity_id: number) =>
    get<SummaryPayload>(`/summaries/${entity_type}/${entity_id}`),
  saveSummary: (
    entity_type: "meeting" | "agenda_item",
    entity_id: number,
    body: { one_line?: string; detailed: string }
  ) => mutate(`/summaries/${entity_type}/${entity_id}`, "PUT", body),

  listSummaryVersions: (
    entity_type: "meeting" | "agenda_item",
    entity_id: number
  ) =>
    get<SummaryVersionMeta[]>(
      `/summaries/${entity_type}/${entity_id}/versions`
    ),

  getSummaryVersion: (
    entity_type: "meeting" | "agenda_item",
    entity_id: number,
    version_id: number
  ) =>
    get<SummaryVersionFull>(
      `/summaries/${entity_type}/${entity_id}/versions/${version_id}`
    ),

  restoreSummaryVersion: (
    entity_type: "meeting" | "agenda_item",
    entity_id: number,
    version_id: number
  ) =>
    mutate(
      `/summaries/${entity_type}/${entity_id}/versions/${version_id}/restore`,
      "POST"
    ),

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

  downloadBriefingDocx: async (meeting_id: number): Promise<void> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}/briefing.docx`, {
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

    // Prefer the server's Content-Disposition filename when present.
    let filename = `Briefing_${meeting_id}.docx`;
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
    if (m) filename = decodeURIComponent(m[1] || m[2]);

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
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

export interface SummaryVersionMeta {
  id: number;
  version: number;
  status: string;
  is_manual: boolean;
  model_id: string | null;
  created_at: string | null;
  created_by: string | null;
  size: number;
  preview: string;
}

export interface SummaryVersionFull extends SummaryVersionMeta {
  detailed: string;
  one_line: string;
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

export interface AppConfigCommittee {
  name: string;
  short: string;
  url: string;
  active: boolean;
}

export interface AppConfig {
  lookahead_days: number;
  committees: AppConfigCommittee[];
}

export interface IngestByUrlResult {
  meeting_id: number;
  external_id: string;
  committee_short: string;
  docs: number;
  already_existed: boolean;
}

export interface SummarizeEstimateLine {
  level: number;
  item_id: string | null;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface SummarizeCommitteeStats {
  count: number;
  avg_cost_usd: number;
  avg_duration_seconds: number;
}

export interface SummarizeEstimate {
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_cost_usd: number;
  model_breakdown: SummarizeEstimateLine[];
  docs_without_text: number;
  items_planned: number;
  committee_stats?: SummarizeCommitteeStats | null;
}

export type SummarizeJobStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "complete"
  | "failed"
  | "cancelled";

export interface SummarizeJob {
  id: number;
  meeting_id: number;
  status: SummarizeJobStatus;
  progress_text: string;
  level1_done: number;
  level2_done: number;
  level3_done: boolean;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  estimated_cost_usd: number | null;
  estimated_input_tokens: number | null;
  estimated_output_tokens: number | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  created_by: string | null;
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

export interface NotificationRow {
  id: number;
  user_id: number | null;
  kind: string;
  payload: Record<string, unknown>;
  meeting_id: number | null;
  created_at: string;
  read_at: string | null;
}

export interface BriefingApproval {
  version: number | null;
  status: string | null;
  approved_by: string | null;
  approved_at: string | null;
}

export interface ShareToken {
  id: number;
  token: string;
  meeting_id: number;
  created_by: number | null;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface PublicShareResponse {
  venue: string;
  type_short: string;
  type_name: string;
  meeting_date: string;
  briefing: Briefing;
}

export interface SummarySearchHit {
  entity_type: "meeting" | "agenda_item";
  entity_id: number;
  meeting_id: number;
  meeting_title: string;
  meeting_date: string;
  venue: string;
  type_short: string;
  item_id: string | null;
  item_title: string | null;
  snippet: string;
  rank: number;
}

export interface UsageTotals {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  jobs: number;
}

export interface UsageByCommittee {
  venue: string;
  committee: string;
  cost_usd: number;
  jobs: number;
}

export interface UsageMonthlyPoint {
  month: string; // "YYYY-MM"
  cost_usd: number;
  jobs: number;
}

export interface UsageDashboard {
  this_month: UsageTotals;
  last_month: UsageTotals;
  by_committee_this_month: UsageByCommittee[];
  trailing_six_months: UsageMonthlyPoint[];
  month_label: string;
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
