import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { api, type DocAssignment } from "../lib/api";
import { extFromFilename } from "../lib/format";
import type { AgendaItem } from "../types";

interface Props {
  meetingId: number;
  agenda: AgendaItem[];
}

interface ItemOption {
  id: number;
  item_id: string;
  title: string;
}

export function MaterialAssignment({ meetingId, agenda }: Props) {
  const qc = useQueryClient();
  const { data: docs, isLoading } = useQuery({
    queryKey: ["meeting-docs", meetingId],
    queryFn: () => api.meetingDocuments(meetingId),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
    qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
  };

  const assignMut = useMutation({
    mutationFn: ({ itemId, docId }: { itemId: number; docId: number }) =>
      api.assignDoc(itemId, docId),
    onSuccess: invalidate,
  });
  const ignoreMut = useMutation({
    mutationFn: ({ docId, ignored }: { docId: number; ignored: boolean }) =>
      api.setDocIgnored(docId, ignored),
    onSuccess: invalidate,
  });

  const itemOptions: ItemOption[] = agenda
    .filter((i) => i.id > 0) // exclude synthesized parents (negative ids)
    .map((i) => ({
      id: i.id,
      item_id: i.item_id || "—",
      title: i.title,
    }));

  if (isLoading || !docs) {
    return null;
  }

  const unassigned = docs.unassigned;
  const ignored = docs.ignored;

  return (
    <>
      {unassigned.length > 0 && (
        <UnassignedSection
          unassigned={unassigned}
          itemOptions={itemOptions}
          onAssign={(itemId, docId) =>
            assignMut.mutate({ itemId, docId })
          }
          onIgnore={(docId) => ignoreMut.mutate({ docId, ignored: true })}
        />
      )}

      {ignored.length > 0 && (
        <IgnoredSection
          ignored={ignored}
          onRestore={(docId) =>
            ignoreMut.mutate({ docId, ignored: false })
          }
        />
      )}
    </>
  );
}

function UnassignedSection({
  unassigned,
  itemOptions,
  onAssign,
  onIgnore,
}: {
  unassigned: DocAssignment[];
  itemOptions: ItemOption[];
  onAssign: (itemId: number, docId: number) => void;
  onIgnore: (docId: number) => void;
}) {
  return (
    <div className="assign-card" style={{ marginBottom: 24 }}>
      <div className="row" style={{ marginBottom: 12 }}>
        <span
          className="field-label"
          style={{ marginBottom: 0, color: "var(--warn)" }}
        >
          ⚠ Unassigned materials ({unassigned.length})
        </span>
        <span className="spacer" />
        <span className="text-xs muted">
          Auto-assignment couldn't place these. Assign manually or ignore.
        </span>
      </div>
      <div className="assign-list">
        {unassigned.map((d) => (
          <div className="assign-row" key={d.id}>
            <Icon name="doc" />
            <div className="truncate" style={{ fontSize: 13 }}>
              {d.filename}
            </div>
            <span className="mono text-xs muted">
              {extFromFilename(d.filename)}
            </span>
            <ItemPicker
              placeholder="Assign to…"
              options={itemOptions}
              onChange={(itemId) => onAssign(itemId, d.id)}
            />
            <button
              className="btn btn-sm"
              onClick={() => onIgnore(d.id)}
              title="Hide this document from briefing generation"
            >
              <Icon name="x" size={12} /> Ignore
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function IgnoredSection({
  ignored,
  onRestore,
}: {
  ignored: DocAssignment[];
  onRestore: (docId: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="ignored-card"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      style={{ marginTop: 16 }}
    >
      <summary>
        <Icon name={open ? "chev-d" : "chev-r"} size={12} />
        <span className="field-label" style={{ marginBottom: 0, marginLeft: 6 }}>
          Ignored documents ({ignored.length})
        </span>
      </summary>
      <div className="assign-list" style={{ marginTop: 12 }}>
        {ignored.map((d) => (
          <div className="assign-row" key={d.id}>
            <Icon name="x" />
            <div className="truncate" style={{ fontSize: 13 }}>
              {d.filename}
            </div>
            <span className="mono text-xs muted">
              {extFromFilename(d.filename)}
            </span>
            <span />
            <button
              className="btn btn-sm"
              onClick={() => onRestore(d.id)}
              title="Restore to unassigned"
            >
              <Icon name="refresh" size={12} /> Restore
            </button>
          </div>
        ))}
      </div>
    </details>
  );
}

export function ItemPicker({
  placeholder,
  options,
  onChange,
  currentItemId,
}: {
  placeholder: string;
  options: ItemOption[];
  onChange: (itemId: number) => void;
  currentItemId?: number;
}) {
  return (
    <select
      className="select"
      style={{ width: 220, fontSize: 12.5 }}
      value=""
      onChange={(e) => {
        const v = Number(e.target.value);
        if (v) onChange(v);
      }}
    >
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o.id} value={o.id} disabled={o.id === currentItemId}>
          {o.item_id} — {o.title.length > 50 ? o.title.slice(0, 50) + "…" : o.title}
        </option>
      ))}
    </select>
  );
}

export function PerItemDocControls({
  meetingId,
  docId,
  itemId,
  agenda,
}: {
  meetingId: number;
  docId: number;
  itemId: number;
  agenda: AgendaItem[];
}) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["meeting-docs", meetingId] });
    qc.invalidateQueries({ queryKey: ["meeting", meetingId] });
  };
  const reassignMut = useMutation({
    mutationFn: ({ newItemId }: { newItemId: number }) =>
      api.reassignDoc(docId, newItemId, meetingId),
    onSuccess: invalidate,
  });
  const unassignMut = useMutation({
    mutationFn: () => api.unassignDoc(itemId, docId, meetingId),
    onSuccess: invalidate,
  });

  const itemOptions: ItemOption[] = agenda
    .filter((i) => i.id > 0)
    .map((i) => ({
      id: i.id,
      item_id: i.item_id || "—",
      title: i.title,
    }));

  return (
    <div className="row" style={{ gap: 4 }}>
      <ItemPicker
        placeholder="Move…"
        options={itemOptions}
        currentItemId={itemId}
        onChange={(newItemId) => reassignMut.mutate({ newItemId })}
      />
      <button
        className="btn btn-sm btn-ghost"
        title="Unassign from this item"
        onClick={() => unassignMut.mutate()}
      >
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}
