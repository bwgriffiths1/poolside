import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { TypeTag } from "../components/Tag";
import { api, type DeepDive as DeepDiveData } from "../lib/api";
import { qk, useCan } from "../lib/queries";
import { Markdown } from "../lib/markdown";

function metaLine(d: DeepDiveData): string {
  const parts: string[] = [];
  const meetings = new Set(d.sources.map((s) => s.meeting_id));
  if (d.sources.length > 0) {
    parts.push(
      `${d.sources.length} source doc${d.sources.length === 1 ? "" : "s"} across ${
        meetings.size
      } meeting${meetings.size === 1 ? "" : "s"}`,
    );
  }
  if (d.model_id) parts.push(d.model_id);
  if (d.updated_at) {
    const dt = new Date(d.updated_at);
    if (!Number.isNaN(dt.getTime())) {
      parts.push(
        `generated ${dt.toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric",
        })}`,
      );
    }
  }
  return parts.join(" · ");
}

export function DeepDive() {
  const { id } = useParams();
  const rid = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { canEdit } = useCan();

  const {
    data: d,
    isLoading,
    isError,
  } = useQuery({
    queryKey: qk.deepDive(rid),
    queryFn: () => api.deepDive(rid),
    enabled: Number.isFinite(rid),
    refetchInterval: (query) =>
      query.state.data?.status === "generating" ? 3000 : false,
    // Generation takes minutes; keep polling while the tab is backgrounded.
    refetchIntervalInBackground: true,
  });

  const rerun = useMutation({
    mutationFn: () => api.rerunDeepDive(rid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.deepDive(rid) });
      qc.invalidateQueries({ queryKey: qk.deepDives });
    },
  });

  const del = useMutation({
    mutationFn: () => api.deleteDeepDive(rid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.deepDives });
      navigate("/deep-dives");
    },
  });

  if (isLoading) {
    return (
      <>
        <Topbar crumbs={[{ label: "Deep Dives", to: "/deep-dives" }, { label: "…" }]} />
        <div className="page">
          <div className="empty">Loading…</div>
        </div>
      </>
    );
  }

  if (isError || !d) {
    return (
      <>
        <Topbar
          crumbs={[{ label: "Deep Dives", to: "/deep-dives" }, { label: "Not found" }]}
        />
        <div className="page">
          <div className="empty">
            This deep dive doesn't exist (it may have been deleted).
          </div>
        </div>
      </>
    );
  }

  const generating = d.status === "generating" || rerun.isPending;

  const onRerun = () => {
    if (
      window.confirm(
        `Regenerate "${d.title}"? This re-runs the model over ` +
          `${d.sources.length} document${d.sources.length === 1 ? "" : "s"} and overwrites the current report.`,
      )
    ) {
      rerun.mutate();
    }
  };

  const onDelete = () => {
    if (window.confirm(`Delete "${d.title}"? This cannot be undone.`)) {
      del.mutate();
    }
  };

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Deep Dives", to: "/deep-dives" },
          { label: d.title },
        ]}
        actions={
          canEdit && (
            <>
              <button
                className="btn btn-ghost btn-sm"
                disabled={generating}
                onClick={onRerun}
              >
                <Icon name="refresh" size={12} />
                {d.status === "complete" ? "Regenerate" : "Generate"}
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
          )
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Deep Dive · Special report</div>
          <h1 className="page-title">{d.title}</h1>
          <p className="page-subtitle">{metaLine(d)}</p>
        </div>

        {d.sources.length > 0 && (
          <div className="ru-sources">
            <span className="ru-sources-label">Sources</span>
            {d.sources.map((s) => (
              <button
                key={s.document_id}
                className="ru-source-chip"
                onClick={() => navigate(`/meeting/${s.meeting_id}`)}
                title={`${s.type_name} — ${s.meeting_date}`}
              >
                <TypeTag>{s.type_short}</TypeTag>
                <span className="dd-source-filename">{s.filename}</span>
              </button>
            ))}
          </div>
        )}

        {rerun.isError && (
          <div className="ru-error-banner">
            <Icon name="x" size={12} />{" "}
            {rerun.error instanceof Error
              ? rerun.error.message
              : String(rerun.error)}
          </div>
        )}

        {d.status === "generating" ? (
          <div className="ru-generating">
            <Icon name="refresh" size={16} />
            <div>
              <div className="ru-generating-title">
                Reading {d.sources.length} document
                {d.sources.length === 1 ? "" : "s"}…
              </div>
              <div className="ru-generating-progress">
                One multimodal pass over full text + figures. This can take a
                few minutes.
              </div>
            </div>
          </div>
        ) : d.status === "error" ? (
          <div className="ru-error-panel">
            <div className="ru-error-title">Generation failed</div>
            <div className="ru-error-detail">
              {d.error_message || "Unknown error."}
            </div>
            {canEdit && (
              <button
                className="btn btn-sm"
                disabled={rerun.isPending}
                onClick={() => rerun.mutate()}
              >
                <Icon name="refresh" size={12} /> Retry
              </button>
            )}
          </div>
        ) : d.report_md ? (
          <article className="ru-body">
            <Markdown source={d.report_md} preserveH2 />
          </article>
        ) : (
          <div className="empty">
            Not generated yet.
            {canEdit && (
              <div style={{ marginTop: 12 }}>
                <button
                  className="btn btn-sm"
                  disabled={rerun.isPending}
                  onClick={() => rerun.mutate()}
                >
                  <Icon name="spark" size={12} /> Generate report
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
