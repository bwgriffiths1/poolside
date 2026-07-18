import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { api } from "../../lib/api";
import { qk } from "../../lib/queries";
import { toast } from "../../lib/toast";

export function AddAgendaItem({ meetingId }: { meetingId: number }) {
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
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      setOpen(false);
      setDraft({ item_id: "", title: "", presenter: "" });
    },
    onError: (e: Error) => toast.error(`Add failed: ${e.message}`),
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
