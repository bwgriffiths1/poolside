import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Pill } from "../components/Pill";
import { Tag, VenueTag, TypeTag } from "../components/Tag";
import { Icon } from "../components/Icon";
import { api } from "../lib/api";
import { fmtDateRange, extFromFilename } from "../lib/format";
import { Markdown } from "../lib/markdown";
import {
  MaterialAssignment,
  PerItemDocControls,
} from "../components/MaterialAssignment";
import { VersionHistory } from "../components/VersionHistory";
import type { AgendaItem, DocumentRef } from "../types";

interface AgendaDraft {
  title: string;
  item_id: string;
  presenter: string;
  time_slot: string;
  vote_status: string;
  one_line: string;
  detailed: string;
}

interface AgendaRowProps {
  item: AgendaItem;
  meetingId: number;
  agenda: AgendaItem[];
  expanded: boolean;
  onToggle: () => void;
  isEditing: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: (draft: AgendaDraft) => void;
  onDelete: () => void;
}

function voteClass(vote?: string | null): string {
  if (!vote) return "vote";
  const v = vote.toLowerCase();
  if (v.includes("approved")) return "approved";
  if (v.includes("discussion")) return "discussion";
  return "vote";
}

function WatchToggle({ meetingId }: { meetingId: number }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["watch", meetingId],
    queryFn: () => api.isWatching(meetingId),
  });
  const watching = data?.watching ?? false;
  const toggle = useMutation({
    mutationFn: () =>
      watching ? api.unwatchMeeting(meetingId) : api.watchMeeting(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watch", meetingId] });
    },
  });
  return (
    <button
      className="btn btn-sm"
      onClick={() => toggle.mutate()}
      disabled={toggle.isPending}
      title={
        watching
          ? "Stop watching — you won't get notifications about this meeting."
          : "Watch — get a notification when this briefing is approved."
      }
    >
      <Icon name={watching ? "eye-off" : "eye"} size={12} />{" "}
      {watching ? "Watching" : "Watch"}
    </button>
  );
}

function SummaryMeta({ item }: { item: AgendaItem }) {
  if (item.summary_version == null) return null;
  const parts: string[] = [`v${item.summary_version}`];
  if (item.summary_status) parts.push(item.summary_status);
  if (item.summary_is_manual) parts.push("manual");
  if (item.summary_updated_at) parts.push(formatRel(item.summary_updated_at));
  return <span className="text-xs muted">{parts.join(" · ")}</span>;
}

function DocRow({
  doc,
  meetingId,
  itemId,
  agenda,
}: {
  doc: DocumentRef;
  meetingId: number;
  itemId: number;
  agenda: AgendaItem[];
}) {
  return (
    <div className="doc-row doc-row-assignable">
      <div className="doc-icon">
        {doc.ceii ? <Icon name="lock" /> : <Icon name="doc" />}
      </div>
      <div className="doc-name truncate">{doc.filename}</div>
      <div className="doc-ext mono text-xs">{extFromFilename(doc.filename)}</div>
      <div className="doc-actions">
        <PerItemDocControls
          meetingId={meetingId}
          docId={doc.id}
          itemId={itemId}
          agenda={agenda}
        />
        {doc.source_url && (
          <>
            <a
              className="btn btn-sm btn-ghost"
              title="Open source"
              href={doc.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Icon name="external" size={12} />
            </a>
            <a
              className="btn btn-sm btn-ghost"
              title="Download"
              href={doc.source_url}
              download={doc.filename}
            >
              <Icon name="download" size={12} />
            </a>
          </>
        )}
      </div>
    </div>
  );
}

function AgendaRow({
  item,
  meetingId,
  agenda,
  expanded,
  onToggle,
  isEditing,
  onEdit,
  onCancelEdit,
  onSaveEdit,
  onDelete,
}: AgendaRowProps) {
  const [draft, setDraft] = useState<AgendaDraft>({
    title: item.title,
    item_id: item.item_id ?? "",
    presenter: item.presenter ?? "",
    time_slot: item.time_slot ?? "",
    vote_status: item.vote_status ?? "",
    one_line: item.one_line ?? "",
    detailed: item.detailed ?? "",
  });

  useEffect(() => {
    if (isEditing) {
      setDraft({
        title: item.title,
        item_id: item.item_id ?? "",
        presenter: item.presenter ?? "",
        time_slot: item.time_slot ?? "",
        vote_status: item.vote_status ?? "",
        one_line: item.one_line ?? "",
        detailed: item.detailed ?? "",
      });
    }
  }, [isEditing, item]);

  const qc = useQueryClient();
  const resummarize = useMutation({
    mutationFn: () => api.resummarizeAgendaItem(item.id),
    onSuccess: (res) => {
      if (!res.ok) {
        alert(`Re-run skipped: ${res.reason ?? "no inputs"}`);
        return;
      }
      qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
      qc.invalidateQueries({ queryKey: ["summary", "agenda_item", item.id] });
      qc.invalidateQueries({ queryKey: ["summary-versions", "agenda_item", item.id] });
    },
    onError: (e: Error) => alert(`Re-run failed: ${e.message}`),
  });

  const [showVersions, setShowVersions] = useState(false);

  return (
    <div
      className={`agenda-item depth-${item.depth} ${expanded ? "open" : ""}`}
      style={{ paddingLeft: item.depth * 24 }}
    >
      <button className="agenda-head" onClick={onToggle}>
        <div className="agenda-chev">
          <Icon name={expanded ? "chev-d" : "chev-r"} size={12} />
        </div>
        <div className="agenda-num">{item.item_id || "—"}</div>
        <div className="agenda-title-wrap">
          <div className="agenda-title">{item.title}</div>
          {item.one_line && (
            <div className="agenda-oneline serif">{item.one_line}</div>
          )}
        </div>
        <div className="agenda-meta">
          {item.presenter && (
            <span className="text-xs muted">
              {item.presenter}
              {item.org ? ` · ${item.org}` : ""}
            </span>
          )}
        </div>
        <div className="agenda-status">
          {item.vote_status && (
            <span className={`vote-pill ${voteClass(item.vote_status)}`}>
              {item.vote_status}
            </span>
          )}
        </div>
        <div className="agenda-summary-state">
          {item.has_summary ? (
            <span className="state-dot summarized" title="Summarized">
              <Icon name="check" size={11} />
            </span>
          ) : (
            <span className="state-dot pending" title="No summary">
              ○
            </span>
          )}
        </div>
      </button>

      {expanded && (
        <div className="agenda-body">
          {item.docs.length > 0 && (
            <div className="doc-table">
              {item.docs.map((d) => (
                <DocRow
                  key={d.id}
                  doc={d}
                  meetingId={meetingId}
                  itemId={item.id}
                  agenda={agenda}
                />
              ))}
            </div>
          )}

          {!isEditing ? (
            <div className="agenda-summary">
              {item.has_summary ? (
                <>
                  <div
                    className="row"
                    style={{ alignItems: "center", marginBottom: 12, gap: 8 }}
                  >
                    <span
                      className="field-label"
                      style={{ marginBottom: 0 }}
                    >
                      Summary
                    </span>
                    <SummaryMeta item={item} />
                    <span style={{ flex: 1 }} />
                    <button className="btn btn-sm" onClick={onEdit}>
                      <Icon name="edit" size={12} /> Quick edit
                    </button>
                    <a
                      href={`#/edit/agenda_item/${item.id}`}
                      className="btn btn-sm btn-accent"
                      style={{ textDecoration: "none" }}
                    >
                      <Icon name="external" size={12} /> Open in full editor
                    </a>
                    <button
                      className="btn btn-sm btn-ghost"
                      title="Re-run AI summarization for this item (uses current doc summaries + child item summaries, current model, current prompt)"
                      disabled={resummarize.isPending}
                      onClick={() => resummarize.mutate()}
                    >
                      <Icon name="refresh" size={12} />{" "}
                      {resummarize.isPending ? "Re-running…" : "Re-run"}
                    </button>
                    <button
                      className={`btn btn-sm btn-ghost ${showVersions ? "is-active" : ""}`}
                      title="Show every saved version of this summary"
                      onClick={() => setShowVersions(!showVersions)}
                    >
                      <Icon name={showVersions ? "chev-d" : "chev-r"} size={11} />{" "}
                      Versions
                    </button>
                  </div>
                  {item.one_line && (
                    <p
                      className="serif"
                      style={{
                        fontSize: 15,
                        lineHeight: 1.55,
                        margin: "0 0 12px",
                        color: "var(--ink-soft)",
                        fontStyle: "italic",
                      }}
                    >
                      {item.one_line}
                    </p>
                  )}
                  {item.detailed ? (
                    <Markdown
                      source={item.detailed}
                      className="agenda-summary-body"
                    />
                  ) : !item.one_line ? (
                    <p className="muted text-sm" style={{ margin: 0 }}>
                      Summary stored but body is empty.
                    </p>
                  ) : null}
                  {showVersions && (
                    <VersionHistory
                      entityType="agenda_item"
                      entityId={item.id}
                      meetingId={meetingId}
                      onRestored={() => setShowVersions(false)}
                    />
                  )}
                </>
              ) : (
                <div className="empty-summary">
                  <span className="muted text-sm" style={{ flex: 1 }}>
                    No summary yet for this item.
                  </span>
                  <button
                    className="btn btn-sm btn-accent"
                    onClick={() => resummarize.mutate()}
                    disabled={resummarize.isPending}
                    title="Generate an AI summary for this item using its assigned documents and any child-item summaries."
                  >
                    <Icon name="spark" size={12} />{" "}
                    {resummarize.isPending ? "Summarizing…" : "Summarize this item"}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="agenda-edit">
              <div className="row" style={{ gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
                <div style={{ flex: "0 0 80px" }}>
                  <label className="field-label">Item ID</label>
                  <input
                    className="input"
                    placeholder="e.g. 7 or 7.a"
                    value={draft.item_id}
                    onChange={(e) =>
                      setDraft({ ...draft, item_id: e.target.value })
                    }
                  />
                </div>
                <div style={{ flex: 3, minWidth: 220 }}>
                  <label className="field-label">Title</label>
                  <input
                    className="input"
                    value={draft.title}
                    onChange={(e) =>
                      setDraft({ ...draft, title: e.target.value })
                    }
                  />
                </div>
              </div>
              <div className="row" style={{ gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
                <div style={{ flex: 2, minWidth: 180 }}>
                  <label className="field-label">Presenter</label>
                  <input
                    className="input"
                    value={draft.presenter}
                    onChange={(e) =>
                      setDraft({ ...draft, presenter: e.target.value })
                    }
                  />
                </div>
                <div style={{ flex: 1, minWidth: 120 }}>
                  <label className="field-label">Time slot</label>
                  <input
                    className="input"
                    placeholder="9:00 AM"
                    value={draft.time_slot}
                    onChange={(e) =>
                      setDraft({ ...draft, time_slot: e.target.value })
                    }
                  />
                </div>
                <div style={{ flex: 1, minWidth: 150 }}>
                  <label className="field-label">Vote status</label>
                  <input
                    className="input"
                    placeholder="Vote — Approved"
                    value={draft.vote_status}
                    onChange={(e) =>
                      setDraft({ ...draft, vote_status: e.target.value })
                    }
                  />
                </div>
              </div>
              <label className="field-label">One-line summary</label>
              <input
                className="input"
                value={draft.one_line}
                onChange={(e) =>
                  setDraft({ ...draft, one_line: e.target.value })
                }
              />
              <div style={{ height: 10 }} />
              <label className="field-label">Detailed summary</label>
              <textarea
                className="textarea"
                rows={5}
                value={draft.detailed}
                onChange={(e) =>
                  setDraft({ ...draft, detailed: e.target.value })
                }
              />
              <div className="row" style={{ marginTop: 12, gap: 8 }}>
                <button
                  className="btn btn-sm btn-accent"
                  onClick={() => onSaveEdit(draft)}
                >
                  <Icon name="check" size={12} /> Save changes
                </button>
                <button className="btn btn-sm" onClick={onCancelEdit}>
                  Cancel
                </button>
                <span style={{ flex: 1 }} />
                <button
                  className="btn btn-sm btn-ghost"
                  style={{ color: "var(--danger)" }}
                  onClick={() => {
                    if (window.confirm(
                      "Delete this agenda item? Document assignments will be removed but documents themselves stay (they'll fall back to unassigned)."
                    )) {
                      onDelete();
                    }
                  }}
                >
                  <Icon name="x" size={12} /> Delete item
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function Meeting() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const meetingId = Number(id);

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["meeting", meetingId],
    queryFn: () => api.meeting(meetingId),
  });
  const { data: briefing } = useQuery({
    queryKey: ["briefing", meetingId],
    queryFn: () => api.briefing(meetingId),
    retry: false,
  });

  const m = detail; // detail is a MeetingDetail (extends MeetingListItem)
  const hasBriefing =
    !!briefing && (briefing.sections.length > 0 || briefing.tldr.length > 0);

  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set([3]));
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showSummaryRunner, setShowSummaryRunner] = useState(false);
  // TODO: meeting-level summarize options (briefing style, extract images,
  // force re-run) are not honored by the backend yet — see the parity plan.
  // Per-item re-runs work via AgendaRow's "Re-run" button.

  const qcRefresh = useQueryClient();
  const refreshMeeting = useMutation({
    mutationFn: () => api.refreshMeeting(meetingId),
    onSuccess: () => {
      qcRefresh.invalidateQueries({ queryKey: ["meeting", meetingId] });
      qcRefresh.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
    },
    onError: (e: Error) => alert(`Refresh failed: ${e.message}`),
  });

  // Estimate fetched lazily when the modal opens.
  const estimate = useQuery({
    queryKey: ["summarize-estimate", meetingId],
    queryFn: () => api.estimateSummarize(meetingId),
    enabled: showSummaryRunner,
    staleTime: 60_000,
  });

  // Active job poller — also wired up when an in-flight job is discovered
  // via getActiveJob on mount.
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [completedAlerted, setCompletedAlerted] = useState<Set<number>>(new Set());

  // Recover any in-flight job for this meeting on mount.
  useEffect(() => {
    let cancelled = false;
    api.getActiveJob(meetingId).then((j) => {
      if (
        !cancelled &&
        j &&
        (j.status === "queued" || j.status === "running" || j.status === "cancelling")
      ) {
        setActiveJobId(j.id);
      }
    }).catch(() => { /* no-op */ });
    return () => { cancelled = true; };
  }, [meetingId]);

  const activeJob = useQuery({
    queryKey: ["job", activeJobId],
    queryFn: () => api.getJob(activeJobId as number),
    enabled: activeJobId != null,
    refetchInterval: (q) => {
      const data = q.state.data as
        | { status?: string }
        | undefined;
      if (!data) return 3000;
      const terminal =
        data.status === "complete" ||
        data.status === "failed" ||
        data.status === "cancelled";
      return terminal ? false : 3000;
    },
  });

  const cancelSummarize = useMutation({
    mutationFn: (jobId: number) => api.cancelJob(jobId),
    onError: (e: Error) => alert(`Cancel failed: ${e.message}`),
  });

  // When the polled job hits a terminal state, refresh data + toast once.
  useEffect(() => {
    const j = activeJob.data;
    if (!j || activeJobId == null) return;
    if (j.status !== "complete" && j.status !== "failed" && j.status !== "cancelled") return;
    if (completedAlerted.has(j.id)) return;
    setCompletedAlerted((s) => new Set([...s, j.id]));
    qcRefresh.invalidateQueries({ queryKey: ["meeting", meetingId] });
    qcRefresh.invalidateQueries({ queryKey: ["briefing", meetingId] });
    qcRefresh.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
    qcRefresh.invalidateQueries({ queryKey: ["meetings"] });
    if (j.status === "complete") {
      alert(
        `Summarization complete.\n` +
          `Actual cost $${j.cost_usd.toFixed(4)}.\n` +
          `Input tokens: ${j.input_tokens.toLocaleString()}\n` +
          `Output tokens: ${j.output_tokens.toLocaleString()}`,
      );
    }
  }, [activeJob.data, activeJobId, completedAlerted, qcRefresh, meetingId]);

  const startSummarize = useMutation({
    mutationFn: () => api.startSummarize(meetingId),
    onSuccess: (res) => {
      setShowSummaryRunner(false);
      setActiveJobId(res.job_id);
    },
    onError: (e: Error) => alert(`Could not start summarize: ${e.message}`),
  });

  const cleanupZips = useMutation({
    mutationFn: () => api.cleanupZipExpansion(meetingId),
    onSuccess: (res) => {
      qcRefresh.invalidateQueries({ queryKey: ["meeting", meetingId] });
      qcRefresh.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
      qcRefresh.invalidateQueries({ queryKey: ["meetings"] });
      if (res.deleted_children === 0 && res.un_ignored_zips === 0) {
        alert("Nothing to clean up — this meeting wasn't pre-expanded.");
      } else {
        alert(
          `Removed ${res.deleted_children} expanded child row(s); ` +
            `restored ${res.un_ignored_zips} zip(s). ` +
            `Zips are now handled inline at summarize time.`,
        );
      }
    },
    onError: (e: Error) => alert(`Cleanup failed: ${e.message}`),
  });

  const toggle = (itemId: number) =>
    setExpandedIds((prev) => {
      const n = new Set(prev);
      if (n.has(itemId)) n.delete(itemId);
      else n.add(itemId);
      return n;
    });

  const totals = useMemo(() => {
    const agenda = detail?.agenda ?? [];
    const total = agenda.length;
    const withSummary = agenda.filter((i) => i.has_summary).length;
    const docs = agenda.flatMap((i) => i.docs).length;
    return { total, withSummary, docs };
  }, [detail]);

  if (!m || !detail) {
    return (
      <>
        <Topbar
          crumbs={[
            { label: "Meetings", to: "/meetings" },
            { label: detailLoading ? "Loading…" : "Not found" },
          ]}
        />
        <div className="page">
          <div className="muted">
            {detailLoading ? "Loading meeting…" : "Meeting not found."}
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Meetings", to: "/meetings" },
          { label: `${m.venue} · ${m.type_short}` },
          { label: m.title },
        ]}
        actions={
          <>
            <WatchToggle meetingId={meetingId} />
            <button
              className="btn btn-sm"
              onClick={() => navigate(`/briefing/${m.id}`)}
            >
              <Icon name="book" /> Open briefing
            </button>
            <button
              className="btn btn-sm"
              onClick={() => cleanupZips.mutate()}
              disabled={cleanupZips.isPending}
              title="Undo a prior Expand zips run — zips are now handled inline at summarize time."
            >
              <Icon name="refresh" />{" "}
              {cleanupZips.isPending ? "Cleaning…" : "Reset zip rows"}
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => setShowSummaryRunner(true)}
            >
              <Icon name="spark" /> Summarize
            </button>
          </>
        }
      />

      <div className="page-wide" style={{ paddingLeft: 48, paddingRight: 48 }}>
        <div className="meeting-head">
          <div>
            <div className="page-eyebrow">
              <VenueTag style={{ marginRight: 6 }}>{m.venue}</VenueTag>
              <TypeTag style={{ marginRight: 6 }}>{m.type_short}</TypeTag>
              {m.external_id}
            </div>
            <h1 className="page-title">{m.type_name}</h1>
            <div className="meeting-head-meta">
              <span>
                <Icon name="calendar" size={13} />{" "}
                {fmtDateRange(m.meeting_date, m.end_date)}
              </span>
              <span>
                <Icon name="globe" size={13} /> {m.location}
              </span>
              <Pill status={m.status} />
            </div>
            {detail.one_line && (
              <p className="meeting-headline serif">{detail.one_line}</p>
            )}
          </div>
          <div className="meeting-head-right">
            <div className="stat-block">
              <div className="stat-block-num">{totals.total}</div>
              <div className="stat-block-label">agenda items</div>
            </div>
            <div className="stat-block">
              <div className="stat-block-num">{totals.docs}</div>
              <div className="stat-block-label">documents</div>
            </div>
            <div className="stat-block">
              <div className="stat-block-num">
                <span>{totals.withSummary}</span>
                <span className="muted">/{totals.total}</span>
              </div>
              <div className="stat-block-label">summarized</div>
            </div>
          </div>
        </div>

        {m.tags.length > 0 && (
          <div
            className="row"
            style={{ gap: 6, flexWrap: "wrap", marginBottom: 24 }}
          >
            <span
              className="field-label"
              style={{ marginBottom: 0, marginRight: 4 }}
            >
              Topics
            </span>
            {m.tags.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </div>
        )}

        {hasBriefing ? (
          <div
            className="briefing-card"
            onClick={() => navigate(`/briefing/${m.id}`)}
          >
            <div>
              <div className="page-eyebrow" style={{ marginBottom: 6 }}>
                Meeting briefing
              </div>
              <h2 className="briefing-card-title serif">
                {briefing!.headline || detail.one_line || briefing!.title}
              </h2>
              <div className="row" style={{ marginTop: 12, gap: 14 }}>
                <span className="text-xs muted">
                  <Icon name="dot" size={11} /> {briefing!.word_count} words ·{" "}
                  {briefing!.reading_time} min read
                </span>
                <span className="text-xs muted">{briefing!.model}</span>
                <span className="text-xs muted">
                  Generated {briefing!.generated_at}
                </span>
              </div>
            </div>
            <div className="briefing-card-right">
              <button
                className="btn btn-sm"
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    await api.downloadBriefingDocx(meetingId);
                  } catch (err) {
                    console.error("Download failed", err);
                    alert("Could not download briefing — see console for details.");
                  }
                }}
              >
                <Icon name="download" /> Download .docx
              </button>
              <button
                className="btn btn-sm btn-accent"
                onClick={() => navigate(`/briefing/${m.id}`)}
              >
                Read briefing <Icon name="arrow-r" size={12} />
              </button>
            </div>
          </div>
        ) : (
          <div
            className="briefing-card"
            style={{
              background: "var(--bg-elev)",
              borderColor: "var(--border)",
              cursor: "default",
            }}
          >
            <div>
              <div className="page-eyebrow" style={{ marginBottom: 6 }}>
                Meeting briefing
              </div>
              <h2 className="briefing-card-title serif" style={{ color: "var(--muted)" }}>
                No briefing yet — run summarization to generate one.
              </h2>
            </div>
            <div className="briefing-card-right">
              <button
                className="btn btn-sm btn-accent"
                onClick={() => setShowSummaryRunner(true)}
              >
                <Icon name="spark" size={12} /> Summarize
              </button>
            </div>
          </div>
        )}

        {activeJob.data &&
          activeJobId === activeJob.data.id &&
          activeJob.data.status !== "complete" && (
            <div
              className={`summary-banner ${
                activeJob.data.status === "failed" ? "is-error" : ""
              }`}
            >
              <div className="summary-banner-main">
                <div className="summary-banner-title">
                  {activeJob.data.status === "running" && "Summarizing meeting…"}
                  {activeJob.data.status === "queued" && "Queued…"}
                  {activeJob.data.status === "cancelling" && "Cancelling…"}
                  {activeJob.data.status === "cancelled" && "Cancelled"}
                  {activeJob.data.status === "failed" && "Summarization failed"}
                </div>
                <div className="summary-banner-sub">
                  {activeJob.data.status === "failed"
                    ? activeJob.data.error || "Unknown error."
                    : activeJob.data.status === "cancelling"
                    ? "Waiting for the current step to finish, then stopping."
                    : activeJob.data.progress_text || "Working…"}
                </div>
              </div>
              {(activeJob.data.status === "queued" ||
                activeJob.data.status === "running") && (
                <button
                  className="btn btn-sm btn-ghost"
                  disabled={cancelSummarize.isPending}
                  onClick={() => cancelSummarize.mutate(activeJob.data!.id)}
                >
                  {cancelSummarize.isPending ? "Cancelling…" : "Cancel"}
                </button>
              )}
              {(activeJob.data.status === "failed" ||
                activeJob.data.status === "cancelled") && (
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => setActiveJobId(null)}
                >
                  Dismiss
                </button>
              )}
            </div>
          )}

        {showSummaryRunner && (
          <div className="summary-runner">
            <div className="row" style={{ marginBottom: 14 }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>
                Summarize this meeting
              </h3>
              <span style={{ flex: 1 }} />
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => setShowSummaryRunner(false)}
                disabled={startSummarize.isPending || refreshMeeting.isPending}
              >
                <Icon name="x" size={12} />
              </button>
            </div>
            <div
              className="text-sm muted"
              style={{ marginBottom: 14, lineHeight: 1.5 }}
            >
              Runs the full three-level pipeline: summarize each document, roll
              up per agenda item, then write the meeting briefing. The job runs
              in the background — you can close this modal and come back.
            </div>

            <div
              style={{
                background: "var(--bg-sunk)",
                border: "1px solid var(--border-soft)",
                borderRadius: "var(--radius)",
                padding: "10px 12px",
                marginBottom: 14,
              }}
            >
              {estimate.isLoading && (
                <div className="text-sm muted">Loading estimate…</div>
              )}
              {estimate.isError && (
                <div className="text-sm" style={{ color: "var(--accent)" }}>
                  Couldn't load estimate: {(estimate.error as Error).message}
                </div>
              )}
              {estimate.data && (
                <>
                  <div style={{ fontSize: 14 }}>
                    <span className="muted">Est. cost </span>
                    <strong>
                      ≈ ${estimate.data.estimated_cost_usd.toFixed(4)}
                    </strong>
                  </div>
                  <div className="muted text-xs" style={{ marginTop: 4 }}>
                    ~{estimate.data.estimated_input_tokens.toLocaleString()} input
                    tokens · ~
                    {estimate.data.estimated_output_tokens.toLocaleString()}{" "}
                    output tokens · {estimate.data.items_planned} LLM call(s)
                  </div>
                  {estimate.data.docs_without_text > 0 && (
                    <div className="muted text-xs" style={{ marginTop: 4 }}>
                      Note: {estimate.data.docs_without_text} document(s)
                      haven't been text-extracted yet, so the estimate is rough
                      — actuals may differ.
                    </div>
                  )}
                  {estimate.data.committee_stats &&
                    estimate.data.committee_stats.count > 0 && (
                      <div
                        className="muted text-xs"
                        style={{
                          marginTop: 8,
                          paddingTop: 8,
                          borderTop: "1px solid var(--border-soft)",
                        }}
                      >
                        Typical for this committee: $
                        {estimate.data.committee_stats.avg_cost_usd.toFixed(4)}{" "}
                        · ~
                        {Math.max(
                          1,
                          Math.round(
                            estimate.data.committee_stats.avg_duration_seconds / 60,
                          ),
                        )}{" "}
                        min ({estimate.data.committee_stats.count} prior run
                        {estimate.data.committee_stats.count === 1 ? "" : "s"})
                      </div>
                    )}
                </>
              )}
            </div>

            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button
                className="btn btn-sm"
                disabled={refreshMeeting.isPending || startSummarize.isPending}
                onClick={() => refreshMeeting.mutate()}
                title="Pull latest documents and re-parse the agenda; does NOT call the LLM."
              >
                <Icon name="refresh" size={11} />{" "}
                {refreshMeeting.isPending
                  ? "Refreshing…"
                  : "Refresh materials only"}
              </button>
              <span style={{ flex: 1 }} />
              <button
                className="btn btn-sm btn-accent"
                disabled={startSummarize.isPending || refreshMeeting.isPending}
                onClick={() => startSummarize.mutate()}
              >
                <Icon name="spark" size={11} />{" "}
                {startSummarize.isPending
                  ? "Starting…"
                  : "Run summarization"}
              </button>
            </div>
          </div>
        )}

        <MaterialAssignment meetingId={meetingId} agenda={detail.agenda} />

        <div className="section-h" style={{ marginTop: 32 }}>
          <h2>Agenda</h2>
          <span className="meta">
            {totals.total} items · {totals.docs} documents
          </span>
        </div>
        {detail.agenda.length === 0 ? (
          <AgendaEmpty meetingId={meetingId} lastScraped={m.last_scraped_at} />
        ) : (
          <div className="agenda-list">
            {detail.agenda.map((item) => (
              <AgendaRow
                key={item.id}
                item={item}
                meetingId={meetingId}
                agenda={detail.agenda}
                expanded={expandedIds.has(item.id)}
                onToggle={() => toggle(item.id)}
                isEditing={editingId === item.id}
                onEdit={() => setEditingId(item.id)}
                onCancelEdit={() => setEditingId(null)}
                onSaveEdit={(draft) => {
                  api
                    .updateAgendaItem(item.id, {
                      title: draft.title,
                      item_id: draft.item_id || undefined,
                      presenter: draft.presenter || undefined,
                      time_slot: draft.time_slot || undefined,
                      vote_status: draft.vote_status || undefined,
                    })
                    .then(() => {
                      qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
                      setEditingId(null);
                    })
                    .catch((err) => alert(`Save failed: ${err.message || err}`));
                }}
                onDelete={() => {
                  api
                    .deleteAgendaItem(item.id)
                    .then(() => {
                      qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
                      qc.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
                      setEditingId(null);
                    })
                    .catch((err) => alert(`Delete failed: ${err.message || err}`));
                }}
              />
            ))}
          </div>
        )}

        <AddAgendaItem meetingId={meetingId} />

        <DangerZone meetingId={meetingId} title={m.title} />

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}

function DangerZone({ meetingId, title }: { meetingId: number; title: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const wipeDocs = useMutation({
    mutationFn: () => api.deleteAllDocuments(meetingId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
      qc.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
      qc.invalidateQueries({ queryKey: ["meetings"] });
      alert(
        res.removed_documents === 0
          ? "No documents to remove."
          : `Removed ${res.removed_documents} document${res.removed_documents === 1 ? "" : "s"}. Agenda items kept; doc assignments cleared.`,
      );
    },
    onError: (e: Error) => alert(`Failed: ${e.message}`),
  });

  const deleteMtg = useMutation({
    mutationFn: () => api.deleteMeeting(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["meetings"] });
      navigate("/overview", { replace: true });
    },
    onError: (e: Error) => alert(`Failed: ${e.message}`),
  });

  const onWipeDocs = () => {
    if (
      window.confirm(
        "Remove every document attached to this meeting? Agenda items + summaries are kept; doc-to-item assignments are cleared. This can't be undone.",
      )
    ) {
      wipeDocs.mutate();
    }
  };

  const onDelete = () => {
    const ans = window.prompt(
      `Type the meeting title to confirm full deletion:\n\n${title}`,
    );
    if (ans == null) return;
    if (ans.trim() !== title.trim()) {
      alert("Title didn't match — nothing deleted.");
      return;
    }
    deleteMtg.mutate();
  };

  return (
    <div className="danger-zone">
      <h2 className="danger-zone-h">Danger zone</h2>
      <div className="danger-row">
        <div>
          <div className="danger-row-h">Remove all documents</div>
          <div className="muted text-xs">
            Wipes documents + their item assignments. Keeps agenda + summaries.
            Useful when the scraper pulled garbage and you want to re-discover
            materials from scratch.
          </div>
        </div>
        <button
          className="btn btn-sm"
          onClick={onWipeDocs}
          disabled={wipeDocs.isPending}
        >
          <Icon name="trash" size={12} />{" "}
          {wipeDocs.isPending ? "Removing…" : "Remove all docs"}
        </button>
      </div>
      <div className="danger-row">
        <div>
          <div className="danger-row-h">Delete this meeting</div>
          <div className="muted text-xs">
            Drops the meeting row + every agenda item, document, summary,
            share link, and summarize job that hangs off it. Cascades. Cannot
            be undone.
          </div>
        </div>
        <button
          className="btn btn-sm"
          style={{ borderColor: "var(--accent-soft)", color: "var(--accent)" }}
          onClick={onDelete}
          disabled={deleteMtg.isPending}
        >
          <Icon name="trash" size={12} />{" "}
          {deleteMtg.isPending ? "Deleting…" : "Delete meeting"}
        </button>
      </div>
    </div>
  );
}

function AddAgendaItem({ meetingId }: { meetingId: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ item_id: "", title: "", presenter: "" });
  const create = useMutation({
    mutationFn: () =>
      api.createAgendaItem(meetingId, {
        item_id: draft.item_id || undefined,
        title: draft.title,
        presenter: draft.presenter || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
      setOpen(false);
      setDraft({ item_id: "", title: "", presenter: "" });
    },
    onError: (e: Error) => alert(`Add failed: ${e.message}`),
  });

  if (!open) {
    return (
      <div style={{ marginTop: 12 }}>
        <button className="btn btn-sm" onClick={() => setOpen(true)}>
          <Icon name="plus" size={12} /> Add agenda item
        </button>
      </div>
    );
  }
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="row" style={{ gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "0 0 80px" }}>
          <label className="field-label">Item ID</label>
          <input
            className="input"
            placeholder="e.g. 7"
            value={draft.item_id}
            onChange={(e) => setDraft({ ...draft, item_id: e.target.value })}
          />
        </div>
        <div style={{ flex: 3, minWidth: 240 }}>
          <label className="field-label">Title</label>
          <input
            className="input"
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            placeholder="Agenda item title"
          />
        </div>
        <div style={{ flex: 2, minWidth: 180 }}>
          <label className="field-label">Presenter</label>
          <input
            className="input"
            value={draft.presenter}
            onChange={(e) => setDraft({ ...draft, presenter: e.target.value })}
          />
        </div>
      </div>
      <div className="row" style={{ gap: 8 }}>
        <button
          className="btn btn-sm btn-accent"
          disabled={!draft.title.trim() || create.isPending}
          onClick={() => create.mutate()}
        >
          <Icon name="check" size={12} />{" "}
          {create.isPending ? "Adding…" : "Add item"}
        </button>
        <button className="btn btn-sm" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function AgendaEmpty({
  meetingId,
  lastScraped,
}: {
  meetingId: number;
  lastScraped?: string;
}) {
  const qc = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => api.refreshMeeting(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
      qc.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
    },
  });

  const rel = lastScraped ? formatRel(lastScraped) : null;

  return (
    <div className="empty" style={{ textAlign: "left", padding: "var(--pad-5)" }}>
      <div className="serif" style={{ fontSize: 17, color: "var(--ink)", marginBottom: 6 }}>
        Agenda not posted yet.
      </div>
      <div className="muted text-sm" style={{ marginBottom: 12 }}>
        {rel
          ? `Last checked ${rel}. ISO-NE typically posts agendas about a week before the meeting.`
          : "This meeting hasn't been scraped for materials yet."}
      </div>
      <button
        className="btn btn-sm btn-accent"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
      >
        <Icon name="refresh" size={12} />{" "}
        {refresh.isPending ? "Checking…" : "Re-check now"}
      </button>
    </div>
  );
}

function formatRel(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  return `${d}d ago`;
}
