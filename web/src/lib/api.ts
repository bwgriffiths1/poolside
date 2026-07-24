// Thin REST client for the FastAPI backend.
// Falls back to fixtures for list/aggregate endpoints (empty lists when API
// is down). Per-id detail endpoints (meeting/:id, briefing/:id) propagate
// errors up to react-query so the route component renders its own
// "not found" / "no briefing" empty state — no fictional data ever leaks in.

import type {
  Attachment,
  Briefing,
  CurrentUser,
  IngestJob,
  MeetingDetail,
  MeetingListItem,
  Role,
  Roundup,
  RoundupMonth,
} from "../types";
import { MEETINGS, RECENT_INGESTS } from "./fixtures";

const BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES === "true";

async function get<T>(path: string, fallback?: () => T): Promise<T> {
  if (USE_FIXTURES && fallback) return fallback();
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { credentials: "include" });
  } catch (err) {
    // Network-level failure only — API down, offline.
    if (fallback) {
      console.warn(`[api] ${path} failed, using fixture:`, err);
      return fallback();
    }
    throw err;
  }
  if (res.status === 401) {
    // Never mask an expired session behind a fixture fallback. The error
    // propagates to the QueryClient's global handlers (main.tsx), which
    // re-check /me so AppShell's isError gate redirects to /login — the one
    // place that owns that redirect (this is a HashRouter app; an api-level
    // pathname check can never work here).
    throw new Error("401 Unauthorized");
  }
  if (!res.ok) {
    if (fallback) {
      console.warn(`[api] ${path} returned ${res.status}, using fixture`);
      return fallback();
    }
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
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

  searchSummaries: async (
    q: string,
    opts: {
      limit?: number;
      from_date?: string | null;
      to_date?: string | null;
      type_short?: string | null;
      tag?: string | null;
      presenter?: string | null;
      status?: "approved" | "draft" | null;
    } = {},
  ): Promise<SummarySearchHit[]> => {
    if (!q.trim()) return [];
    const qs = new URLSearchParams({ q: q.trim() });
    if (opts.limit) qs.set("limit", String(opts.limit));
    if (opts.from_date) qs.set("from_date", opts.from_date);
    if (opts.to_date) qs.set("to_date", opts.to_date);
    if (opts.type_short) qs.set("type_short", opts.type_short);
    if (opts.tag) qs.set("tag", opts.tag);
    if (opts.presenter) qs.set("presenter", opts.presenter);
    if (opts.status) qs.set("status", opts.status);
    const res = await fetch(`${BASE}/search/summaries?${qs}`, {
      credentials: "include",
    });
    if (res.status === 401) throw new Error("401 Unauthorized");
    if (!res.ok) return [];
    return res.json();
  },

  listSearchTags: () =>
    get<{ name: string; tag_type: string }[]>(`/search/tags`, () => []),

  usageDashboard: () =>
    get<UsageDashboard>("/admin/usage", () => ({
      this_month: { cost_usd: 0, input_tokens: 0, output_tokens: 0, jobs: 0 },
      last_month: { cost_usd: 0, input_tokens: 0, output_tokens: 0, jobs: 0 },
      by_committee_this_month: [],
      trailing_six_months: [],
      month_label: "",
    })),

  pruneImages: (): Promise<{
    deleted: number;
    freed_bytes: number;
    stats: ImageStorageStats;
  }> => postJson(`/admin/images/prune`, {}),

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
    meeting_id: number,
    mode: SummarizeMode = "all"
  ): Promise<{
    job_id: number;
    already_running: boolean;
    mode?: SummarizeMode;
    estimated_cost_usd: number | null;
    estimated_input_tokens: number | null;
    estimated_output_tokens: number | null;
  }> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}/summarize`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
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

  estimateSummarize: (meeting_id: number, mode: SummarizeMode = "all") =>
    get<SummarizeEstimate>(
      `/meetings/${meeting_id}/summarize/estimate?mode=${mode}`
    ),

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

  // ── Danger zone ──────────────────────────────────────────────────────
  deleteMeeting: async (meeting_id: number): Promise<{ deleted: boolean }> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  deleteAllDocuments: async (
    meeting_id: number,
  ): Promise<{ removed_documents: number }> => {
    const res = await fetch(`${BASE}/meetings/${meeting_id}/documents`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  // ── User administration ──────────────────────────────────────────────
  listUsers: () => get<AppUser[]>(`/admin/users`, () => []),
  updateUser: async (
    id: number,
    patch: { role?: Role; is_active?: boolean },
  ): Promise<AppUser> => {
    const res = await fetch(`${BASE}/admin/users/${id}`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      // Surface the server detail — the guard rails ("no active admins
      // left", "can't change your own role") are the interesting errors.
      const data = await res.json().catch(() => null);
      throw new Error(data?.detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  },

  listAudit: (params?: { limit?: number; before_id?: number; user?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.before_id) q.set("before_id", String(params.before_id));
    if (params?.user) q.set("user", params.user);
    const qs = q.toString();
    return get<AuditPage>(`/admin/audit${qs ? `?${qs}` : ""}`);
  },

  // ── Invites + password resets ────────────────────────────────────────
  listUserTokens: (purpose?: "invite" | "password_reset") =>
    get<UserTokenRow[]>(
      purpose ? `/admin/user-tokens?purpose=${purpose}` : `/admin/user-tokens`,
      () => [],
    ),
  createInvite: async (body: {
    email: string;
    name: string;
    role?: Role;
    expires_days?: number | null;
  }): Promise<UserTokenCreated> => {
    const res = await fetch(`${BASE}/admin/invites`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(data?.detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  },
  createPasswordReset: async (
    email: string,
    expires_days?: number | null,
  ): Promise<UserTokenCreated> => {
    const res = await fetch(`${BASE}/admin/password-resets`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, expires_days }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(data?.detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  },
  revokeUserToken: async (id: number): Promise<{ revoked: boolean }> => {
    const res = await fetch(`${BASE}/admin/user-tokens/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  publicTokenPreview: async (token: string): Promise<PublicTokenPreview> => {
    const res = await fetch(`${BASE}/public/user-tokens/${encodeURIComponent(token)}`);
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(data?.detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  },
  // ── Initiatives ──────────────────────────────────────────────────────
  listInitiatives: () =>
    get<InitiativeSummary[]>(`/initiatives`, () => []),
  getInitiative: (code: string) =>
    get<InitiativeDetail>(`/initiatives/${encodeURIComponent(code)}`),
  generateInitiativeBrief: (code: string) =>
    postJson<{ code: string; brief: InitiativeBrief | null }>(
      `/initiatives/${encodeURIComponent(code)}/brief`,
      {},
    ),

  publicTokenAccept: async (
    token: string,
    password: string,
  ): Promise<{ ok: boolean; purpose: string; email: string }> => {
    const res = await fetch(
      `${BASE}/public/user-tokens/${encodeURIComponent(token)}/accept`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(data?.detail || `${res.status} ${res.statusText}`);
    }
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
  getSummary: (entity_type: SummaryEntityType, entity_id: number) =>
    get<SummaryPayload>(`/summaries/${entity_type}/${entity_id}`),
  saveSummary: (
    entity_type: SummaryEntityType,
    entity_id: number,
    body: { one_line?: string; detailed: string }
  ) => mutate(`/summaries/${entity_type}/${entity_id}`, "PUT", body),

  listSummaryVersions: (
    entity_type: SummaryEntityType,
    entity_id: number
  ) =>
    get<SummaryVersionMeta[]>(
      `/summaries/${entity_type}/${entity_id}/versions`
    ),

  getSummaryVersion: (
    entity_type: SummaryEntityType,
    entity_id: number,
    version_id: number
  ) =>
    get<SummaryVersionFull>(
      `/summaries/${entity_type}/${entity_id}/versions/${version_id}`
    ),

  restoreSummaryVersion: (
    entity_type: SummaryEntityType,
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

  // -- Meeting attachments (Files portal) ----------------------------------

  listAttachments: (meeting_id: number): Promise<{ attachments: Attachment[] }> =>
    get(`/meetings/${meeting_id}/attachments`, () => ({ attachments: [] })),

  uploadAttachment: async (
    meeting_id: number,
    file: File,
    note?: string,
  ): Promise<Attachment> => {
    const data_b64 = await fileToBase64(file);
    const res = await fetch(`${BASE}/meetings/${meeting_id}/attachments`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name,
        mime_type: file.type || "application/octet-stream",
        data_b64,
        note: note || undefined,
      }),
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const j = await res.json();
        if (j?.detail) detail = j.detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json();
  },

  deleteAttachment: async (attachment_id: number): Promise<void> => {
    const res = await fetch(`${BASE}/attachments/${attachment_id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  },

  downloadAttachment: async (att: {
    id: number;
    filename: string;
  }): Promise<void> => {
    const res = await fetch(`${BASE}/attachments/${att.id}/download`, {
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = att.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  // -- Agenda-item materials (attach a memo to a section for summarizing) ---

  addItemMaterialUrl: async (
    item_id: number,
    url: string,
    filename?: string,
  ): Promise<MaterialResult> =>
    postJson(`/agenda-items/${item_id}/materials`, {
      url,
      filename: filename || undefined,
    }),

  addItemMaterialFile: async (
    item_id: number,
    file: File,
  ): Promise<MaterialResult> => {
    const data_b64 = await fileToBase64(file);
    return postJson(`/agenda-items/${item_id}/materials`, {
      filename: file.name,
      mime_type: file.type || "application/octet-stream",
      data_b64,
    });
  },

  deleteDocument: async (doc_id: number): Promise<void> => {
    const res = await fetch(`${BASE}/documents/${doc_id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  },

  downloadDocumentFile: async (doc: {
    id: number;
    filename: string;
  }): Promise<void> => {
    const res = await fetch(`${BASE}/documents/${doc.id}/file`, {
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = doc.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  // -- Monthly roundups (cross-committee state of play) ---------------------

  roundups: (venue = "ISO-NE") =>
    get<RoundupMonth[]>(
      `/roundups?venue=${encodeURIComponent(venue)}`,
      () => [],
    ),

  roundup: (id: number) => get<Roundup>(`/roundups/${id}`),

  generateRoundup: (venue: string, month: string): Promise<Roundup> =>
    postJson(`/roundups/generate`, { venue, month }),

  deleteRoundup: async (id: number): Promise<void> => {
    const res = await fetch(`${BASE}/roundups/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  },

  // ── My preferences ───────────────────────────────────────────────────
  getMyPrefs: () => get<MyPrefs>(`/me/prefs`),

  updateMyPrefs: async (body: {
    email_prefs: Partial<Record<string, boolean>>;
  }): Promise<MyPrefs> => {
    const res = await fetch(`${BASE}/me/prefs`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as MyPrefs;
  },

  // ── Ask Poolside ─────────────────────────────────────────────────────
  ask: (body: {
    question: string;
    type_short?: string;
    from_date?: string;
    to_date?: string;
  }): Promise<AskResponse> => postJson(`/ask`, body),

  // ── Deep dives ───────────────────────────────────────────────────────
  listDeepDives: () => get<DeepDive[]>(`/deep-dives`, () => []),

  deepDive: (id: number) => get<DeepDive>(`/deep-dives/${id}`),

  createDeepDive: (body: {
    title: string;
    document_ids: number[];
    max_images?: number;
    comparison_mode?: boolean;
  }): Promise<DeepDive> => postJson(`/deep-dives`, body),

  rerunDeepDive: (id: number): Promise<DeepDive> =>
    postJson(`/deep-dives/${id}/rerun`, {}),

  deleteDeepDive: async (id: number): Promise<void> => {
    const res = await fetch(`${BASE}/deep-dives/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  },

  // ── FERC eLibrary dockets ────────────────────────────────────────────
  dockets: () => get<DocketListItem[]>(`/dockets`, () => []),

  docket: (id: number) => get<DocketDetail>(`/dockets/${id}`),

  addDocket: (body: {
    docket_number: string;
    title?: string;
  }): Promise<{ docket: DocketListItem; job: DocketJobStart | null }> =>
    postJson(`/dockets`, body),

  updateDocket: (
    id: number,
    body: { title?: string; notes?: string; auto_refresh?: boolean }
  ): Promise<DocketListItem> => {
    return (async () => {
      const res = await fetch(`${BASE}/dockets/${id}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return (await res.json()) as DocketListItem;
    })();
  },

  deleteDocket: async (id: number): Promise<void> => {
    const res = await fetch(`${BASE}/dockets/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  },

  syncDocket: (id: number): Promise<DocketJobStart> =>
    postJson(`/dockets/${id}/sync`, {}),

  generateStateOfPlay: (id: number): Promise<DocketJobStart> =>
    postJson(`/dockets/${id}/state-of-play`, {}),

  getDocketActiveJob: (docketId: number) =>
    get<DocketJob | null>(`/dockets/${docketId}/active-job`),

  getDocketJob: (jobId: number) => get<DocketJob>(`/docket-jobs/${jobId}`),

  cancelDocketJob: (
    jobId: number
  ): Promise<{ job_id: number; status: string; changed: boolean }> =>
    postJson(`/docket-jobs/${jobId}/cancel`, {}),
  // (The docket .docx downloads through a plain <a href> on the docket
  // page — same-origin GET with Content-Disposition; no blob dance.)
};

export interface MaterialResult {
  document: {
    id: number;
    filename: string;
    type: string;
    source_url?: string | null;
    manual: boolean;
  };
  extracted_chars: number;
  summarizable: boolean;
}

/** POST JSON and surface the server's `detail` message on failure. */
async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

/** Read a File as a bare base64 string (strips the data: URL prefix). */
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export type SummaryEntityType =
  | "meeting"
  | "agenda_item"
  | "docket"
  | "docket_filing";

// ── FERC eLibrary dockets ──────────────────────────────────────────────

export interface DocketListItem {
  id: number;
  docket_number: string;
  title: string | null;
  notes: string | null;
  auto_refresh: boolean;
  last_crawled_at: string | null;
  created_by: string | null;
  created_at: string | null;
  filing_count?: number;
  intervenor_count?: number;
  latest_filed_date?: string | null;
  brief_status?: string | null;
  brief_generated_at?: string | null;
}

export interface DocketFilingFile {
  id: number;
  file_desc: string | null;
  orig_file_name: string | null;
  file_type: string | null;
  file_size: number | null;
  page_count: number | null;
  included: boolean;
  has_content: boolean;
}

export interface DocketFilingParty {
  type: "AUTHOR" | "AGENT" | string;
  org: string;
}

export interface DocketFiling {
  id: number;
  accession_number: string;
  category: string | null;
  document_class: string | null;
  document_type: string | null;
  description: string | null;
  sub_docket: string | null;
  filed_date: string | null;
  issued_date: string | null;
  posted_date: string | null;
  comments_due_date: string | null;
  response_due_date: string | null;
  ferc_cite: string | null;
  filing_parties: DocketFilingParty[];
  treatment: "full" | "brief" | "skip";
  is_docless: boolean;
  role: "initial" | "order" | null;
  summary_one_line: string | null;
  summary_detailed: string | null;
  summary_status: string | null;
  elibrary_url: string;
  filelist_url: string;
  files: DocketFilingFile[];
}

export interface DocketBrief {
  summary_id: number;
  version: number | null;
  status: string | null;
  detailed: string | null;
  is_manual: boolean;
  created_at: string | null;
  created_by: string | null;
  stale: boolean;
}

export interface DocketDetail extends DocketListItem {
  brief: DocketBrief | null;
  filings: DocketFiling[];
  intervenors: { org: string; date: string | null }[];
}

export interface DocketJob {
  id: number;
  docket_id: number;
  mode: "sync" | "brief";
  status: string;
  progress_text: string;
  filings_found: number;
  filings_summarized: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_by: string | null;
}

export interface DocketJobStart {
  job_id: number;
  already_running: boolean;
  mode?: string;
}

export interface SummaryPayload {
  entity_type: SummaryEntityType;
  entity_id: number;
  meeting_id: number | null;
  docket_id?: number | null;
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

/** Which slice of the summarize pipeline to run.
 *  - "all"      — full re-run (Level 1 + 2 + 3)
 *  - "missing"  — only items lacking summaries; briefing rebuilds if anything new
 *  - "briefing" — reuse existing item summaries, regenerate only the briefing
 */
export type SummarizeMode = "all" | "missing" | "briefing";

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

export interface UserTokenRow {
  id: number;
  token: string;
  purpose: "invite" | "password_reset";
  email: string;
  name: string | null;
  role: Role | null;
  created_by: number | null;
  created_at: string;
  expires_at: string | null;
  used_at: string | null;
  status?: "active" | "expired" | "used";
}

/** Create responses add the always-copyable URL and whether an email was
 *  queued (queued, not delivered — sends are best-effort, off-thread). */
export type UserTokenCreated = UserTokenRow & {
  emailed: boolean;
  accept_url: string;
};

export interface PublicTokenPreview {
  purpose: "invite" | "password_reset";
  email: string;
  name: string | null;
  role: Role | null;
  expires_at: string | null;
}

export interface AppUser {
  id: number;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  auth_provider: string;
  created_at: string | null;
  last_login: string | null;
}

export interface AuditItem {
  id: number;
  label: string;
  user_email: string;
  method: string;
  path: string;
  route: string | null;
  path_params: Record<string, string>;
  query: string | null;
  status: number;
  duration_ms: number | null;
  created_at: string;
}

export interface AuditPage {
  items: AuditItem[];
  next_before_id: number | null;
}

export interface InitiativeSummary {
  tag_id: number;
  code: string;
  description: string | null;
  item_count: number;
  meeting_count: number;
  latest_meeting_date: string | null;
  brief_status: InitiativeBriefStatus | null;
}

export type InitiativeBriefStatus = "draft" | "generating" | "complete" | "error";

export interface MyPrefs {
  email_prefs: Record<string, boolean>;
  mail_configured: boolean;
}

export interface AskSource {
  n: number;
  entity_type: "meeting" | "agenda_item";
  entity_id: number;
  meeting_id: number;
  meeting_title: string | null;
  meeting_date: string;
  venue: string;
  type_short: string;
  item_id: string | null;
  item_title: string | null;
  snippet: string; // pre-escaped HTML with <b> highlights
}

export interface AskResponse {
  question: string;
  answer_md: string;
  sources: AskSource[];
  model_id: string | null;
  cost_usd: number | null;
}

export interface DeepDiveSource {
  document_id: number;
  filename: string;
  file_type: string | null;
  meeting_id: number;
  meeting_date: string;
  end_date: string | null;
  meeting_title: string | null;
  type_short: string;
  type_name: string;
  venue: string;
}

export interface DeepDive {
  id: number;
  title: string;
  status: "draft" | "generating" | "complete" | "error";
  model_id: string | null;
  config: { max_images?: number; comparison_mode?: boolean };
  error_message: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  report_md?: string | null; // omitted in list responses
  sources: DeepDiveSource[];
  source_count: number;
}

export interface InitiativeBrief {
  status: InitiativeBriefStatus;
  brief_md: string | null;
  error_message: string | null;
  model_id: string | null;
  cost_usd: number | null;
  generated_at: string | null;
  source_item_count: number | null;
  source_latest_meeting_date: string | null;
  stale: boolean;
}

export interface InitiativeItem {
  meeting_id: number;
  meeting_date: string;
  meeting_title: string | null;
  venue: string;
  type_short: string;
  type_name: string;
  item_id: string | null;
  item_title: string | null;
  presenter: string | null;
  organization: string | null;
  vote_status: string | null;
  summary_version: number | null;
  summary_status: string | null;
  summary_snippet: string;
}

export interface InitiativeDetail {
  code: string;
  description: string | null;
  items: InitiativeItem[];
  item_count: number;
  meeting_count: number;
  brief: InitiativeBrief | null;
}

export interface PublicShareResponse {
  venue: string;
  type_short: string;
  type_name: string;
  meeting_date: string;
  external_id: string;
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
  presenter: string | null;
  organization: string | null;
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

export interface ImageStorageStats {
  stored: number;
  stored_bytes: number;
  referenced: number;
  unreferenced_bytes: number;
  last_prune: {
    at: string;
    deleted: number;
    freed_bytes: number;
    by?: string;
  } | null;
}

export interface UsageDashboard {
  this_month: UsageTotals;
  last_month: UsageTotals;
  by_committee_this_month: UsageByCommittee[];
  trailing_six_months: UsageMonthlyPoint[];
  month_label: string;
  images?: ImageStorageStats;
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
