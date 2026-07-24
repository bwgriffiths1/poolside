import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { UsersPanel } from "../components/admin/UsersPanel";
import { api, type ImageStorageStats } from "../lib/api";
import { qk } from "../lib/queries";
import { toast } from "../lib/toast";

function dollars(n: number, frac = 4): string {
  return `$${n.toFixed(frac)}`;
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function Admin() {
  const { data, isLoading, error } = useQuery({
    queryKey: qk.usageDashboard,
    queryFn: () => api.usageDashboard(),
  });

  // Pad trailing series to 6 months so the bar chart layout is stable.
  const series = useMemo(() => {
    if (!data) return [];
    const arr = [...data.trailing_six_months];
    return arr;
  }, [data]);

  const maxMonth = series.reduce((m, p) => Math.max(m, p.cost_usd), 0);

  return (
    <>
      <Topbar crumbs={[{ label: "Admin", to: "/admin" }]} />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Admin</div>
          <h1 className="page-title">Usage & users</h1>
          <p className="page-subtitle">
            Token + cost totals derived from completed summarize jobs, plus
            account management. Usage numbers reflect actual API spend
            captured at run time, not the pre-flight estimates.
          </p>
        </div>

        {isLoading && <div className="muted">Loading…</div>}
        {error && (
          <div className="empty" style={{ color: "var(--accent)" }}>
            Could not load usage: {(error as Error).message}
          </div>
        )}

        {data && (
          <>
            <section style={{ marginBottom: 28 }}>
              <h2 className="section-head">{data.month_label}</h2>
              <div className="usage-grid">
                <Tile label="Total cost" value={dollars(data.this_month.cost_usd, 2)} sub={`${data.this_month.jobs} job${data.this_month.jobs === 1 ? "" : "s"}`} />
                <Tile label="Input tokens" value={compact(data.this_month.input_tokens)} sub="this month" />
                <Tile label="Output tokens" value={compact(data.this_month.output_tokens)} sub="this month" />
                <Tile
                  label="vs. last month"
                  value={
                    data.last_month.jobs > 0
                      ? `${data.last_month.cost_usd === 0 ? "—" : ((data.this_month.cost_usd / data.last_month.cost_usd - 1) * 100).toFixed(0) + "%"}`
                      : "—"
                  }
                  sub={`Last month: ${dollars(data.last_month.cost_usd, 2)}`}
                />
              </div>
            </section>

            <section style={{ marginBottom: 28 }}>
              <h2 className="section-head">By committee · this month</h2>
              {data.by_committee_this_month.length === 0 ? (
                <div className="empty">No completed summarize jobs yet this month.</div>
              ) : (
                <div className="usage-table">
                  <div className="usage-row usage-row-head">
                    <div style={{ flex: 0.7 }}>Venue</div>
                    <div style={{ flex: 0.7 }}>Committee</div>
                    <div style={{ flex: 0.5, textAlign: "right" }}>Jobs</div>
                    <div style={{ flex: 0.8, textAlign: "right" }}>Cost</div>
                    <div style={{ flex: 2 }} />
                  </div>
                  {data.by_committee_this_month.map((r, i) => {
                    const max = data.by_committee_this_month[0].cost_usd || 1;
                    const pct = Math.max(2, Math.round((r.cost_usd / max) * 100));
                    return (
                      <div className="usage-row" key={i}>
                        <div style={{ flex: 0.7 }} className="mono text-xs">{r.venue}</div>
                        <div style={{ flex: 0.7 }} className="mono text-xs">{r.committee}</div>
                        <div style={{ flex: 0.5, textAlign: "right" }} className="mono">{r.jobs}</div>
                        <div style={{ flex: 0.8, textAlign: "right" }} className="mono">{dollars(r.cost_usd, 2)}</div>
                        <div style={{ flex: 2 }}>
                          <div className="usage-bar">
                            <div className="usage-bar-fill" style={{ width: `${pct}%` }} />
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            <section>
              <h2 className="section-head">Trailing 6 months</h2>
              {series.length === 0 ? (
                <div className="empty">No completed summarize jobs yet.</div>
              ) : (
                <div className="usage-bars">
                  {series.map((p) => {
                    const pct = maxMonth > 0
                      ? Math.max(3, Math.round((p.cost_usd / maxMonth) * 100))
                      : 3;
                    return (
                      <div className="usage-bars-col" key={p.month}>
                        <div className="usage-bars-track" title={`${dollars(p.cost_usd, 2)} · ${p.jobs} job${p.jobs === 1 ? "" : "s"}`}>
                          <div className="usage-bars-fill" style={{ height: `${pct}%` }} />
                        </div>
                        <div className="usage-bars-label muted text-xs">
                          {p.month.slice(5)}
                        </div>
                        <div className="usage-bars-value text-xs mono">
                          {dollars(p.cost_usd, 2)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            {data.images && (
              <section style={{ marginTop: 48 }}>
                <ImageStoragePanel stats={data.images} />
              </section>
            )}
          </>
        )}

        {/* Outside the usage gate — a usage-fetch failure must not hide
            account management. */}
        <section style={{ marginTop: 48 }}>
          <UsersPanel />
        </section>

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}

function mb(bytes: number): string {
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

function ImageStoragePanel({ stats }: { stats: ImageStorageStats }) {
  const qc = useQueryClient();
  const prune = useMutation({
    mutationFn: () => api.pruneImages(),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: qk.usageDashboard });
      toast.success(
        res.deleted === 0
          ? "Nothing to prune — all stored images are referenced or recent."
          : `Pruned ${res.deleted} images, freed ${mb(res.freed_bytes)}.`,
      );
    },
    onError: (e: Error) => toast.error(`Prune failed: ${e.message}`),
  });

  const last = stats.last_prune;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", gap: 12 }}>
        <h2 className="section-head" style={{ marginBottom: 0 }}>
          Image storage
        </h2>
        <button
          className="btn btn-sm"
          disabled={prune.isPending}
          onClick={() => prune.mutate()}
          title="Delete extracted images no briefing references (30+ days old). They re-extract automatically if ever needed — ISO-NE keeps old materials up."
        >
          <Icon name="trash" size={12} />
          {prune.isPending ? "Pruning…" : "Prune now"}
        </button>
      </div>
      <p className="muted text-sm" style={{ margin: "6px 0 12px" }}>
        Extracted figure candidates are a regenerable cache; a weekly job
        (Sun 05:00 ET) removes the ones no briefing kept.
      </p>
      <div className="usage-grid">
        <Tile
          label="Stored"
          value={String(stats.stored)}
          sub={mb(stats.stored_bytes)}
        />
        <Tile
          label="Referenced"
          value={String(stats.referenced)}
          sub="kept by briefings & reports"
        />
        <Tile
          label="Prunable"
          value={String(stats.stored - stats.referenced)}
          sub={`${mb(stats.unreferenced_bytes)} reclaimable`}
        />
        <Tile
          label="Last prune"
          value={
            last
              ? new Date(last.at).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                })
              : "never"
          }
          sub={last ? `${last.deleted} deleted · ${mb(last.freed_bytes)}` : "weekly · Sun 05:00 ET"}
        />
      </div>
    </>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="usage-tile">
      <div className="usage-tile-label">{label}</div>
      <div className="usage-tile-num">{value}</div>
      <div className="usage-tile-sub">{sub}</div>
    </div>
  );
}
