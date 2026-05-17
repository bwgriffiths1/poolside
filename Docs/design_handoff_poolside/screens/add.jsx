// Add Meeting — ingest flow.
// Two paths: 1) Pick from auto-scraped calendar, or 2) Manually paste URL/upload docs.
// Below: recent ingest jobs.

const StepHead = ({ n, label, sub, active, done }) => (
  <div className={`step-head ${active ? "active" : ""} ${done ? "done" : ""}`}>
    <div className="step-bubble mono">{done ? "✓" : String(n).padStart(2, "0")}</div>
    <div>
      <div className="step-label">{label}</div>
      <div className="step-sub">{sub}</div>
    </div>
  </div>
);

const SCRAPED_MEETINGS = [
  { venue: "ISO-NE", committee: "Markets Committee", date: "2026-06-10", end: "2026-06-11", source: "iso-ne.com",  status: "new", docs: 0 },
  { venue: "ISO-NE", committee: "Transmission Committee", date: "2026-05-27",                   source: "iso-ne.com",  status: "new", docs: 0 },
  { venue: "ISO-NE", committee: "Reliability Committee", date: "2026-05-20",                    source: "iso-ne.com",  status: "exists", docs: 18 },
  { venue: "NYISO",  committee: "Management Committee",   date: "2026-05-28",                   source: "nyiso.com",   status: "new", docs: 0 },
  { venue: "NYISO",  committee: "Operating Committee",    date: "2026-05-22",                   source: "nyiso.com",   status: "exists", docs: 0 },
  { venue: "NYISO",  committee: "Business Issues Comm.",  date: "2026-06-11",                   source: "nyiso.com",   status: "new", docs: 0 },
];

const AddScreen = ({ onNav }) => {
  const [mode, setMode] = React.useState("auto"); // auto | manual
  const [selected, setSelected] = React.useState(new Set([
    "ISO-NE|Markets Committee|2026-06-10",
    "NYISO|Management Committee|2026-05-28",
    "ISO-NE|Transmission Committee|2026-05-27",
  ]));
  const [running, setRunning] = React.useState(false);
  const [completed, setCompleted] = React.useState(false);
  const [logLines, setLogLines] = React.useState([]);

  const keyOf = (m) => `${m.venue}|${m.committee}|${m.date}`;
  const toggle = (m) => setSelected((prev) => {
    const n = new Set(prev);
    const k = keyOf(m);
    if (n.has(k)) n.delete(k); else n.add(k);
    return n;
  });

  const selectedCount = selected.size;

  const runIngest = () => {
    setRunning(true);
    setLogLines([]);
    setCompleted(false);
    const lines = [
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
    let i = 0;
    const id = setInterval(() => {
      setLogLines((prev) => [...prev, lines[i]]);
      i++;
      if (i >= lines.length) {
        clearInterval(id);
        setTimeout(() => { setRunning(false); setCompleted(true); }, 300);
      }
    }, 280);
  };

  const recent = window.MOCK_DATA.recentIngests;

  return (
    <>
      <Topbar
        crumbs={[{ label: "Add Meeting" }]}
        actions={
          <button className="btn btn-sm" onClick={() => onNav({ name: "overview" })}>
            <Icon name="x" size={12}/> Cancel
          </button>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Pipeline · Ingest</div>
          <h1 className="page-title">Add meetings to Poolside</h1>
          <p className="page-subtitle">
            Either scrape upcoming meetings from ISO-NE and NYISO calendars, or add a single
            meeting by URL. Documents and agendas are ingested automatically; summarization
            is triggered separately from each meeting page.
          </p>
        </div>

        {/* Mode tabs */}
        <div className="seg" style={{ marginBottom: 24 }}>
          <button className={mode === "auto" ? "on" : ""} onClick={() => setMode("auto")}>
            <Icon name="refresh" size={12}/> Scrape calendars
          </button>
          <button className={mode === "manual" ? "on" : ""} onClick={() => setMode("manual")}>
            <Icon name="plus" size={12}/> Add manually
          </button>
        </div>

        {mode === "auto" ? (
          <>
            {/* Step 1 - Sources */}
            <div className="ingest-step">
              <StepHead n={1} label="Sources" sub="Which committee calendars to scrape" done={completed} />
              <div className="step-body">
                <div className="venue-grid">
                  {[
                    { venue: "ISO-NE", icon: "🏭", count: 7, last: "2 hours ago" },
                    { venue: "NYISO",  icon: "🗽", count: 4, last: "2 hours ago" },
                  ].map((v) => (
                    <label key={v.venue} className="venue-card">
                      <input type="checkbox" defaultChecked />
                      <div>
                        <div className="venue-card-title">{v.venue}</div>
                        <div className="muted text-xs">{v.count} active committees · last scraped {v.last}</div>
                      </div>
                    </label>
                  ))}
                </div>
                <div className="row" style={{ marginTop: 12, gap: 16 }}>
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
                    <div className="seg">
                      <button className="on">On</button>
                      <button>Off</button>
                    </div>
                  </div>
                  <div>
                    <label className="field-label">Auto-parse agenda</label>
                    <div className="seg">
                      <button className="on">On</button>
                      <button>Off</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Step 2 - Preview */}
            <div className="ingest-step">
              <StepHead n={2} label="Preview" sub={`${SCRAPED_MEETINGS.filter(m => m.status === "new").length} new meetings found · ${SCRAPED_MEETINGS.filter(m => m.status === "exists").length} already in database`} active={!running && !completed} done={completed}/>
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
                    const checked = selected.has(k);
                    const exists = m.status === "exists";
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
                        <div><span className="venue-tag">{m.venue}</span></div>
                        <div className="ink-soft">{m.committee}</div>
                        <div className="mono text-sm">{m.date}{m.end ? ` – ${m.end.slice(8)}` : ""}</div>
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

            {/* Step 3 - Run */}
            <div className="ingest-step">
              <StepHead n={3} label="Ingest" sub={`${selectedCount} meeting${selectedCount === 1 ? "" : "s"} selected`} active={running} done={completed}/>
              <div className="step-body">
                {!running && !completed && (
                  <div className="ingest-cta">
                    <div>
                      <div className="ingest-cta-h">Ready to ingest {selectedCount} meetings.</div>
                      <div className="muted text-sm">Estimated time: ~{selectedCount * 90}s. Cost: free (scraping + parse).</div>
                    </div>
                    <button className="btn btn-accent btn-lg" onClick={runIngest} disabled={selectedCount === 0}>
                      <Icon name="play" /> Run ingest
                    </button>
                  </div>
                )}

                {(running || completed) && (
                  <div className="log-pane">
                    <div className="log-head">
                      <span className="mono text-xs">ingest_job_id: ing-205</span>
                      <span style={{ flex: 1 }} />
                      {running ? (
                        <span className="mono text-xs" style={{ color: "var(--accent)" }}>
                          <span className="dot-spin" /> running
                        </span>
                      ) : (
                        <span className="mono text-xs" style={{ color: "var(--success)" }}>✓ complete</span>
                      )}
                    </div>
                    <pre className="log-body">
                      {logLines.map((l, i) => (
                        <div key={i} className={l.startsWith("✓") ? "log-success" : l.startsWith("  ") ? "log-detail" : "log-step"}>
                          {l}
                        </div>
                      ))}
                      {running && <div className="log-cursor">█</div>}
                    </pre>
                    {completed && (
                      <div className="log-foot">
                        <button className="btn btn-sm" onClick={() => { setCompleted(false); setLogLines([]); }}>
                          Run another
                        </button>
                        <span style={{ flex: 1 }} />
                        <button className="btn btn-sm btn-accent" onClick={() => onNav({ name: "overview" })}>
                          View in Overview <Icon name="arrow-r" size={12}/>
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
                  <input className="input" placeholder="https://iso-ne.com/event/markets-committee-june-2026" />
                  <div className="muted text-xs" style={{ marginTop: 4 }}>
                    Paste an ISO-NE event page, NYISO meeting URL, or a public agenda PDF link.
                  </div>
                </div>
                <div className="row" style={{ gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <label className="field-label">Venue</label>
                    <select className="select"><option>ISO-NE</option><option>NYISO</option></select>
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
                    <input className="input" type="date" defaultValue="2026-06-10" />
                  </div>
                </div>
                <div>
                  <label className="field-label">Or upload documents directly</label>
                  <div className="dropzone">
                    <div className="mono text-xs muted">drag PDFs / PPTX / DOCX here</div>
                    <div style={{ marginTop: 8 }}>
                      <button className="btn btn-sm">Browse files</button>
                    </div>
                  </div>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <button className="btn">Cancel</button>
                  <span style={{ flex: 1 }} />
                  <button className="btn btn-accent"><Icon name="play"/> Ingest</button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Recent jobs */}
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
                <div className="muted text-xs">{j.docs} docs · {j.agenda_items} agenda items</div>
              </div>
              <div className="mono text-xs muted">{j.started}</div>
              <div>
                <span className="pill summarized"><span className="dot"/> Complete</span>
              </div>
              <button className="btn btn-sm btn-ghost" onClick={() => onNav({ name: "meeting", id: j.meeting_id })}>
                Open <Icon name="arrow-r" size={11}/>
              </button>
            </div>
          ))}
        </div>

        <div style={{ height: 80 }} />
      </div>
    </>
  );
};

window.AddScreen = AddScreen;
