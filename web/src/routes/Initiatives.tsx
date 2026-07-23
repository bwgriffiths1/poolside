import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { VenueTag, TypeTag } from "../components/Tag";
import { api, type InitiativeBrief } from "../lib/api";
import { qk, useCan } from "../lib/queries";
import { Markdown } from "../lib/markdown";
import { toast } from "../lib/toast";

// Day-granularity relative label for meeting *dates* (coarser on purpose than
// lib/format's formatRel, which is for timestamps).
function rel(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "future";
  const day = Math.floor(ms / 86_400_000);
  if (day < 1) return "today";
  if (day < 30) return `${day}d ago`;
  if (day < 365) return `${Math.floor(day / 30)}mo ago`;
  return `${Math.floor(day / 365)}y ago`;
}

export function Initiatives() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");

  const { data = [], isLoading, error } = useQuery({
    queryKey: qk.initiatives,
    queryFn: () => api.listInitiatives(),
  });

  const q = search.trim().toLowerCase();
  const filtered = q
    ? data.filter(
        (i) =>
          i.code.toLowerCase().includes(q) ||
          (i.description ?? "").toLowerCase().includes(q),
      )
    : data;

  return (
    <>
      <Topbar crumbs={[{ label: "Initiatives" }]} />
      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Cross-meeting threads</div>
          <h1 className="page-title">Initiatives</h1>
          <p className="page-subtitle">
            Topics tagged on agenda items at ingest time — track how each
            thread moves across committees and meetings over time.
          </p>
        </div>

        <div
          className="row"
          style={{
            gap: 6,
            background: "var(--bg-elev)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "4px 10px",
            maxWidth: 360,
            marginBottom: 16,
          }}
        >
          <Icon name="search" size={13} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter initiatives…"
            style={{
              border: 0,
              outline: 0,
              background: "transparent",
              color: "inherit",
              fontSize: 13,
              width: "100%",
              fontFamily: "inherit",
            }}
          />
        </div>

        {isLoading && <div className="muted">Loading…</div>}
        {error && (
          <div className="empty" style={{ color: "var(--accent)" }}>
            Couldn't load initiatives: {(error as Error).message}
          </div>
        )}
        {!isLoading && filtered.length === 0 && (
          <div className="empty">
            No initiative tags yet — they're populated on ingest when the
            agenda parser detects codes like <code>CAR-SA</code>,{" "}
            <code>GISWG</code>, etc.
          </div>
        )}

        <div className="initiative-list">
          {filtered.map((i) => (
            <button
              key={i.tag_id}
              className="initiative-row"
              onClick={() =>
                navigate(`/initiatives/${encodeURIComponent(i.code)}`)
              }
            >
              <div className="initiative-row-code mono">{i.code}</div>
              <div className="initiative-row-main">
                {i.description && (
                  <div className="text-sm">{i.description}</div>
                )}
                <div className="muted text-xs" style={{ marginTop: 2 }}>
                  {i.item_count} item{i.item_count === 1 ? "" : "s"} across{" "}
                  {i.meeting_count} meeting{i.meeting_count === 1 ? "" : "s"} ·
                  last touched {rel(i.latest_meeting_date)}
                  {i.brief_status === "complete" && (
                    <span className="ib-briefed-pill" title="Brief available">
                      Briefed
                    </span>
                  )}
                </div>
              </div>
              <Icon name="chev-r" size={14} />
            </button>
          ))}
        </div>

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}

function briefMetaLine(b: InitiativeBrief): string {
  const parts: string[] = [];
  if (b.source_item_count != null) {
    parts.push(`${b.source_item_count} item${b.source_item_count === 1 ? "" : "s"}`);
  }
  if (b.model_id) parts.push(b.model_id);
  if (b.cost_usd != null) parts.push(`$${b.cost_usd.toFixed(2)}`);
  if (b.generated_at) {
    const d = new Date(b.generated_at);
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

function BriefSection({
  code,
  brief,
  itemCount,
}: {
  code: string;
  brief: InitiativeBrief | null;
  itemCount: number;
}) {
  const qc = useQueryClient();
  const { canEdit } = useCan();
  const generate = useMutation({
    mutationFn: () => api.generateInitiativeBrief(code),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.initiative(code) });
      qc.invalidateQueries({ queryKey: qk.initiatives });
    },
    onError: (err: Error) => toast.error(`Couldn't start the brief: ${err.message}`),
  });

  const generating = brief?.status === "generating" || generate.isPending;
  const hasBrief = brief?.status === "complete" && !!brief.brief_md;

  const onRegenerate = () => {
    if (
      window.confirm(
        `Regenerate the ${code} brief? This re-runs the model over ` +
          `${itemCount} tagged item${itemCount === 1 ? "" : "s"} and overwrites the current brief.`,
      )
    ) {
      generate.mutate();
    }
  };

  if (!brief || brief.status === "draft") {
    return (
      <div className="ib-empty">
        <div>
          <div className="ib-empty-title">No brief yet.</div>
          <div className="muted text-sm">
            Synthesize the {itemCount} tagged item{itemCount === 1 ? "" : "s"}{" "}
            into one "story so far" narrative.
          </div>
        </div>
        {canEdit && (
          <button
            className="btn btn-sm btn-primary"
            disabled={generating || itemCount === 0}
            onClick={() => generate.mutate()}
          >
            <Icon name="spark" size={12} /> Generate brief
          </button>
        )}
      </div>
    );
  }

  if (generating) {
    return (
      <div className="ib-generating">
        <Icon name="refresh" size={16} />
        <div>
          <div className="ib-generating-title">Synthesizing the story so far…</div>
          <div className="muted text-xs">
            One model pass over {itemCount} tagged item{itemCount === 1 ? "" : "s"}.
            This usually takes a minute or two.
          </div>
        </div>
      </div>
    );
  }

  if (brief.status === "error") {
    return (
      <div className="ib-error">
        <div className="ib-error-title">Brief generation failed</div>
        <div className="muted text-sm">{brief.error_message || "Unknown error."}</div>
        {canEdit && (
          <button
            className="btn btn-sm"
            disabled={generate.isPending}
            onClick={() => generate.mutate()}
          >
            <Icon name="refresh" size={12} /> Retry
          </button>
        )}
      </div>
    );
  }

  if (!hasBrief) return null;

  return (
    <div className="ib-brief">
      <div className="ib-brief-head">
        <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
          <h2 className="section-head" style={{ margin: 0 }}>
            The story so far
          </h2>
          {brief.stale && (
            <span
              className="ib-stale-pill"
              title="New tagged items have landed since this brief was generated."
            >
              Stale
            </span>
          )}
        </div>
        <div className="row" style={{ gap: 8 }}>
          <span className="muted text-xs">{briefMetaLine(brief)}</span>
          {canEdit && (
            <button
              className="btn btn-ghost btn-sm"
              disabled={generating}
              onClick={onRegenerate}
            >
              <Icon name="refresh" size={12} /> Regenerate
            </button>
          )}
        </div>
      </div>
      <article className="ib-brief-body">
        <Markdown source={brief.brief_md!} preserveH2 />
      </article>
    </div>
  );
}

export function InitiativeDetail() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery({
    queryKey: qk.initiative(code ?? ""),
    queryFn: () => api.getInitiative(code as string),
    enabled: !!code,
    refetchInterval: (query) =>
      query.state.data?.brief?.status === "generating" ? 3000 : false,
    // Generation takes a minute or two; keep polling while backgrounded.
    refetchIntervalInBackground: true,
  });

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Initiatives", to: "/initiatives" },
          { label: code ?? "" },
        ]}
      />
      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Cross-meeting thread</div>
          <h1 className="page-title mono" style={{ fontSize: 32 }}>
            {code}
          </h1>
          {data?.description && (
            <p className="page-subtitle">{data.description}</p>
          )}
          {data && (
            <div className="muted text-sm" style={{ marginTop: 6 }}>
              {data.item_count} agenda item{data.item_count === 1 ? "" : "s"}{" "}
              across {data.meeting_count} meeting
              {data.meeting_count === 1 ? "" : "s"}
            </div>
          )}
        </div>

        {isLoading && <div className="muted">Loading…</div>}
        {error && (
          <div className="empty" style={{ color: "var(--accent)" }}>
            Couldn't load this initiative: {(error as Error).message}
          </div>
        )}

        {data && code && (
          <BriefSection
            code={code}
            brief={data.brief}
            itemCount={data.item_count}
          />
        )}

        {data && data.items.length > 0 && (
          <div className="section-h" style={{ marginTop: 28 }}>
            <h2>Appearances</h2>
            <span className="meta">
              {data.item_count} item{data.item_count === 1 ? "" : "s"}
            </span>
          </div>
        )}
        {data && data.items.length === 0 && (
          <div className="empty">No tagged items yet.</div>
        )}

        <div className="initiative-detail-list">
          {data?.items.map((it, idx) => (
            <div
              key={`${it.meeting_id}-${it.item_id}-${idx}`}
              className="initiative-detail-row"
            >
              <div className="initiative-detail-head">
                <span className="mono text-xs muted">{it.meeting_date}</span>
                <VenueTag>{it.venue}</VenueTag>
                <TypeTag>{it.type_short}</TypeTag>
                <span style={{ flex: 1 }} />
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => navigate(`/meeting/${it.meeting_id}`)}
                >
                  Open meeting <Icon name="arrow-r" size={11} />
                </button>
              </div>
              <div className="initiative-detail-title">
                <span className="mono text-xs muted">{it.item_id}</span>{" "}
                <strong>{it.item_title}</strong>
                {it.presenter && (
                  <span className="muted text-xs">
                    {" "}
                    · {it.presenter}
                    {it.organization ? ` (${it.organization})` : ""}
                  </span>
                )}
                {it.vote_status && (
                  <span className={`vote-pill ${it.vote_status.toLowerCase().includes("approved") ? "approved" : ""}`} style={{ marginLeft: 6 }}>
                    {it.vote_status}
                  </span>
                )}
              </div>
              {it.summary_snippet ? (
                <div
                  className="serif"
                  style={{
                    fontSize: 14,
                    lineHeight: 1.55,
                    color: "var(--ink-soft)",
                    marginTop: 6,
                  }}
                >
                  {it.summary_snippet}
                </div>
              ) : (
                <div className="muted text-xs" style={{ marginTop: 4 }}>
                  No summary yet.
                </div>
              )}
            </div>
          ))}
        </div>

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
