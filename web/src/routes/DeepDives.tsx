import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { TypeTag } from "../components/Tag";
import { api, type DeepDive, type DeepDiveSource } from "../lib/api";
import { qk, useCan } from "../lib/queries";
import { toast } from "../lib/toast";
import type { MeetingListItem } from "../types";

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function StatusPill({ d }: { d: DeepDive }) {
  if (d.status === "generating") {
    return (
      <span className="ru-status ru-status-generating">
        <Icon name="refresh" size={12} /> Generating…
      </span>
    );
  }
  if (d.status === "complete") {
    return (
      <span className="ru-status ru-status-ready">
        <Icon name="check" size={12} /> Ready
      </span>
    );
  }
  if (d.status === "error") {
    return <span className="ru-status ru-status-error">Failed — open to retry</span>;
  }
  return <span className="text-xs muted">Draft</span>;
}

function sourceSummary(sources: DeepDiveSource[]): string {
  const meetings = new Set(sources.map((s) => s.meeting_id));
  return `${sources.length} doc${sources.length === 1 ? "" : "s"} · ${
    meetings.size
  } meeting${meetings.size === 1 ? "" : "s"}`;
}

// ── Builder: pick documents across meetings ───────────────────────────────

function MeetingDocsPicker({
  meeting,
  selected,
  onToggle,
}: {
  meeting: MeetingListItem;
  selected: Set<number>;
  onToggle: (docId: number, filename: string) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: qk.meetingDocs(meeting.id),
    queryFn: () => api.meetingDocuments(meeting.id),
  });

  const docs = useMemo(() => {
    if (!data) return [];
    const all = [...data.unassigned, ...Object.values(data.by_item).flat()];
    // De-dup defensively; a doc should only appear in one bucket.
    const seen = new Set<number>();
    return all.filter((d) => {
      if (seen.has(d.id)) return false;
      seen.add(d.id);
      return true;
    });
  }, [data]);

  if (isLoading) {
    return <div className="muted text-xs" style={{ padding: "6px 12px" }}>Loading documents…</div>;
  }
  if (docs.length === 0) {
    return <div className="muted text-xs" style={{ padding: "6px 12px" }}>No documents.</div>;
  }
  return (
    <div className="dd-doc-list">
      {docs.map((d) => (
        <label key={d.id} className="dd-doc-row">
          <input
            type="checkbox"
            checked={selected.has(d.id)}
            onChange={() => onToggle(d.id, d.filename)}
          />
          <span className="dd-doc-name">{d.filename}</span>
        </label>
      ))}
    </div>
  );
}

function Builder({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [search, setSearch] = useState("");
  const [openMeeting, setOpenMeeting] = useState<number | null>(null);
  const [selected, setSelected] = useState<Map<number, string>>(new Map());

  const { data: meetings = [] } = useQuery({
    queryKey: qk.meetingsWindow(730, 365),
    queryFn: () => api.meetings({ past_days: 730, future_days: 365 }),
  });

  const withDocs = useMemo(() => {
    const q = search.trim().toLowerCase();
    return meetings
      .filter((m) => m.doc_count > 0)
      .filter(
        (m) =>
          !q ||
          (m.title || "").toLowerCase().includes(q) ||
          m.type_short.toLowerCase().includes(q) ||
          m.meeting_date.includes(q),
      )
      .sort((a, b) => b.meeting_date.localeCompare(a.meeting_date))
      .slice(0, 30);
  }, [meetings, search]);

  const toggle = (docId: number, filename: string) => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(docId)) next.delete(docId);
      else next.set(docId, filename);
      return next;
    });
  };

  const create = useMutation({
    mutationFn: () =>
      api.createDeepDive({
        title: title.trim(),
        document_ids: [...selected.keys()],
      }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: qk.deepDives });
      toast.success("Deep dive started.");
      navigate(`/deep-dive/${d.id}`);
    },
    onError: (e: Error) => toast.error(`Couldn't start the deep dive: ${e.message}`),
  });

  const canRun = title.trim().length > 0 && selected.size > 0 && !create.isPending;

  return (
    <div className="dd-builder">
      <div className="dd-builder-head">
        <h2 className="section-head" style={{ margin: 0 }}>
          New deep dive
        </h2>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>
          <Icon name="x" size={12} /> Cancel
        </button>
      </div>

      <label className="field-label" htmlFor="dd-title">
        Report title
      </label>
      <input
        id="dd-title"
        className="dd-title-input"
        placeholder="e.g. CAR-SA accreditation proposals, Oct 2025 – May 2026"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />

      <div className="dd-builder-cols">
        <div className="dd-pick-col">
          <div className="field-label">Pick documents ({selected.size} selected)</div>
          <div className="row" style={{ gap: 6, marginBottom: 8 }}>
            <Icon name="search" size={13} />
            <input
              className="dd-search-input"
              placeholder="Filter meetings…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="dd-meeting-list">
            {withDocs.map((m) => (
              <div key={m.id} className="dd-meeting">
                <button
                  type="button"
                  className="dd-meeting-head"
                  onClick={() =>
                    setOpenMeeting(openMeeting === m.id ? null : m.id)
                  }
                >
                  <Icon
                    name={openMeeting === m.id ? "chev-d" : "chev-r"}
                    size={12}
                  />
                  <span className="mono text-xs muted">{m.meeting_date}</span>
                  <TypeTag>{m.type_short}</TypeTag>
                  <span className="dd-meeting-title">
                    {m.title || m.type_name}
                  </span>
                  <span className="muted text-xs">{m.doc_count} docs</span>
                </button>
                {openMeeting === m.id && (
                  <MeetingDocsPicker
                    meeting={m}
                    selected={new Set(selected.keys())}
                    onToggle={toggle}
                  />
                )}
              </div>
            ))}
            {withDocs.length === 0 && (
              <div className="muted text-xs" style={{ padding: 8 }}>
                No meetings with documents match.
              </div>
            )}
          </div>
        </div>

        <div className="dd-selected-col">
          <div className="field-label">Selected</div>
          {selected.size === 0 ? (
            <div className="muted text-xs">
              Nothing yet — expand a meeting and tick documents. Deep dives
              work best on 2–10 related documents, within or across meetings.
            </div>
          ) : (
            <ul className="dd-selected-list">
              {[...selected.entries()].map(([id, name]) => (
                <li key={id}>
                  <button
                    type="button"
                    className="dd-selected-remove"
                    title="Remove"
                    onClick={() =>
                      setSelected((prev) => {
                        const next = new Map(prev);
                        next.delete(id);
                        return next;
                      })
                    }
                  >
                    <Icon name="x" size={10} />
                  </button>
                  <span className="dd-doc-name">{name}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="dd-builder-foot">
        <span className="muted text-xs">
          One multimodal model pass over the full text + figures of every
          selected document.
        </span>
        <button
          className="btn btn-primary btn-sm"
          disabled={!canRun}
          onClick={() => create.mutate()}
        >
          <Icon name="spark" size={12} />
          {create.isPending ? "Starting…" : "Run deep dive"}
        </button>
      </div>
    </div>
  );
}

// ── List ──────────────────────────────────────────────────────────────────

export function DeepDives() {
  const navigate = useNavigate();
  const { canEdit } = useCan();
  const [building, setBuilding] = useState(false);

  const { data: reports = [], isLoading } = useQuery({
    queryKey: qk.deepDives,
    queryFn: () => api.listDeepDives(),
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status === "generating") ? 4000 : false,
    refetchIntervalInBackground: true,
  });

  return (
    <>
      <Topbar
        crumbs={[{ label: "Deep Dives" }]}
        actions={
          canEdit && !building && (
            <button
              className="btn btn-sm btn-primary"
              onClick={() => setBuilding(true)}
            >
              <Icon name="plus" /> New deep dive
            </button>
          )
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Special reports · cross-meeting</div>
          <h1 className="page-title">Deep Dives</h1>
          <p className="page-subtitle">
            Hand-pick documents — within or across meetings — and get one
            analyst-grade report comparing and synthesizing them.
          </p>
        </div>

        {canEdit && building && <Builder onClose={() => setBuilding(false)} />}

        {isLoading ? (
          <div className="empty">Loading…</div>
        ) : reports.length === 0 && !building ? (
          <div className="empty">
            No deep dives yet.
            {canEdit && (
              <div style={{ marginTop: 12 }}>
                <button className="btn btn-sm" onClick={() => setBuilding(true)}>
                  <Icon name="spark" size={12} /> Build your first
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="ru-list" style={{ marginTop: building ? 24 : 0 }}>
            {reports.map((d) => (
              <button
                key={d.id}
                className="ru-row"
                onClick={() => navigate(`/deep-dive/${d.id}`)}
              >
                <div className="ru-row-month">
                  <div className="ru-row-month-name">{d.title}</div>
                  <div className="ru-row-month-meta">
                    {sourceSummary(d.sources)} · {fmtDate(d.created_at)}
                  </div>
                </div>
                <div className="ru-row-committees">
                  {[...new Set(d.sources.map((s) => s.type_short))].map((c) => (
                    <TypeTag key={c}>{c}</TypeTag>
                  ))}
                </div>
                <div className="ru-row-status">
                  <StatusPill d={d} />
                </div>
                <div className="ru-row-chev">
                  <Icon name="chev-r" size={14} />
                </div>
              </button>
            ))}
          </div>
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
