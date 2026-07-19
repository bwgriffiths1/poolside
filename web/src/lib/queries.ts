// Central query-key factory + thin hooks for server state shared across
// screens. Keys live here so useQuery sites and invalidation sites can't
// drift apart, and the hooks bundle the queryFn so no component can
// subscribe to a key without also knowing how to fill it (the old
// Overview bug: a bare `useQuery({queryKey: ["me"]})` with no queryFn sat
// permanently pending unless AppShell happened to prime the cache first).

import { useQuery } from "@tanstack/react-query";
import { api, type SummarizeMode } from "./api";

type SummaryEntity = "meeting" | "agenda_item";

export const qk = {
  me: ["me"] as const,

  // "meetings" prefixes every list variant so one invalidation catches all.
  meetings: ["meetings"] as const,
  meetingsAll: ["meetings", { all: true }] as const,
  meetingsWindow: (past_days: number, future_days: number) =>
    ["meetings", { past_days, future_days }] as const,

  // Invalidation-only prefixes: every meeting detail / every briefing.
  allMeetingDetails: ["meeting"] as const,
  allBriefings: ["briefing"] as const,

  meeting: (id: number) => ["meeting", id] as const,
  meetingDocs: (id: number) => ["meeting-docs", id] as const,
  briefing: (id: number) => ["briefing", id] as const,
  approval: (id: number) => ["approval", id] as const,
  attachments: (id: number) => ["attachments", id] as const,
  shareTokens: (id: number) => ["share-tokens", id] as const,
  watch: (id: number) => ["watch", id] as const,
  job: (id: number | null) => ["job", id] as const,
  summarizeEstimate: (id: number, mode: SummarizeMode) =>
    ["summarize-estimate", id, mode] as const,

  summary: (entity: SummaryEntity, id: number) =>
    ["summary", entity, id] as const,
  summaryVersions: (entity: SummaryEntity, id: number) =>
    ["summary-versions", entity, id] as const,

  notificationsList: ["notifications-list"] as const,
  notificationsUnread: ["notifications-unread-count"] as const,

  roundups: ["roundups"] as const,
  roundup: (id: number) => ["roundup", id] as const,

  deepDives: ["deep-dives"] as const,
  deepDive: (id: number) => ["deep-dive", id] as const,

  initiatives: ["initiatives"] as const,
  initiative: (code: string) => ["initiative", code] as const,

  venues: ["venues"] as const,
  scheduler: ["scheduler"] as const,
  appConfig: ["app-config"] as const,
  ingestJobs: ["ingest-jobs"] as const,
  usageDashboard: ["usage-dashboard"] as const,
  userTokens: ["user-tokens"] as const,

  promptIndex: ["prompt-index"] as const,
  prompt: (slug: string) => ["prompt", slug] as const,
  modelConfig: ["model-config"] as const,

  searchTags: ["search-tags"] as const,
  publicShare: (token: string) => ["public-share", token] as const,
  tokenPreview: (token: string) => ["token-preview", token] as const,
};

// ── Thin hooks for the queries used from more than one screen ─────────────

export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: api.me, retry: false });
}

/** The shared "everything" meeting list (10y back, 1y forward) used by
 *  Meetings, Briefings, Search and the command palette. */
export function useMeetingsAll() {
  return useQuery({
    queryKey: qk.meetingsAll,
    queryFn: () => api.meetings({ past_days: 3650, future_days: 365 }),
  });
}

export function useMeeting(id: number) {
  return useQuery({ queryKey: qk.meeting(id), queryFn: () => api.meeting(id) });
}

export function useMeetingDocs(id: number) {
  return useQuery({
    queryKey: qk.meetingDocs(id),
    queryFn: () => api.meetingDocuments(id),
  });
}

export function useBriefing(id: number) {
  return useQuery({
    queryKey: qk.briefing(id),
    queryFn: () => api.briefing(id),
    retry: false,
  });
}
