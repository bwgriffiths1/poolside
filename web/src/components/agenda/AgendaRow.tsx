import { useState, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { VersionHistory } from "../VersionHistory";
import { Markdown } from "../../lib/markdown";
import { api } from "../../lib/api";
import { qk } from "../../lib/queries";
import { toast } from "../../lib/toast";
import { formatRel } from "../../lib/format";
import { DocRow } from "./DocRow";
import { AddItemMaterial } from "./AddItemMaterial";
import { idForAnchor } from "./anchors";
import type { AgendaItem } from "../../types";

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
  onCloseEdit: () => void;
}

function voteClass(vote?: string | null): string {
  if (!vote) return "vote";
  const v = vote.toLowerCase();
  if (v.includes("approved")) return "approved";
  if (v.includes("discussion")) return "discussion";
  return "vote";
}

function SummaryMeta({ item }: { item: AgendaItem }) {
  if (item.summary_version == null) return null;
  const parts: string[] = [`v${item.summary_version}`];
  if (item.summary_status) parts.push(item.summary_status);
  if (item.summary_is_manual) parts.push("manual");
  if (item.summary_updated_at) parts.push(formatRel(item.summary_updated_at));
  return <span className="text-xs muted">{parts.join(" · ")}</span>;
}

export function AgendaRow({
  item,
  meetingId,
  agenda,
  expanded,
  onToggle,
  isEditing,
  onEdit,
  onCloseEdit,
}: AgendaRowProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const resummarize = useMutation({
    mutationFn: () => api.resummarizeAgendaItem(item.id),
    onSuccess: (res) => {
      if (!res.ok) {
        toast.info(`Re-run skipped: ${res.reason ?? "no inputs"}`);
        return;
      }
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      qc.invalidateQueries({ queryKey: qk.summary("agenda_item", item.id) });
      qc.invalidateQueries({
        queryKey: qk.summaryVersions("agenda_item", item.id),
      });
    },
    onError: (e: Error) => toast.error(`Re-run failed: ${e.message}`),
  });

  const save = useMutation({
    mutationFn: async (draft: AgendaDraft) => {
      await api.updateAgendaItem(item.id, {
        title: draft.title,
        item_id: draft.item_id || undefined,
        presenter: draft.presenter || undefined,
        time_slot: draft.time_slot || undefined,
        vote_status: draft.vote_status || undefined,
      });
      // The summary lives in its own entity — save it only when the quick-edit
      // actually changed it, so we don't mint a new manual version per save.
      const summaryChanged =
        draft.one_line !== (item.one_line ?? "") ||
        draft.detailed !== (item.detailed ?? "");
      if (summaryChanged) {
        await api.saveSummary("agenda_item", item.id, {
          one_line: draft.one_line || undefined,
          detailed: draft.detailed,
        });
      }
      return summaryChanged;
    },
    onSuccess: (summaryChanged) => {
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      if (summaryChanged) {
        qc.invalidateQueries({ queryKey: qk.summary("agenda_item", item.id) });
        qc.invalidateQueries({
          queryKey: qk.summaryVersions("agenda_item", item.id),
        });
      }
      onCloseEdit();
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteAgendaItem(item.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
      onCloseEdit();
    },
    onError: (e: Error) => toast.error(`Delete failed: ${e.message}`),
  });

  const [showVersions, setShowVersions] = useState(false);

  const copyAnchor = (e: MouseEvent) => {
    e.stopPropagation();
    const url = `${window.location.origin}/#/meeting/${meetingId}?item=${encodeURIComponent(item.item_id || "")}`;
    void navigator.clipboard.writeText(url).catch(() => {
      window.prompt("Copy this URL:", url);
    });
  };

  return (
    <div
      id={idForAnchor(item.item_id)}
      className={`agenda-item depth-${item.depth} ${expanded ? "open" : ""}`}
      style={{ paddingLeft: item.depth * 24 }}
    >
      <button className="agenda-head" onClick={onToggle}>
        <div className="agenda-chev">
          <Icon name={expanded ? "chev-d" : "chev-r"} size={12} />
        </div>
        <div className="agenda-num">
          {item.item_id || "—"}
          <span
            className="agenda-anchor"
            onClick={copyAnchor}
            title="Copy link to this item"
            aria-label="Copy link"
          >
            <Icon name="link" size={11} />
          </span>
        </div>
        <div className="agenda-title-wrap">
          <div className="agenda-title">
            {item.title}
            {(item.initiative_codes ?? []).map((code) => (
              <span
                key={code}
                className="initiative-chip mono"
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/initiatives/${encodeURIComponent(code)}`);
                }}
                title={`Open initiative ${code}`}
              >
                {code}
              </span>
            ))}
          </div>
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

          <AddItemMaterial
            itemId={item.id}
            meetingId={meetingId}
            onAdded={() => resummarize.mutate()}
          />

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
            <AgendaEditForm
              item={item}
              saving={save.isPending}
              deleting={remove.isPending}
              onSave={(draft) => save.mutate(draft)}
              onCancel={onCloseEdit}
              onDelete={() => remove.mutate()}
            />
          )}
        </div>
      )}
    </div>
  );
}

/** Quick-edit form. Mounted only while editing, and the draft is seeded once
 *  on mount — a background refetch of the meeting can't clobber in-progress
 *  edits (the old effect-based reset did exactly that). */
function AgendaEditForm({
  item,
  saving,
  deleting,
  onSave,
  onCancel,
  onDelete,
}: {
  item: AgendaItem;
  saving: boolean;
  deleting: boolean;
  onSave: (draft: AgendaDraft) => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const [draft, setDraft] = useState<AgendaDraft>(() => ({
    title: item.title,
    item_id: item.item_id ?? "",
    presenter: item.presenter ?? "",
    time_slot: item.time_slot ?? "",
    vote_status: item.vote_status ?? "",
    one_line: item.one_line ?? "",
    detailed: item.detailed ?? "",
  }));

  return (
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
          disabled={saving}
          onClick={() => onSave(draft)}
        >
          <Icon name="check" size={12} />{" "}
          {saving ? "Saving…" : "Save changes"}
        </button>
        <button className="btn btn-sm" onClick={onCancel}>
          Cancel
        </button>
        <span style={{ flex: 1 }} />
        <button
          className="btn btn-sm btn-ghost"
          style={{ color: "var(--danger)" }}
          disabled={deleting}
          onClick={() => {
            if (window.confirm(
              "Delete this agenda item? Document assignments will be removed but documents themselves stay (they'll fall back to unassigned)."
            )) {
              onDelete();
            }
          }}
        >
          <Icon name="x" size={12} /> {deleting ? "Deleting…" : "Delete item"}
        </button>
      </div>
    </div>
  );
}
