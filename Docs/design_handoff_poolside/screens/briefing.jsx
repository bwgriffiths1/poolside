// Briefing Reader — the hero. Magazine/editorial layout for the published briefing.
// Sticky TOC, in-line citations to source docs, inline tables, an "Edit" mode.

const SectionBlock = ({ block }) => {
  switch (block.kind) {
    case "p":
      return <p className="b-p">{block.text}</p>;
    case "h":
      return <h3 className="b-h3">{block.text}</h3>;
    case "callout":
      return (
        <div className="b-callout">
          <div className="b-callout-label">{block.label}</div>
          <div className="b-callout-body">{block.text}</div>
        </div>
      );
    case "data":
      return (
        <figure className="b-figure">
          <table className="b-table">
            <thead>
              <tr>{block.rows[0].map((c, i) => <th key={i}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {block.rows.slice(1).map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci} className={ci === 0 ? "" : "mono num"}>
                      {ci === r.length - 1 && /^[-+]/.test(c) ? (
                        <span className={c.startsWith("+") ? "delta-pos" : "delta-neg"}>{c}</span>
                      ) : c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <figcaption>{block.title}</figcaption>
        </figure>
      );
    default: return null;
  }
};

const TOC = ({ briefing, active, onJump }) => (
  <nav className="b-toc">
    <div className="b-toc-label">On this page</div>
    <ul>
      <li className={active === "top" ? "on" : ""}>
        <button onClick={() => onJump("top")}>Headline & TL;DR</button>
      </li>
      {briefing.sections.map((s) => (
        <li key={s.id} className={active === s.id ? "on" : ""}>
          <button onClick={() => onJump(s.id)}>
            <span className="toc-num mono">{s.item_id}</span>
            <span>{s.title}</span>
          </button>
        </li>
      ))}
      <li><button onClick={() => onJump("decisions")}>Decisions & next steps</button></li>
      <li><button onClick={() => onJump("sources")}>Source documents</button></li>
    </ul>
    <div className="b-toc-meta">
      <div className="row" style={{ gap: 6 }}><Icon name="dot" size={11}/> <span className="text-xs">{briefing.reading_time} min read</span></div>
      <div className="row" style={{ gap: 6 }}><Icon name="dot" size={11}/> <span className="text-xs mono">{briefing.model}</span></div>
    </div>
  </nav>
);

const BriefingScreen = ({ id, onNav }) => {
  const m = window.MOCK_DATA.meetings.find((x) => x.id === id) || window.MOCK_DATA.meetings[0];
  const briefing = window.MOCK_DATA.briefing101;
  const [active, setActive] = React.useState("top");
  const [editMode, setEditMode] = React.useState(false);
  const refs = React.useRef({});

  // Scroll-spy
  React.useEffect(() => {
    const main = document.querySelector(".main");
    if (!main) return;
    const onScroll = () => {
      const ids = ["top", ...briefing.sections.map((s) => s.id), "decisions", "sources"];
      let cur = "top";
      for (const id of ids) {
        const el = refs.current[id];
        if (!el) continue;
        const rect = el.getBoundingClientRect();
        if (rect.top - 140 <= 0) cur = id;
      }
      setActive(cur);
    };
    main.addEventListener("scroll", onScroll);
    return () => main.removeEventListener("scroll", onScroll);
  }, []);

  const jump = (id) => {
    const el = refs.current[id];
    if (!el) return;
    const main = document.querySelector(".main");
    if (!main) return;
    main.scrollTo({ top: el.offsetTop - 80, behavior: "smooth" });
  };

  const allDocs = window.MOCK_DATA.meeting101.agenda.flatMap((i) => i.docs.map((d) => ({ ...d, item: i.title, item_id: i.item_id })));

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Overview", onClick: () => onNav({ name: "overview" }) },
          { label: m.venue + " · " + m.type_short, onClick: () => onNav({ name: "meeting", id: m.id }) },
          { label: "Briefing" },
        ]}
        actions={
          <>
            <button className="btn btn-sm btn-ghost" onClick={() => setEditMode(!editMode)}>
              <Icon name="edit" /> {editMode ? "Read" : "Edit"}
            </button>
            <button className="btn btn-sm">
              <Icon name="download" /> Download .docx
            </button>
            <button className="btn btn-sm btn-primary">
              <Icon name="check" /> Approve & publish
            </button>
          </>
        }
      />

      <div className="briefing-page">
        {/* Sticky TOC rail */}
        <aside className="briefing-side">
          <TOC briefing={briefing} active={active} onJump={jump} />
        </aside>

        {/* Body */}
        <article className="briefing-article">
          <header ref={(el) => (refs.current.top = el)} className="briefing-header">
            <div className="page-eyebrow">
              <span className="venue-tag" style={{ marginRight: 6 }}>{m.venue}</span>
              <span className="type-tag" style={{ marginRight: 6 }}>{m.type_short}</span>
              <span>{briefing.subtitle}</span>
            </div>
            <h1 className="briefing-title">{briefing.title}</h1>
            <p className="briefing-headline">{briefing.headline}</p>

            <div className="briefing-meta-row">
              <span><Icon name="dot" size={11}/> Generated {briefing.generated_at}</span>
              <span><Icon name="dot" size={11}/> {briefing.word_count.toLocaleString()} words · {briefing.reading_time} min read</span>
              <span><Icon name="dot" size={11}/> {briefing.model}</span>
              <span><Icon name="dot" size={11}/> v2 · awaiting review</span>
            </div>
          </header>

          {/* TL;DR */}
          <section className="briefing-tldr">
            <div className="b-eyebrow">Key takeaways</div>
            <ol>
              {briefing.tldr.map((t, i) => (
                <li key={i}>
                  <span className="tldr-num mono">{String(i + 1).padStart(2, "0")}</span>
                  <span>{t}</span>
                </li>
              ))}
            </ol>
          </section>

          {/* Drop cap intro */}
          <section className="briefing-intro">
            <p className="b-p has-dropcap">
              The Markets Committee convened May 12–13 in Holyoke for what proved to be the most consequential meeting of the spring stakeholder cycle. Three voting items reached resolution — Capacity Accreditation Phase 2 advanced to NPC, ESI Phase 2 design was approved, and FCA 19 parameters were finalized — while two discussion items (DASI status, IBR performance) set up Q3 deliverables. Below: section-by-section analysis with positions and downstream implications.
            </p>
          </section>

          {/* Agenda-anchored sections */}
          {briefing.sections.map((s) => (
            <section
              key={s.id}
              ref={(el) => (refs.current[s.id] = el)}
              className="briefing-section"
            >
              <div className="b-section-head">
                <div className="b-section-num mono">{s.item_id}</div>
                <div>
                  <h2 className="b-h2">{s.title}</h2>
                  {s.vote && (
                    <div className={`b-section-vote ${s.vote.toLowerCase().includes("approved") ? "ok" : ""}`}>
                      {s.vote}
                    </div>
                  )}
                </div>
                <button className="btn btn-sm btn-ghost" onClick={() => onNav({ name: "meeting", id: m.id })} title="Open in Meeting">
                  <Icon name="external" size={12}/>
                </button>
              </div>

              <div className="b-section-body">
                {s.body.map((b, i) => <SectionBlock key={i} block={b} />)}
              </div>

              {s.next_steps && s.next_steps.length > 0 && (
                <div className="b-next">
                  <div className="b-next-label">Next steps</div>
                  <ul>
                    {s.next_steps.map((n, i) => <li key={i}>{n}</li>)}
                  </ul>
                </div>
              )}
            </section>
          ))}

          {/* Decisions roll-up */}
          <section ref={(el) => (refs.current.decisions = el)} className="briefing-section">
            <div className="b-section-head">
              <div className="b-section-num mono">∗</div>
              <div>
                <h2 className="b-h2">Decisions & next steps</h2>
              </div>
            </div>
            <table className="b-decisions">
              <thead>
                <tr><th>Item</th><th>Decision</th><th>Outcome</th><th>Next</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td className="mono">3</td>
                  <td>Capacity Accreditation Phase 2</td>
                  <td><span className="delta-pos">Approved 78%</span></td>
                  <td>NPC vote · Jun 5</td>
                </tr>
                <tr>
                  <td className="mono">4</td>
                  <td>ESI Phase 2 design framework</td>
                  <td><span className="delta-pos">Approved 91%</span></td>
                  <td>Tariff WG kickoff · Jun 11</td>
                </tr>
                <tr>
                  <td className="mono">5</td>
                  <td>FCA 19 parameters</td>
                  <td><span className="delta-pos">Approved 96%</span></td>
                  <td>Qualification opens · Jul 1</td>
                </tr>
                <tr>
                  <td className="mono">6</td>
                  <td>DASI six-month review</td>
                  <td>Discussion</td>
                  <td>Curve calibration filing · Aug</td>
                </tr>
              </tbody>
            </table>
          </section>

          {/* Sources */}
          <section ref={(el) => (refs.current.sources = el)} className="briefing-section">
            <div className="b-section-head">
              <div className="b-section-num mono">§</div>
              <div>
                <h2 className="b-h2">Source documents</h2>
                <div className="muted text-sm">{allDocs.length} files ingested · all available on the Meeting page</div>
              </div>
            </div>
            <div className="b-sources">
              {allDocs.slice(0, 12).map((d) => (
                <a key={d.id} className="b-source" href="#">
                  <div className="b-source-ext mono">{(d.filename.split(".").pop() || "").toUpperCase()}</div>
                  <div>
                    <div className="b-source-name">{d.filename}</div>
                    <div className="b-source-item">Item {d.item_id} · {d.item}</div>
                  </div>
                  <Icon name="external" size={12}/>
                </a>
              ))}
            </div>
          </section>

          <footer className="briefing-footer">
            <div className="muted text-sm">
              Generated by Poolside · claude-sonnet-4.5 · May 13 18:42
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn btn-sm"><Icon name="refresh" /> Regenerate</button>
              <button className="btn btn-sm"><Icon name="edit" /> Edit markdown</button>
              <button className="btn btn-sm btn-primary"><Icon name="check"/> Approve & publish</button>
            </div>
          </footer>
        </article>
      </div>
    </>
  );
};

window.BriefingScreen = BriefingScreen;
