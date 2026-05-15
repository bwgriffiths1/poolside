import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { Pill } from "../components/Pill";
import { VenueTag } from "../components/Tag";
import { api } from "../lib/api";
import { SCRAPED_MEETINGS, type ScrapedMeeting } from "../lib/fixtures";

type Mode = "auto" | "manual";

const INGEST_LOG_LINES = [
  "→ Scraping ISO-NE Markets Committee calendar…",
  "  found 1 new meeting (2026-06-10)",
  "→ Fetching event documents from event_id 215042…",
  "  17 documents enumerated",
  "→ Downloading documents (parallel × 6)…",
  "  ✓ 17/17 downloaded · 142.4 MB",
  "→ Parsing agenda from MC_2026_06_Agenda.pdf (LLM)…",
  "  detected 13 items, 4 votes scheduled",
  "→ Scraping NYISO Management Committee…",
  "  found 1 new meeting (2026-05-28)",
  "→ Ingesting ISO-NE Transmission Committee (2026-05-27)…",
  "  9 documents enumerated · 8 downloaded · 1 CEII-restricted (skipped)",
  "→ Building manifests…",
  "✓ Done — 3 meetings ingested, 39 documents, 30 agenda items.",
];

function StepHead({
  n,
  label,
  sub,
  active,
  done,
}: {
  n: number;
  label: string;
  sub: string;
  active?: boolean;
  done?: boolean;
}) {
  return (
    <div className={`step-head ${active ? "active" : ""} ${done ? "done" : ""}`}>
      <div className="step-bubble">{done ? "✓" : String(n).padStart(2, "0")}</div>
      <div>
        <div className="step-label">{label}</div>
        <div className="step-sub">{sub}</div>
      </div>
    </div>
  );
}

const keyOf = (m: ScrapedMeeting) => `${m.venue}|${m.committee}|${m.date}`;

export function Add() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("auto");
  const [selected, setSelected] = useState<Set<string>>(
    () =>
      new Set([
        "ISO-NE|Markets Committee|2026-06-10",
        "NYISO|Management Committee|2026-05-28",
        "ISO-NE|Transmission Committee|2026-05-27",
      ])
  );
  const [autoIngest, setAutoIngest] = useState<"on" | "off">("on");
  const [autoParse, setAutoParse] = useState<"on" | "off">("on");
  const [running, setRunning] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);

  const toggle = (m: ScrapedMeeting) => {
    if (m.status === "exists") return;
    setSelected((prev) => {
      const n = new Set(prev);
      const k = keyOf(m);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  };

  const selectedCount = selected.size;

  const { data: recent = [] } = useQuery({
    queryKey: ["ingestJobs"],
    queryFn: () => api.ingestJobs(),
  });

  const runIngest = () => {
    setRunning(true);
    setCompleted(false);
    setLogLines([]);
    const lines = INGEST_LOG_LINES;
    const id = window.setInterval(() => {
      setLogLines((prev) => {
        // Idempotent updater (React 19 StrictMode invokes twice in dev).
        if (prev.length >= lines.length) return prev;
        return [...prev, lines[prev.length]];
      });
    }, 280);
    window.setTimeout(() => {
      window.clearInterval(id);
      setRunning(false);
      setCompleted(true);
    }, lines.length * 280 + 300);
  };

  const newCount = SCRAPED_MEETINGS.filter((m) => m.status === "new").length;
  const existsCount = SCRAPED_MEETINGS.filter((m) => m.status === "exists").length;

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
            Either scrape upcoming meetings from ISO-NE and NYISO calendars, or
            add a single meeting by URL. Documents and agendas are ingested
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

        {mode === "auto" ? (
          <>
            <div className="ingest-step">
              <StepHead
                n={1}
                label="Sources"
                sub="Which committee calendars to scrape"
                done={completed}
              />
              <div className="step-body">
                <VenueGrid />
                <div className="row" style={{ marginTop: 12, gap: 16, flexWrap: "wrap" }}>
                  <div>
                    <label className="field-label">Lookahead</label>
                    <select className="select" defaultValue="90" style={{ width: 140 }}>
                      <option value="30">30 days</option>
                      <option value="60">60 days</option>
                      <option value="90">90 days</option>
                      <option value="180">180 days</option>
                    </select>
                  </div>
                  <div>
                    <label className="field-label">Auto-ingest documents</label>
                    <Segmented
                      value={autoIngest}
                      onChange={setAutoIngest}
                      options={[
                        { value: "on", label: "On" },
                        { value: "off", label: "Off" },
                      ]}
                    />
                  </div>
                  <div>
                    <label className="field-label">Auto-parse agenda</label>
                    <Segmented
                      value={autoParse}
                      onChange={setAutoParse}
                      options={[
                        { value: "on", label: "On" },
                        { value: "off", label: "Off" },
                      ]}
                    />
                  </div>
                </div>
              </div>
            </div>

            <div className="ingest-step">
              <StepHead
                n={2}
                label="Preview"
                sub={`${newCount} new meetings found · ${existsCount} already in database`}
                active={!running && !completed}
                done={completed}
              />
              <div className="step-body">
                <div className="scrape-table">
                  <div className="scrape-row scrape-row-head">
                    <div></div>
                    <div>Venue</div>
                    <div>Committee</div>
                    <div>Date</div>
                    <div>Source</div>
                    <div style={{ textAlign: "right" }}>Status</div>
                  </div>
                  {SCRAPED_MEETINGS.map((m) => {
                    const k = keyOf(m);
                    const exists = m.status === "exists";
                    const checked = selected.has(k);
                    return (
                      <label
                        key={k}
                        className={`scrape-row ${checked ? "checked" : ""} ${exists ? "exists" : ""}`}
                      >
                        <div>
                          <input
                            type="checkbox"
                            checked={checked && !exists}
                            disabled={exists}
                            onChange={() => toggle(m)}
                          />
                        </div>
                        <div>
                          <VenueTag>{m.venue}</VenueTag>
                        </div>
                        <div className="ink-soft">{m.committee}</div>
                        <div className="mono text-sm">
                          {m.date}
                          {m.end ? ` – ${m.end.slice(8)}` : ""}
                        </div>
                        <div className="muted text-xs">{m.source}</div>
                        <div style={{ textAlign: "right" }}>
                          {exists ? (
                            <span className="muted text-xs">In database</span>
                          ) : (
                            <span className="badge-new">NEW</span>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="ingest-step">
              <StepHead
                n={3}
                label="Ingest"
                sub={`${selectedCount} meeting${selectedCount === 1 ? "" : "s"} selected`}
                active={running}
                done={completed}
              />
              <div className="step-body">
                {!running && !completed && (
                  <div className="ingest-cta">
                    <div>
                      <div className="ingest-cta-h">
                        Ready to ingest {selectedCount} meetings.
                      </div>
                      <div className="muted text-sm">
                        Estimated time: ~{selectedCount * 90}s. Cost: free
                        (scraping + parse).
                      </div>
                    </div>
                    <button
                      className="btn btn-accent btn-lg"
                      onClick={runIngest}
                      disabled={selectedCount === 0}
                    >
                      <Icon name="play" /> Run ingest
                    </button>
                  </div>
                )}

                {(running || completed) && (
                  <div className="log-pane">
                    <div className="log-head">
                      <span className="mono text-xs">
                        ingest_job_id: ing-205
                      </span>
                      <span style={{ flex: 1 }} />
                      {running ? (
                        <span
                          className="mono text-xs"
                          style={{ color: "var(--accent)" }}
                        >
                          <span className="dot-spin" /> running
                        </span>
                      ) : (
                        <span
                          className="mono text-xs"
                          style={{ color: "var(--success)" }}
                        >
                          ✓ complete
                        </span>
                      )}
                    </div>
                    <pre className="log-body">
                      {logLines.map((l, i) => {
                        const text = l ?? "";
                        const cls = text.startsWith("✓")
                          ? "log-success"
                          : text.startsWith("  ")
                          ? "log-detail"
                          : "log-step";
                        return (
                          <div key={i} className={cls}>
                            {text}
                          </div>
                        );
                      })}
                      {running && <div className="log-cursor">█</div>}
                    </pre>
                    {completed && (
                      <div className="log-foot">
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            setCompleted(false);
                            setLogLines([]);
                          }}
                        >
                          Run another
                        </button>
                        <span style={{ flex: 1 }} />
                        <button
                          className="btn btn-sm btn-accent"
                          onClick={() => navigate("/overview")}
                        >
                          View in Overview <Icon name="arrow-r" size={12} />
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="ingest-step">
            <StepHead n={1} label="Manual add" sub="Paste a meeting URL or upload a packet" active />
            <div className="step-body">
              <div className="manual-grid">
                <div>
                  <label className="field-label">Meeting URL</label>
                  <input
                    className="input"
                    placeholder="https://iso-ne.com/event/markets-committee-june-2026"
                  />
                  <div className="muted text-xs" style={{ marginTop: 4 }}>
                    Paste an ISO-NE event page, NYISO meeting URL, or a public
                    agenda PDF link.
                  </div>
                </div>
                <div className="row" style={{ gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <label className="field-label">Venue</label>
                    <select className="select">
                      <option>ISO-NE</option>
                      <option>NYISO</option>
                    </select>
                  </div>
                  <div style={{ flex: 1 }}>
                    <label className="field-label">Committee</label>
                    <select className="select">
                      <option>Markets Committee (MC)</option>
                      <option>NEPOOL Participants Committee (NPC)</option>
                      <option>Reliability Committee (RC)</option>
                    </select>
                  </div>
                  <div style={{ flex: 1 }}>
                    <label className="field-label">Date</label>
                    <input
                      className="input"
                      type="date"
                      defaultValue="2026-06-10"
                    />
                  </div>
                </div>
                <div>
                  <label className="field-label">
                    Or upload documents directly
                  </label>
                  <div className="dropzone">
                    <div className="mono text-xs muted">
                      drag PDFs / PPTX / DOCX here
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <button className="btn btn-sm">Browse files</button>
                    </div>
                  </div>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <button className="btn">Cancel</button>
                  <span style={{ flex: 1 }} />
                  <button className="btn btn-accent">
                    <Icon name="play" /> Ingest
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

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

function VenueGrid() {
  const qc = useQueryClient();
  const { data: venues = [] } = useQuery({
    queryKey: ["venues"],
    queryFn: () => api.venues(),
  });
  const discoverMut = useMutation({
    mutationFn: () => api.triggerDiscover(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["venues"] }),
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

  return (
    <>
      <div className="venue-grid">
        {venues.map((v) => (
          <label key={v.id} className="venue-card">
            <input type="checkbox" defaultChecked />
            <div>
              <div className="venue-card-title">{v.short_name}</div>
              <div className="muted text-xs">
                {v.name} · last scraped {rel(v.last_scraped_at)}
              </div>
            </div>
          </label>
        ))}
      </div>
      <div className="row" style={{ marginTop: 8, gap: 8 }}>
        <button
          className="btn btn-sm"
          onClick={() => discoverMut.mutate()}
          disabled={discoverMut.isPending}
        >
          <Icon name="refresh" size={12} />{" "}
          {discoverMut.isPending ? "Scraping…" : "Scrape now"}
        </button>
        {discoverMut.isSuccess && (
          <span className="text-xs muted">✓ scrape complete</span>
        )}
        {discoverMut.isError && (
          <span className="text-xs" style={{ color: "var(--danger)" }}>
            ✗ scrape failed
          </span>
        )}
      </div>
    </>
  );
}
