import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { TypeTag } from "../components/Tag";
import { VersionHistory } from "../components/VersionHistory";
import { api } from "../lib/api";
import { qk, useCan } from "../lib/queries";
import { fmtDateRange } from "../lib/format";
import { Markdown } from "../lib/markdown";
import type { Roundup as RoundupData } from "../types";
import { useTrackView } from "../hooks/useTrackView";

function metaLine(r: RoundupData): string {
  const parts: string[] = [];
  if (r.sources.length > 0) {
    parts.push(
      `${r.sources.length} source briefing${r.sources.length === 1 ? "" : "s"}`,
    );
  }
  if (r.model_id) parts.push(r.model_id);
  if (r.cost_usd != null) parts.push(`$${r.cost_usd.toFixed(2)}`);
  if (r.updated_at) {
    const d = new Date(r.updated_at);
    if (!Number.isNaN(d.getTime())) {
      parts.push(
        `generated ${d.toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric",
        })}`,
      );
    }
  }
  return parts.join(" · ");
}

export function Roundup() {
  const { id } = useParams();
  const rid = Number(id);
  useTrackView("roundup", rid);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { canEdit } = useCan();
  const [showVersions, setShowVersions] = useState(false);

  const {
    data: r,
    isLoading,
    isError,
  } = useQuery({
    queryKey: qk.roundup(rid),
    queryFn: () => api.roundup(rid),
    enabled: Number.isFinite(rid),
    refetchInterval: (query) =>
      query.state.data?.status === "generating" ? 3000 : false,
    // Generation takes minutes; keep polling while the tab is backgrounded.
    refetchIntervalInBackground: true,
  });

  const regenerate = useMutation({
    mutationFn: () => api.generateRoundup(r!.venue, r!.month),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.roundup(rid) });
      qc.invalidateQueries({ queryKey: qk.roundups });
      qc.invalidateQueries({ queryKey: qk.summary("roundup", rid) });
      qc.invalidateQueries({
        queryKey: qk.summaryVersions("roundup", rid),
      });
    },
  });

  const del = useMutation({
    mutationFn: () => api.deleteRoundup(rid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.roundups });
      navigate("/roundups");
    },
  });

  if (isLoading) {
    return (
      <>
        <Topbar crumbs={[{ label: "Roundups", to: "/roundups" }, { label: "…" }]} />
        <div className="page">
          <div className="empty">Loading…</div>
        </div>
      </>
    );
  }

  if (isError || !r) {
    return (
      <>
        <Topbar crumbs={[{ label: "Roundups", to: "/roundups" }, { label: "Not found" }]} />
        <div className="page">
          <div className="empty">This roundup doesn't exist (it may have been deleted).</div>
        </div>
      </>
    );
  }

  const generating = r.status === "generating" || regenerate.isPending;

  const onRegenerate = () => {
    if (
      window.confirm(
        `Regenerate the ${r.month_label} roundup? This re-runs the model over ` +
          `${r.sources.length || "the month's"} briefings and replaces the ` +
          `current report (it stays restorable under Versions).`,
      )
    ) {
      regenerate.mutate();
    }
  };

  const onDelete = () => {
    if (window.confirm(`Delete the ${r.month_label} roundup? This cannot be undone.`)) {
      del.mutate();
    }
  };

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Roundups", to: "/roundups" },
          { label: r.month_label },
        ]}
        actions={
          <>
            {r.report_md && (
              // Plain same-origin link: the browser downloads natively via
              // Content-Disposition (the fetch→blob dance crashed Safari).
              <a
                className="btn btn-ghost btn-sm"
                href={`/api/roundups/${rid}/docx`}
                title="Word export of this roundup"
              >
                <Icon name="download" size={12} /> Word
              </a>
            )}
            {r.report_md && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setShowVersions(!showVersions)}
                title="Browse and restore previous versions of this roundup"
              >
                <Icon name="refresh" size={12} /> Versions
              </button>
            )}
            {canEdit && r.report_md && !generating && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => navigate(`/edit/roundup/${rid}`)}
              >
                <Icon name="edit" size={12} /> Edit
              </button>
            )}
            {canEdit && (
              <>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={generating}
                  onClick={onRegenerate}
                >
                  <Icon name="refresh" size={12} />
                  {r.status === "complete" ? "Regenerate" : "Generate"}
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={generating || del.isPending}
                  onClick={onDelete}
                >
                  <Icon name="trash" size={12} />
                  Delete
                </button>
              </>
            )}
          </>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Monthly Roundup · {r.venue}</div>
          <h1 className="page-title">{r.month_label}</h1>
          <p className="page-subtitle">{metaLine(r)}</p>
        </div>

        {r.sources.length > 0 && (
          <div className="ru-sources">
            <span className="ru-sources-label">Sources</span>
            {r.sources.map((s) => (
              <button
                key={s.meeting_id}
                className="ru-source-chip"
                onClick={() => navigate(`/briefing/${s.meeting_id}`)}
                title={s.type_name}
              >
                <TypeTag>{s.type_short}</TypeTag>
                <span>{fmtDateRange(s.meeting_date, s.end_date ?? undefined)}</span>
              </button>
            ))}
          </div>
        )}

        {showVersions && (
          <section style={{ margin: "16px 0 24px" }}>
            <div className="b-eyebrow">Version history</div>
            <VersionHistory
              entityType="roundup"
              entityId={rid}
              onRestored={() => {
                setShowVersions(false);
                qc.invalidateQueries({ queryKey: qk.roundup(rid) });
                qc.invalidateQueries({ queryKey: qk.roundups });
              }}
            />
          </section>
        )}

        {regenerate.isError && (
          <div className="ru-error-banner">
            <Icon name="x" size={12} />{" "}
            {regenerate.error instanceof Error
              ? regenerate.error.message
              : String(regenerate.error)}
          </div>
        )}

        {r.status === "generating" ? (
          <div className="ru-generating">
            <Icon name="refresh" size={16} />
            <div>
              <div className="ru-generating-title">Synthesizing the month…</div>
              <div className="ru-generating-progress">
                {r.progress_text || "Working…"}
              </div>
            </div>
          </div>
        ) : r.status === "error" ? (
          <div className="ru-error-panel">
            <div className="ru-error-title">Generation failed</div>
            <div className="ru-error-detail">{r.error_message || "Unknown error."}</div>
            {canEdit && (
              <button
                className="btn btn-sm"
                disabled={regenerate.isPending}
                onClick={() => regenerate.mutate()}
              >
                <Icon name="refresh" size={12} /> Retry
              </button>
            )}
          </div>
        ) : r.report_md ? (
          <article className="ru-body">
            <Markdown source={r.report_md} preserveH2 />
          </article>
        ) : (
          <div className="empty">
            Not generated yet.
            {canEdit && (
              <div style={{ marginTop: 12 }}>
                <button
                  className="btn btn-sm"
                  disabled={regenerate.isPending}
                  onClick={() => regenerate.mutate()}
                >
                  <Icon name="spark" size={12} /> Generate roundup
                </button>
              </div>
            )}
          </div>
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
