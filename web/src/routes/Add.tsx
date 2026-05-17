import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { Pill } from "../components/Pill";
import { api } from "../lib/api";

type Mode = "auto" | "manual";

export function Add() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("auto");

  const { data: recent = [] } = useQuery({
    queryKey: ["ingestJobs"],
    queryFn: () => api.ingestJobs(),
  });

  return (
    <>
      <Topbar
        crumbs={[{ label: "Add Meeting" }]}
        actions={
          <button className="btn btn-sm" onClick={() => navigate("/overview")}>
            <Icon name="x" size={12} /> Cancel
          </button>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Pipeline · Ingest</div>
          <h1 className="page-title">Add meetings to Poolside.</h1>
          <p className="page-subtitle">
            Either scrape upcoming meetings from the ISO-NE calendar, or add a
            single meeting by URL. Documents and agendas are ingested
            automatically; summarization is triggered separately from each
            meeting page.
          </p>
        </div>

        <Segmented
          value={mode}
          onChange={setMode}
          style={{ marginBottom: 24 }}
          options={[
            {
              value: "auto",
              label: (
                <>
                  <Icon name="refresh" size={12} /> Scrape calendars
                </>
              ),
            },
            {
              value: "manual",
              label: (
                <>
                  <Icon name="plus" size={12} /> Add manually
                </>
              ),
            },
          ]}
        />

        {mode === "auto" ? <AutoScrape /> : <ManualAdd />}

        <div className="section-h" style={{ marginTop: 48 }}>
          <h2>Recent ingest jobs</h2>
          <span className="meta">Last 30 days</span>
        </div>
        <div className="ingest-history">
          {recent.map((j) => (
            <div className="hist-row" key={j.id}>
              <div className="mono text-xs muted">{j.id}</div>
              <div>
                <div className="hist-label">{j.label}</div>
                <div className="muted text-xs">
                  {j.docs} docs · {j.agenda_items} agenda items
                </div>
              </div>
              <div className="mono text-xs muted">{j.started}</div>
              <div>
                <Pill status="complete" />
              </div>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => navigate(`/meeting/${j.meeting_id}`)}
              >
                Open <Icon name="arrow-r" size={11} />
              </button>
            </div>
          ))}
        </div>

        <div style={{ height: 80 }} />
      </div>
    </>
  );
}

// ─── Auto-scrape ────────────────────────────────────────────────────────────

function AutoScrape() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: venues = [] } = useQuery({
    queryKey: ["venues"],
    queryFn: () => api.venues(),
  });

  const discover = useMutation({
    mutationFn: () => api.triggerDiscover(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["venues"] });
      qc.invalidateQueries({ queryKey: ["meetings"] });
    },
  });

  const rel = (iso: string | null) => {
    if (!iso) return "never";
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60_000) return "just now";
    const min = Math.floor(ms / 60_000);
    if (min < 60) return `${min} min ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return `${Math.floor(hr / 24)}d ago`;
  };

  const totalDiscovered = discover.data
    ? Object.values(discover.data.discovered).reduce((n, v) => n + v, 0)
    : 0;

  return (
    <div className="card" style={{ padding: 20 }}>
      <h2 className="section-head" style={{ marginBottom: 6 }}>
        Scrape ISO-NE calendars
      </h2>
      <p className="muted text-sm" style={{ marginBottom: 16 }}>
        Visits every active committee calendar and creates stub meetings for
        any new events. The cron also runs this every morning at 6 AM — use
        the button here when you want results sooner. Documents and agendas
        are pulled separately by the refresh job (or the per-meeting
        Re-check button); this step only discovers new meeting rows.
      </p>

      <div className="venue-grid" style={{ marginBottom: 16 }}>
        {venues.map((v) => (
          <div key={v.id} className="venue-card" style={{ cursor: "default" }}>
            <div className="venue-card-title">{v.short_name}</div>
            <div className="muted text-xs">
              {v.name} · last scraped {rel(v.last_scraped_at)}
            </div>
          </div>
        ))}
      </div>

      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        <button
          className="btn btn-accent"
          onClick={() => discover.mutate()}
          disabled={discover.isPending}
        >
          <Icon name="refresh" />{" "}
          {discover.isPending ? "Scraping…" : "Scrape now"}
        </button>
        {discover.isSuccess && (
          <span className="text-sm">
            {totalDiscovered === 0 ? (
              <span className="muted">
                ✓ Scrape complete — no new meetings.
              </span>
            ) : (
              <>
                ✓ Discovered{" "}
                <strong>{totalDiscovered} new meeting{totalDiscovered === 1 ? "" : "s"}</strong>
                .{" "}
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => navigate("/overview")}
                  style={{ padding: "2px 6px" }}
                >
                  View in Overview <Icon name="arrow-r" size={11} />
                </button>
              </>
            )}
          </span>
        )}
        {discover.isError && (
          <span className="text-sm" style={{ color: "var(--accent)" }}>
            ✗ Scrape failed: {(discover.error as Error).message}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Manual ─────────────────────────────────────────────────────────────────

function ManualAdd() {
  const navigate = useNavigate();
  const { data: config } = useQuery({
    queryKey: ["app-config"],
    queryFn: () => api.getConfig(),
  });

  const [url, setUrl] = useState("");
  const [committee, setCommittee] = useState("");
  const [result, setResult] = useState<{ ok: true; meetingId: number; reused: boolean } | null>(null);

  const committees = (config?.committees ?? []).filter((c) => c.active);

  const submit = useMutation({
    mutationFn: () =>
      api.ingestByUrl({
        url: url.trim(),
        committee_short: committee || undefined,
      }),
    onSuccess: (res) => {
      setResult({ ok: true, meetingId: res.meeting_id, reused: res.already_existed });
    },
    onError: () => setResult(null),
  });

  return (
    <div className="card" style={{ padding: 20 }}>
      <h2 className="section-head" style={{ marginBottom: 6 }}>
        Add a single meeting by URL
      </h2>
      <p className="muted text-sm" style={{ marginBottom: 16 }}>
        Paste an ISO-NE event URL or a bare event ID. Documents and the
        agenda are ingested automatically; summarization stays manual.
      </p>

      <div className="manual-grid">
        <div>
          <label className="field-label">Meeting URL or event ID</label>
          <input
            className="input"
            placeholder="https://iso-ne.com/event-details?eventId=215042"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            autoFocus
          />
        </div>

        <div className="row" style={{ gap: 12 }}>
          <div style={{ flex: 1 }}>
            <label className="field-label">Venue</label>
            <select className="select" value="ISO-NE" disabled>
              <option>ISO-NE</option>
            </select>
          </div>
          <div style={{ flex: 2 }}>
            <label className="field-label">
              Committee (override — usually auto-detected)
            </label>
            <select
              className="select"
              value={committee}
              onChange={(e) => setCommittee(e.target.value)}
            >
              <option value="">Auto-detect from page</option>
              {committees.map((c) => (
                <option key={c.short} value={c.short}>
                  {c.name} ({c.short})
                </option>
              ))}
            </select>
          </div>
        </div>

        {submit.error && (
          <div
            style={{
              background: "rgba(196,99,58,0.08)",
              border: "1px solid var(--accent-soft)",
              color: "var(--accent)",
              padding: "8px 10px",
              borderRadius: "var(--radius)",
              fontSize: 12.5,
            }}
          >
            {(submit.error as Error).message}
          </div>
        )}

        {result && (
          <div
            style={{
              background: "var(--accent-tint)",
              border: "1px solid var(--accent-soft)",
              padding: "10px 12px",
              borderRadius: "var(--radius)",
            }}
          >
            <div className="text-sm">
              {result.reused
                ? "Re-ingested existing meeting."
                : "Meeting ingested."}
            </div>
            <div style={{ marginTop: 8 }}>
              <button
                className="btn btn-sm btn-accent"
                onClick={() => navigate(`/meeting/${result.meetingId}`)}
              >
                Open meeting <Icon name="arrow-r" size={12} />
              </button>
            </div>
          </div>
        )}

        <div className="row" style={{ gap: 8 }}>
          <span style={{ flex: 1 }} />
          <button
            className="btn btn-accent"
            disabled={!url.trim() || submit.isPending}
            onClick={() => {
              setResult(null);
              submit.mutate();
            }}
          >
            <Icon name="play" />{" "}
            {submit.isPending ? "Ingesting…" : "Ingest"}
          </button>
        </div>
      </div>
    </div>
  );
}
