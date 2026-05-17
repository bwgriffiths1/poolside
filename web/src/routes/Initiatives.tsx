import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { VenueTag, TypeTag } from "../components/Tag";
import { api } from "../lib/api";

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
    queryKey: ["initiatives"],
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

export function InitiativeDetail() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery({
    queryKey: ["initiative", code],
    queryFn: () => api.getInitiative(code as string),
    enabled: !!code,
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
