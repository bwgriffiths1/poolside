import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Pill } from "../components/Pill";
import { Tag, VenueTag, TypeTag } from "../components/Tag";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { api } from "../lib/api";
import { fmtDateRange, extFromFilename } from "../lib/format";
import { Markdown } from "../lib/markdown";
import {
  MaterialAssignment,
  PerItemDocControls,
} from "../components/MaterialAssignment";
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
        <button className="btn btn-sm btn-ghost" title="Open">
          <Icon name="external" size={12} />
        </button>
        <button className="btn btn-sm btn-ghost" title="Download">
          <Icon name="download" size={12} />
        </button>
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
                    <span className="text-xs muted">
                      v2 · approved · May 12 18:42
                    </span>
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
                    <button className="btn btn-sm btn-ghost" title="Re-run AI summarization">
                      <Icon name="refresh" size={12} /> Re-run
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
                </>
              ) : (
                <div className="empty-summary">
                  <span className="muted text-sm">No summary yet.</span>
                  <button className="btn btn-sm btn-accent">
                    <Icon name="spark" size={12} /> Summarize
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
  const [briefingStyle, setBriefingStyle] = useState<"standard" | "detailed">(
    "standard"
  );
  const [extractImages, setExtractImages] = useState(false);
  const [forceRerun, setForceRerun] = useState(false);

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
            <button
              className="btn btn-sm"
              onClick={() => navigate(`/briefing/${m.id}`)}
            >
              <Icon name="book" /> Open briefing
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
            <button className="btn btn-sm btn-ghost">
              <Icon name="plus" size={11} />
            </button>
          </div>
        )}

        {hasBriefing ? (
          <div
            className="briefing-card"
            onClick={() => navigate(`/briefing/${m.id}`)}
          >
            <div>
              <div className="page-eyebrow" style={{ marginBottom: 6 }}>
                Meeting briefing · v2
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
                onClick={(e) => e.stopPropagation()}
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

        {showSummaryRunner && (
          <div className="summary-runner">
            <div className="row" style={{ marginBottom: 14 }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>
                Re-summarize this meeting
              </h3>
              <span style={{ flex: 1 }} />
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => setShowSummaryRunner(false)}
              >
                <Icon name="x" size={12} />
              </button>
            </div>
            <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
              <div style={{ minWidth: 200 }}>
                <label className="field-label">Briefing style</label>
                <Segmented
                  value={briefingStyle}
                  onChange={setBriefingStyle}
                  options={[
                    { value: "standard", label: "Standard" },
                    { value: "detailed", label: "Detailed" },
                  ]}
                />
              </div>
              <label className="row" style={{ gap: 6, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={extractImages}
                  onChange={(e) => setExtractImages(e.target.checked)}
                />
                <span className="text-sm">Extract images & charts</span>
              </label>
              <label className="row" style={{ gap: 6, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={forceRerun}
                  onChange={(e) => setForceRerun(e.target.checked)}
                />
                <span className="text-sm">Force re-run all levels</span>
              </label>
              <span style={{ flex: 1 }} />
              <span className="text-xs muted">Est. cost: ~$2.40 · ~3 min</span>
              <button className="btn btn-sm btn-accent">
                <Icon name="play" size={11} /> Run
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

        <div style={{ height: 64 }} />
      </div>
    </>
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
          ? `Last checked ${rel}. ISO-NE / NYISO typically post agendas about a week before the meeting.`
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
