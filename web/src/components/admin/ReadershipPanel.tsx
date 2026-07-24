import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type ViewEntityType } from "../../lib/api";
import { qk } from "../../lib/queries";

const DAY_CHOICES = [7, 30, 90] as const;

const TYPE_LABEL: Record<ViewEntityType, string> = {
  meeting: "meeting",
  briefing: "briefing",
  docket: "docket",
  roundup: "roundup",
  deep_dive: "deep dive",
};

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

/** Admin → Readership: who's actually reading what (page-view beacons,
 *  deduped server-side to one row per user+entity per 30 min). */
export function ReadershipPanel() {
  const [days, setDays] = useState<number>(30);
  const summary = useQuery({
    queryKey: qk.viewsSummary(days),
    queryFn: () => api.viewsSummary(days),
  });
  const recent = useQuery({
    queryKey: qk.viewsRecent,
    queryFn: api.viewsRecent,
  });

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", gap: 12 }}>
        <h2 className="section-head" style={{ marginBottom: 0 }}>Readership</h2>
        <div className="row" style={{ gap: 4 }}>
          {DAY_CHOICES.map((d) => (
            <button
              key={d}
              className={`btn btn-sm ${days === d ? "btn-accent" : "btn-ghost"}`}
              onClick={() => setDays(d)}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>
      <p className="muted text-sm" style={{ margin: "6px 0 12px" }}>
        Who's reading what — one view per person per page per half hour.
      </p>

      {summary.isLoading ? (
        <div className="muted">Loading…</div>
      ) : (summary.data ?? []).length === 0 ? (
        <div className="empty">No reads recorded in the last {days} days.</div>
      ) : (
        <div className="usage-table" style={{ marginBottom: 24 }}>
          <div className="usage-row usage-row-head">
            <div style={{ flex: 2 }}>Page</div>
            <div style={{ flex: 0.6 }}>Type</div>
            <div style={{ flex: 0.5, textAlign: "right" }}>Views</div>
            <div style={{ flex: 0.6, textAlign: "right" }}>Readers</div>
            <div style={{ flex: 1 }}>Last read</div>
          </div>
          {(summary.data ?? []).map((r) => (
            <div className="usage-row" key={`${r.entity_type}-${r.entity_id}`}>
              <div style={{ flex: 2 }}>{r.title}</div>
              <div style={{ flex: 0.6 }} className="mono text-xs">
                {TYPE_LABEL[r.entity_type] ?? r.entity_type}
              </div>
              <div style={{ flex: 0.5, textAlign: "right" }} className="mono">
                {r.views}
              </div>
              <div style={{ flex: 0.6, textAlign: "right" }} className="mono">
                {r.unique_viewers}
              </div>
              <div style={{ flex: 1 }} className="muted text-xs">
                {when(r.last_viewed_at)}
              </div>
            </div>
          ))}
        </div>
      )}

      <h3 className="section-head" style={{ fontSize: 14 }}>Recent reads</h3>
      {recent.isLoading ? (
        <div className="muted">Loading…</div>
      ) : (recent.data ?? []).length === 0 ? (
        <div className="empty">Nothing yet.</div>
      ) : (
        <div className="usage-table">
          {(recent.data ?? []).map((r, i) => (
            <div className="usage-row" key={i}>
              <div style={{ flex: 1.2 }} className="text-xs">{r.user_email}</div>
              <div style={{ flex: 2 }}>
                {r.title}
                <span className="muted text-xs mono"> · {TYPE_LABEL[r.entity_type] ?? r.entity_type}</span>
              </div>
              <div style={{ flex: 0.9 }} className="muted text-xs">{when(r.viewed_at)}</div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
