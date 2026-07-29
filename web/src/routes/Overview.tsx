import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Pill } from "../components/Pill";
import { VenueTag, TypeTag } from "../components/Tag";
import { api, type DocketListItem } from "../lib/api";
import { qk, useCan, useMe } from "../lib/queries";
import {
  dayNumber,
  fmtDateRange,
  fmtShortDate,
  formatRel,
  weekdayShort,
} from "../lib/format";
import { toast } from "../lib/toast";
import { addDays, localIso, mondayOf } from "../lib/dates";
import type { MeetingListItem, MeetingStatus } from "../types";

function weekLabel(startIso: string, endIso: string): string {
  const s = new Date(`${startIso}T12:00:00`);
  const e = new Date(`${endIso}T12:00:00`);
  const sTxt = s.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const eTxt =
    s.getMonth() === e.getMonth()
      ? String(e.getDate())
      : e.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `${sTxt} – ${eTxt}`;
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Good evening";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export function Overview() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: me } = useMe();
  const { canEdit, isAdmin } = useCan();
  const firstName = (me?.name || "").split(" ")[0] || "there";

  // 31 back covers the Inbox's 30-day cutoff; 15 forward covers next week
  // (Sunday is 13 days out on a Monday) with a day of slack for the
  // server's UTC "today".
  const { data: meetings = [] } = useQuery({
    queryKey: qk.meetingsWindow(31, 15),
    queryFn: () => api.meetings({ past_days: 31, future_days: 15 }),
  });

  const refreshAll = useMutation({
    // Scrape calendars (discover new meetings) + refresh materials for known
    // upcoming meetings in one click. Both are independent backend calls;
    // we run them in parallel and merge the result into a single alert.
    mutationFn: async () => {
      const [discoverRes, refreshRes] = await Promise.allSettled([
        api.triggerDiscover(),
        api.refreshAll(),
      ]);
      return { discoverRes, refreshRes };
    },
    onSuccess: ({ discoverRes, refreshRes }) => {
      qc.invalidateQueries({ queryKey: qk.meetings });
      qc.invalidateQueries({ queryKey: qk.venues });

      const parts: string[] = [];

      if (discoverRes.status === "fulfilled") {
        const totalNew = Object.values(discoverRes.value.discovered).reduce(
          (n, v) => n + v,
          0,
        );
        parts.push(
          totalNew === 0
            ? "No new meetings on the calendars."
            : `Discovered ${totalNew} new meeting${totalNew === 1 ? "" : "s"}.`,
        );
      } else {
        parts.push(`Calendar scrape failed: ${discoverRes.reason}`);
      }

      if (refreshRes.status === "fulfilled") {
        const total = refreshRes.value.count;
        const errored = refreshRes.value.refreshed.filter((r) => r.error).length;
        parts.push(
          errored === 0
            ? `Refreshed materials for ${total} meeting${total === 1 ? "" : "s"}.`
            : `Refreshed ${total} meeting${total === 1 ? "" : "s"} (${errored} had errors — see server log).`,
        );
      } else {
        parts.push(`Materials refresh failed: ${refreshRes.reason}`);
      }

      const anyFailed =
        discoverRes.status === "rejected" || refreshRes.status === "rejected";
      toast(parts.join("\n"), anyFailed ? "error" : "success");
    },
    onError: (err: Error) => toast.error(`Refresh failed: ${err.message}`),
  });

  const now = new Date();
  const todayIso = localIso(now);
  const monday = mondayOf(now);
  const weekStartIso = localIso(monday);
  const weekEndIso = localIso(addDays(monday, 6));
  const nextWeekStartIso = localIso(addDays(monday, 7));
  const nextWeekEndIso = localIso(addDays(monday, 13));

  // A meeting belongs to a week if its date span overlaps it, so a two-day
  // meeting straddling the weekend shows in both weeks.
  const inWeek = (m: MeetingListItem, start: string, end: string) =>
    (m.end_date || m.meeting_date) >= start && m.meeting_date <= end;

  const byDate = (a: MeetingListItem, b: MeetingListItem) =>
    a.meeting_date.localeCompare(b.meeting_date) ||
    a.type_short.localeCompare(b.type_short);

  const thisWeek = useMemo(
    () => meetings.filter((m) => inWeek(m, weekStartIso, weekEndIso)).sort(byDate),
    [meetings, weekStartIso, weekEndIso],
  );
  const nextWeek = useMemo(
    () =>
      meetings
        .filter((m) => inWeek(m, nextWeekStartIso, nextWeekEndIso))
        .sort(byDate),
    [meetings, nextWeekStartIso, nextWeekEndIso],
  );

  const summarizedThisMonth = meetings.filter(
    (m) => m.status === "summarized" && m.meeting_date.startsWith(todayIso.slice(0, 7))
  ).length;
  const pendingReview = meetings.filter((m) => m.status === "materials").length;

  const openMeeting = (m: MeetingListItem) => navigate(`/meeting/${m.id}`);

  return (
    <>
      <Topbar
        crumbs={[{ label: "Overview" }]}
        actions={
          <>
            {isAdmin && (
              <button
                className="btn btn-sm"
                onClick={() => refreshAll.mutate()}
                disabled={refreshAll.isPending}
                title="Scrape calendars for new meetings AND pull latest materials for upcoming ones."
              >
                <Icon name="refresh" />
                {refreshAll.isPending ? "Refreshing…" : "Refresh"}
              </button>
            )}
            {canEdit && (
              <button
                className="btn btn-sm btn-primary"
                onClick={() => navigate("/add")}
              >
                <Icon name="plus" /> Add meeting
              </button>
            )}
          </>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Meetings &amp; dockets</div>
          <h1 className="page-title">{greeting()}, {firstName}.</h1>
          <p className="page-subtitle">
            {pendingReview > 0 ? (
              <>
                <span style={{ color: "var(--ink)" }}>
                  {pendingReview} meetings
                </span>{" "}
                with materials ready to summarize.{" "}
              </>
            ) : null}
            {summarizedThisMonth} briefings published this month.
          </p>
        </div>

        <Inbox meetings={meetings} onOpen={openMeeting} />

        <PipelineStatus />

        <div className="section-h">
          <h2>This week</h2>
          <span className="meta">
            {weekLabel(weekStartIso, weekEndIso)} · {thisWeek.length} meeting
            {thisWeek.length === 1 ? "" : "s"}
          </span>
        </div>
        {thisWeek.length === 0 ? (
          <div className="empty">No meetings this week.</div>
        ) : (
          <div className="wk-list">
            {thisWeek.map((m) => (
              <WeekRow key={m.id} m={m} todayIso={todayIso} onOpen={openMeeting} />
            ))}
          </div>
        )}

        <div className="section-h">
          <h2>Next week</h2>
          <span className="meta">
            {weekLabel(nextWeekStartIso, nextWeekEndIso)}
          </span>
        </div>
        {nextWeek.length === 0 ? (
          <div className="wk-quiet">Nothing scheduled yet.</div>
        ) : (
          <div className="wk-next-list">
            {nextWeek.map((m) => (
              <NextWeekRow key={m.id} m={m} onOpen={openMeeting} />
            ))}
          </div>
        )}
        <Link to="/meetings" className="wk-all-link">
          All meetings <Icon name="chev-r" size={12} />
        </Link>

        <DocketActivity />

        <div style={{ height: 48 }} />
      </div>
    </>
  );
}

// ── Week rows ─────────────────────────────────────────────────────────────

function WeekRow({
  m,
  todayIso,
  onOpen,
}: {
  m: MeetingListItem;
  todayIso: string;
  onOpen: (m: MeetingListItem) => void;
}) {
  const isToday =
    m.meeting_date <= todayIso && todayIso <= (m.end_date || m.meeting_date);
  const isPast = !isToday && (m.end_date || m.meeting_date) < todayIso;
  const multiDay = !!m.end_date && m.end_date !== m.meeting_date;

  return (
    <button
      className={`wk-row${isPast ? " is-past" : ""}${isToday ? " is-today" : ""}`}
      onClick={() => onOpen(m)}
    >
      <div className="wk-row-date">
        <div className="wk-row-dow">
          {isToday ? "Today" : weekdayShort(m.meeting_date)}
        </div>
        <div className="wk-row-day">{dayNumber(m.meeting_date)}</div>
      </div>
      <div className="wk-row-venue">
        <VenueTag>{m.venue}</VenueTag>
        <TypeTag>{m.type_short}</TypeTag>
      </div>
      <div className="wk-row-title">
        <div className="title-line">{m.type_name}</div>
        <div className="meta-line">
          {m.location}
          {multiDay && <> · {fmtDateRange(m.meeting_date, m.end_date)}</>}
        </div>
      </div>
      <div className="wk-row-stats">
        {m.doc_count > 0 && (
          <span>
            <span className="mono">{m.doc_count}</span> docs
          </span>
        )}
        {m.item_count > 0 && (
          <span>
            <span className="mono">{m.item_count}</span> items
          </span>
        )}
      </div>
      <div className="wk-row-status">
        <Pill status={m.status} />
      </div>
      <div className="wk-row-chev">
        <Icon name="chev-r" size={14} />
      </div>
    </button>
  );
}

const STATUS_WORDS: Record<MeetingStatus, string> = {
  scheduled: "scheduled",
  materials: "materials posted",
  summarized: "summarized",
  updated: "new files",
};

function NextWeekRow({
  m,
  onOpen,
}: {
  m: MeetingListItem;
  onOpen: (m: MeetingListItem) => void;
}) {
  return (
    <button className="wk-next-row" onClick={() => onOpen(m)}>
      <span className="wk-next-date">
        {weekdayShort(m.meeting_date)} {dayNumber(m.meeting_date)}
      </span>
      <VenueTag>{m.venue}</VenueTag>
      <TypeTag>{m.type_short}</TypeTag>
      <span className="wk-next-title">{m.type_name}</span>
      <span className="wk-next-status">{STATUS_WORDS[m.status]}</span>
    </button>
  );
}

// ── Docket activity ───────────────────────────────────────────────────────

function DocketActivity() {
  const navigate = useNavigate();
  const { data: dockets = [] } = useQuery({
    queryKey: qk.dockets,
    queryFn: () => api.dockets(),
  });

  const active = useMemo(
    () =>
      dockets
        .filter((d) => (d.recent_filing_count ?? 0) > 0)
        .sort((a, b) =>
          (b.latest_filed_date || "").localeCompare(a.latest_filed_date || ""),
        ),
    [dockets],
  );

  // Nothing tracked at all → the section could only ever say "no activity";
  // stay out of the way entirely.
  if (dockets.length === 0) return null;

  return (
    <>
      <div className="section-h">
        <h2>Docket activity</h2>
        <span className="meta">
          Last 14 days · <Link to="/elibrary">all dockets</Link>
        </span>
      </div>
      {active.length === 0 ? (
        <div className="wk-quiet">
          No filings on tracked dockets in the last two weeks.
        </div>
      ) : (
        <div className="dk-list">
          {active.map((d) => (
            <DocketRow key={d.id} d={d} onOpen={() => navigate(`/docket/${d.id}`)} />
          ))}
        </div>
      )}
    </>
  );
}

function DocketRow({ d, onOpen }: { d: DocketListItem; onOpen: () => void }) {
  const n = d.recent_filing_count ?? 0;
  return (
    <button className="dk-row" onClick={onOpen}>
      <div className="dk-row-top">
        <span className="dk-row-number">{d.docket_number}</span>
        <span className="dk-row-title">{d.title || "Untitled docket"}</span>
        <span className="dk-row-count">
          {n} new filing{n === 1 ? "" : "s"}
        </span>
      </div>
      {d.latest_filing_one_line && (
        <div className="dk-row-line">
          {d.latest_filed_date && (
            <span className="dk-row-when">
              {fmtShortDate(d.latest_filed_date)}
            </span>
          )}
          {d.latest_filing_one_line}
        </div>
      )}
    </button>
  );
}

// ── Pipeline status ───────────────────────────────────────────────────────

function shortFutureTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const ms = d.getTime() - Date.now();
  if (ms <= 0) return "imminent";
  const min = Math.round(ms / 60_000);
  if (min < 60) return `in ${min} min`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `in ${hr}h`;
  return `on ${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}`;
}

function PipelineStatus() {
  const venues = useQuery({
    queryKey: qk.venues,
    queryFn: () => api.venues(),
  });
  const scheduler = useQuery({
    queryKey: qk.scheduler,
    queryFn: () => api.schedulerStatus(),
  });

  const isone = venues.data?.find((v) => v.short_name === "ISO-NE");
  const discoverJob = scheduler.data?.jobs.find((j) => j.id === "discover_all_venues");
  const refreshJob = scheduler.data?.jobs.find((j) => j.id === "refresh_upcoming_meetings");

  const running = scheduler.data?.running ?? false;

  return (
    <div className="pipeline-status">
      <span
        className={`pipeline-dot ${running ? "ok" : "off"}`}
        title={running ? "Scheduler running" : "Scheduler off"}
      />
      <span className="muted text-xs">
        Calendars: last scrape {formatRel(isone?.last_scraped_at)}
        {discoverJob?.next_run_time && (
          <> · next {shortFutureTime(discoverJob.next_run_time)}</>
        )}
        {refreshJob?.next_run_time && (
          <> · materials refresh {shortFutureTime(refreshJob.next_run_time)}</>
        )}
      </span>
    </div>
  );
}

// ── Inbox ─────────────────────────────────────────────────────────────────

type Bucket =
  | "has_agenda"
  | "needs_categorization"
  | "new_files"
  | "ready_to_summarize";

interface BucketDef {
  key: Bucket;
  label: string;
  hint: string;
  match: (m: MeetingListItem) => boolean;
}

// Mutually-exclusive — each meeting falls into at most one bucket. Order here
// is display order (and the priority tiebreaker, though the match predicates
// are already pairwise disjoint).
const BUCKETS: BucketDef[] = [
  {
    key: "has_agenda",
    label: "Has agenda",
    hint: "Agenda parsed but no documents yet — waiting on the next refresh.",
    match: (m) =>
      m.item_count > 0 &&
      m.doc_count === 0 &&
      m.status !== "summarized" &&
      m.status !== "updated",
  },
  {
    key: "needs_categorization",
    label: "Needs file categorization",
    hint: "Documents arrived but aren't matched to agenda items yet.",
    match: (m) => (m.unassigned_doc_count ?? 0) > 0,
  },
  {
    key: "new_files",
    label: "New files uploaded",
    hint: "Briefing already exists but new documents have landed since.",
    match: (m) => m.status === "updated",
  },
  {
    key: "ready_to_summarize",
    label: "Ready to summarize",
    hint: "Agenda + documents are in, everything categorized. Click into the meeting and run summaries.",
    match: (m) =>
      m.item_count > 0 &&
      m.doc_count > 0 &&
      (m.unassigned_doc_count ?? 0) === 0 &&
      m.status !== "summarized" &&
      m.status !== "updated",
  },
];

function bucketize(meetings: MeetingListItem[]): Record<Bucket, MeetingListItem[]> {
  const result: Record<Bucket, MeetingListItem[]> = {
    has_agenda: [],
    needs_categorization: [],
    new_files: [],
    ready_to_summarize: [],
  };
  // Inbox is about *active* work — limit to recent + upcoming, skip ancient stubs.
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 30);
  const cutoffIso = cutoff.toISOString().slice(0, 10);

  for (const m of meetings) {
    if (m.meeting_date < cutoffIso) continue;
    for (const b of BUCKETS) {
      if (b.match(m)) {
        result[b.key].push(m);
        break;
      }
    }
  }
  // Most-recent first within each bucket.
  for (const k of Object.keys(result) as Bucket[]) {
    result[k].sort((a, b) => b.meeting_date.localeCompare(a.meeting_date));
  }
  return result;
}

function Inbox({
  meetings,
  onOpen,
}: {
  meetings: MeetingListItem[];
  onOpen: (m: MeetingListItem) => void;
}) {
  const buckets = useMemo(() => bucketize(meetings), [meetings]);
  const [open, setOpen] = useState<Bucket | null>(null);

  const needsAttention =
    buckets.has_agenda.length +
    buckets.needs_categorization.length +
    buckets.new_files.length +
    buckets.ready_to_summarize.length;

  if (needsAttention === 0) {
    return (
      <div className="inbox inbox-clear">
        <h2 className="section-head" style={{ margin: 0 }}>
          Inbox
        </h2>
        <span className="muted text-xs">All caught up.</span>
      </div>
    );
  }

  return (
    <div className="inbox">
      <div className="inbox-head">
        <h2 className="section-head" style={{ margin: 0 }}>
          Inbox
        </h2>
        <span className="muted text-xs">
          {needsAttention} meeting{needsAttention === 1 ? "" : "s"} need
          {needsAttention === 1 ? "s" : ""} attention.
        </span>
      </div>
      <div className="inbox-grid">
        {BUCKETS.map((b) => {
          const list = buckets[b.key];
          const isOpen = open === b.key;
          return (
            <div key={b.key} className={`inbox-card ${list.length === 0 ? "muted-card" : ""}`}>
              <button
                type="button"
                className="inbox-card-head"
                onClick={() => setOpen(isOpen ? null : b.key)}
              >
                <div>
                  <div className="inbox-card-label">{b.label}</div>
                  <div className="inbox-card-hint">{b.hint}</div>
                </div>
                <div className="inbox-card-num">{list.length}</div>
              </button>
              {isOpen && list.length > 0 && (
                <div className="inbox-list">
                  {list.slice(0, 25).map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      className="inbox-row"
                      onClick={() => onOpen(m)}
                    >
                      <span className="mono text-xs muted">{m.meeting_date}</span>
                      <span className="inbox-row-title">{m.title || m.type_name}</span>
                      <span className="mono text-xs muted">{m.type_short}</span>
                    </button>
                  ))}
                  {list.length > 25 && (
                    <div className="muted text-xs" style={{ padding: "6px 10px" }}>
                      + {list.length - 25} more
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
