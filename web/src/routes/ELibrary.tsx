import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { api, type DocketListItem } from "../lib/api";
import { qk, useCan } from "../lib/queries";

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function BriefStatus({ d }: { d: DocketListItem }) {
  if (!d.filing_count) {
    return <span className="text-xs muted">Not crawled yet</span>;
  }
  if (d.brief_status === "approved" || d.brief_status === "draft") {
    return (
      <span className="ru-status ru-status-ready">
        <Icon name="check" size={12} />
        State of play
      </span>
    );
  }
  return <span className="text-xs muted">No state of play yet</span>;
}

export function ELibrary() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { canEdit } = useCan();
  const [number, setNumber] = useState("");
  const [title, setTitle] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  const { data: dockets = [], isLoading } = useQuery({
    queryKey: qk.dockets,
    queryFn: () => api.dockets(),
  });

  const add = useMutation({
    mutationFn: () =>
      api.addDocket({
        docket_number: number.trim(),
        title: title.trim() || undefined,
      }),
    onSuccess: (res) => {
      setAddError(null);
      setNumber("");
      setTitle("");
      qc.invalidateQueries({ queryKey: qk.dockets });
      navigate(`/docket/${res.docket.id}`);
    },
    onError: (e) => setAddError(e instanceof Error ? e.message : String(e)),
  });

  const canAdd = number.trim().length > 0 && !add.isPending;

  return (
    <>
      <Topbar crumbs={[{ label: "FERC eLibrary" }]} />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">FERC dockets</div>
          <h1 className="page-title">eLibrary</h1>
          <p className="page-subtitle">
            Track a FERC docket: every filing summarized, the intervenor
            roster recorded, and a state-of-play report kept current as the
            proceeding moves.
          </p>
        </div>

        {canEdit && (
          <>
            <form
              className="el-add"
              onSubmit={(e) => {
                e.preventDefault();
                if (canAdd) add.mutate();
              }}
            >
              <input
                className="input el-add-number"
                placeholder="Docket number, e.g. ER26-925"
                value={number}
                onChange={(e) => setNumber(e.target.value)}
                spellCheck={false}
              />
              <input
                className="input el-add-title"
                placeholder="Label (optional)"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <button className="btn btn-accent" type="submit" disabled={!canAdd}>
                <Icon name="plus" size={12} />
                {add.isPending ? "Adding…" : "Track docket"}
              </button>
            </form>
            <p className="el-add-hint">
              Adding a docket starts the first crawl + summarization in the
              background — the initial sync on a busy docket can take a while.
            </p>

            {addError && (
              <div className="ru-error-banner">
                <Icon name="x" size={12} /> {addError}
              </div>
            )}
          </>
        )}

        {isLoading ? (
          <div className="empty">Loading…</div>
        ) : dockets.length === 0 ? (
          <div className="empty">
            No dockets tracked yet — add one above to get started.
          </div>
        ) : (
          <div className="ru-list">
            {dockets.map((d) => (
              <button
                key={d.id}
                className="el-row"
                onClick={() => navigate(`/docket/${d.id}`)}
              >
                <div>
                  <div className="el-row-number">{d.docket_number}</div>
                  <div className="el-row-title">{d.title || "Untitled docket"}</div>
                </div>
                <div className="el-row-meta">
                  <span>
                    <span className="mono">{d.filing_count ?? 0}</span> filing
                    {(d.filing_count ?? 0) === 1 ? "" : "s"}
                  </span>
                  <span>
                    <span className="mono">{d.intervenor_count ?? 0}</span>{" "}
                    intervention{(d.intervenor_count ?? 0) === 1 ? "" : "s"}
                  </span>
                  <span>latest {fmtDate(d.latest_filed_date)}</span>
                </div>
                <div className="ru-row-status">
                  <BriefStatus d={d} />
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
