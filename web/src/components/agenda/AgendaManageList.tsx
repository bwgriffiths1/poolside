import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { api } from "../../lib/api";
import { qk, useCan } from "../../lib/queries";
import { toast } from "../../lib/toast";
import type { AgendaItem } from "../../types";

interface ItemDraft {
  item_id: string;
  title: string;
  presenter: string;
  time_slot: string;
  vote_status: string;
}

function confirmDelete(item: AgendaItem): boolean {
  const consequences: string[] = [];
  if (item.docs.length > 0) {
    consequences.push(
      `Its ${item.docs.length} assigned document${item.docs.length === 1 ? "" : "s"} will fall back to unassigned.`,
    );
  }
  if (item.has_summary) {
    consequences.push("Its summary and version history will be deleted.");
  }
  const label = item.item_id ? `${item.item_id} — ${item.title}` : item.title;
  return window.confirm(
    [`Delete agenda item "${label}"?`, ...consequences].join("\n\n"),
  );
}

/** Per-item agenda workbench for the Manage page: rename, renumber, delete.
 *  Summary editing stays on the meeting (reader) page. */
export function AgendaManageList({
  meetingId,
  agenda,
}: {
  meetingId: number;
  agenda: AgendaItem[];
}) {
  const qc = useQueryClient();
  const { canEdit } = useCan();
  const [editingId, setEditingId] = useState<number | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
    qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
  };

  const save = useMutation({
    mutationFn: ({ id, draft }: { id: number; draft: ItemDraft }) =>
      api.updateAgendaItem(id, {
        title: draft.title.trim(),
        item_id: draft.item_id.trim() || null,
        presenter: draft.presenter.trim() || null,
        time_slot: draft.time_slot.trim() || null,
        vote_status: draft.vote_status.trim() || null,
      }),
    onSuccess: () => {
      invalidate();
      setEditingId(null);
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteAgendaItem(id),
    onSuccess: () => {
      invalidate();
      toast.success("Agenda item deleted.");
    },
    onError: (e: Error) => toast.error(`Delete failed: ${e.message}`),
  });

  if (!canEdit || agenda.length === 0) return null;

  return (
    <div className="agenda-manage-list">
      {agenda.map((item) =>
        editingId === item.id ? (
          <ManageEditForm
            key={item.id}
            item={item}
            saving={save.isPending}
            onSave={(draft) => save.mutate({ id: item.id, draft })}
            onCancel={() => setEditingId(null)}
          />
        ) : (
          <ManageRow
            key={item.id}
            item={item}
            deleting={remove.isPending && remove.variables === item.id}
            onEdit={() => setEditingId(item.id)}
            onDelete={() => {
              if (confirmDelete(item)) remove.mutate(item.id);
            }}
          />
        ),
      )}
    </div>
  );
}

function ManageRow({
  item,
  deleting,
  onEdit,
  onDelete,
}: {
  item: AgendaItem;
  deleting: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  // Guard against legacy synthesized parents (negative ids) — no DB row to edit.
  const editable = item.id > 0;
  return (
    <div
      className="agenda-manage-row"
      style={{ marginLeft: item.depth * 20 }}
    >
      <span className="agenda-manage-num mono">{item.item_id || "—"}</span>
      <span className="truncate">
        {item.title}
        {item.presenter && (
          <span className="muted text-xs"> · {item.presenter}</span>
        )}
      </span>
      <span className="muted text-xs" style={{ whiteSpace: "nowrap" }}>
        {item.docs.length > 0
          ? `${item.docs.length} doc${item.docs.length === 1 ? "" : "s"}`
          : ""}
      </span>
      {editable ? (
        <span className="agenda-manage-actions">
          <button
            className="btn btn-sm btn-ghost"
            onClick={onEdit}
            title="Edit item (rename, renumber, presenter, vote status)"
          >
            <Icon name="edit" size={12} />
          </button>
          <button
            className="btn btn-sm btn-ghost"
            style={{ color: "var(--danger)" }}
            disabled={deleting}
            onClick={onDelete}
            title="Delete item"
          >
            <Icon name="x" size={12} />
          </button>
        </span>
      ) : (
        <span />
      )}
    </div>
  );
}

/** Draft is seeded once on mount so background refetches can't clobber
 *  in-progress edits (same pattern as AgendaEditForm on the reader). */
function ManageEditForm({
  item,
  saving,
  onSave,
  onCancel,
}: {
  item: AgendaItem;
  saving: boolean;
  onSave: (draft: ItemDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<ItemDraft>(() => ({
    item_id: item.item_id ?? "",
    title: item.title,
    presenter: item.presenter ?? "",
    time_slot: item.time_slot ?? "",
    vote_status: item.vote_status ?? "",
  }));

  return (
    <div
      className="agenda-manage-edit"
      style={{ marginLeft: item.depth * 20 }}
    >
      <div className="row" style={{ gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "0 0 80px" }}>
          <label className="field-label">Item ID</label>
          <input
            className="input"
            placeholder="e.g. 7 or 7.a"
            value={draft.item_id}
            onChange={(e) => setDraft({ ...draft, item_id: e.target.value })}
          />
        </div>
        <div style={{ flex: 3, minWidth: 220 }}>
          <label className="field-label">Title</label>
          <input
            className="input"
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
        </div>
      </div>
      <div className="row" style={{ gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 2, minWidth: 180 }}>
          <label className="field-label">Presenter</label>
          <input
            className="input"
            value={draft.presenter}
            onChange={(e) => setDraft({ ...draft, presenter: e.target.value })}
          />
        </div>
        <div style={{ flex: 1, minWidth: 120 }}>
          <label className="field-label">Time slot</label>
          <input
            className="input"
            placeholder="9:00 AM"
            value={draft.time_slot}
            onChange={(e) => setDraft({ ...draft, time_slot: e.target.value })}
          />
        </div>
        <div style={{ flex: 1, minWidth: 150 }}>
          <label className="field-label">Vote status</label>
          <input
            className="input"
            placeholder="Vote — Approved"
            value={draft.vote_status}
            onChange={(e) => setDraft({ ...draft, vote_status: e.target.value })}
          />
        </div>
      </div>
      <div className="row" style={{ gap: 8 }}>
        <button
          className="btn btn-sm btn-accent"
          disabled={saving || !draft.title.trim()}
          onClick={() => onSave(draft)}
        >
          <Icon name="check" size={12} /> {saving ? "Saving…" : "Save changes"}
        </button>
        <button className="btn btn-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
