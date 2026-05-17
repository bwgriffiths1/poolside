// Meeting Detail — agenda items, documents, summaries
// The briefing-as-hero is the Briefing screen; here is the workflow side: edit, assign, summarize.

const docIcon = (type) => {
  const m = { pdf: "doc", pptx: "doc", xlsx: "doc", docx: "doc" };
  return m[type] || "doc";
};

const docExtTag = (filename) => {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  return ext.toUpperCase();
};

const AgendaItem = ({ item, idx, expanded, onToggle, isEditing, onEdit, onSaveEdit, onCancelEdit }) => {
  const [draft, setDraft] = React.useState({
    title: item.title,
    presenter: item.presenter || "",
    one_line: item.one_line || "",
    detailed: item.detailed || "",
  });
  React.useEffect(() => {
    setDraft({ title: item.title, presenter: item.presenter || "",
               one_line: item.one_line || "", detailed: item.detailed || "" });
  }, [isEditing, item.id]);

  const indent = item.depth * 24;

  return (
    <div className={`agenda-item depth-${item.depth} ${expanded ? "open" : ""}`} style={{ paddingLeft: indent }}>
      <button className="agenda-head" onClick={onToggle}>
        <div className="agenda-chev"><Icon name={expanded ? "chev-d" : "chev-r"} size={12}/></div>
        <div className="agenda-num">{item.item_id || "—"}</div>
        <div className="agenda-title-wrap">
          <div className="agenda-title">{item.title}</div>
          {item.one_line && (
            <div className="agenda-oneline serif">{item.one_line}</div>
          )}
        </div>
        <div className="agenda-meta">
          {item.presenter && <span className="text-xs muted">{item.presenter}{item.org ? ` · ${item.org}` : ""}</span>}
        </div>
        <div className="agenda-status">
          {item.vote_status && (
            <span className={`vote-pill ${item.vote_status.toLowerCase().includes("approved") ? "approved" : item.vote_status.toLowerCase().includes("discussion") ? "discussion" : "vote"}`}>
              {item.vote_status}
            </span>
          )}
        </div>
        <div className="agenda-summary-state">
          {item.has_summary ? (
            <span className="state-dot summarized" title="Summarized"><Icon name="check" size={11}/></span>
          ) : (
            <span className="state-dot pending" title="No summary">○</span>
          )}
        </div>
      </button>

      {expanded && (
        <div className="agenda-body">
          {item.docs && item.docs.length > 0 && (
            <div className="doc-table">
              {item.docs.map((d) => (
                <div className="doc-row" key={d.id}>
                  <div className="doc-icon">
                    {d.ceii ? <Icon name="lock" /> : <Icon name="doc" />}
                  </div>
                  <div className="doc-name truncate">{d.filename}</div>
                  <div className="doc-ext mono text-xs">{docExtTag(d.filename)}</div>
                  <div className="doc-actions">
                    <button className="btn btn-sm btn-ghost"><Icon name="external" size={12}/></button>
                    <button className="btn btn-sm btn-ghost"><Icon name="download" size={12}/></button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {!isEditing ? (
            <div className="agenda-summary">
              {item.has_summary ? (
                <>
                  <div className="row" style={{ alignItems: "baseline", marginBottom: 8 }}>
                    <span className="field-label" style={{ marginBottom: 0 }}>Summary</span>
                    <span style={{ flex: 1 }} />
                    <span className="text-xs muted">v2 · approved · May 12 18:42</span>
                  </div>
                  <p className="serif" style={{ fontSize: 15, lineHeight: 1.55, margin: 0, color: "var(--ink-soft)" }}>
                    {item.one_line || "—"}
                  </p>
                  <div className="row" style={{ marginTop: 12, gap: 8 }}>
                    <button className="btn btn-sm" onClick={() => onEdit(item.id)}><Icon name="edit" size={12}/> Edit</button>
                    <button className="btn btn-sm btn-ghost"><Icon name="refresh" size={12}/> Re-run</button>
                    <span style={{ flex: 1 }} />
                    <button className="btn btn-sm btn-ghost"><Icon name="external" size={12}/> Open in briefing</button>
                  </div>
                </>
              ) : (
                <div className="empty-summary">
                  <span className="muted text-sm">No summary yet.</span>
                  <button className="btn btn-sm btn-accent"><Icon name="spark" size={12}/> Summarize</button>
                </div>
              )}
            </div>
          ) : (
            <div className="agenda-edit">
              <div className="row" style={{ gap: 12, marginBottom: 12 }}>
                <div style={{ flex: 2 }}>
                  <label className="field-label">Title</label>
                  <input className="input" value={draft.title}
                         onChange={(e) => setDraft({ ...draft, title: e.target.value })}/>
                </div>
                <div style={{ flex: 1 }}>
                  <label className="field-label">Presenter</label>
                  <input className="input" value={draft.presenter}
                         onChange={(e) => setDraft({ ...draft, presenter: e.target.value })}/>
                </div>
              </div>
              <label className="field-label">One-line summary</label>
              <input className="input" value={draft.one_line}
                     onChange={(e) => setDraft({ ...draft, one_line: e.target.value })}/>
              <div style={{ height: 10 }} />
              <label className="field-label">Detailed summary</label>
              <textarea className="textarea" rows={5}
                        value={draft.detailed || "ISO-NE staff presented Phase 2 design for capacity accreditation, replacing average ELCC with marginal ELCC for storage and hybrid resources…"}
                        onChange={(e) => setDraft({ ...draft, detailed: e.target.value })}/>
              <div className="row" style={{ marginTop: 12, gap: 8 }}>
                <button className="btn btn-sm btn-accent" onClick={() => onSaveEdit(item.id, draft)}>
                  <Icon name="check" size={12}/> Save changes
                </button>
                <button className="btn btn-sm" onClick={onCancelEdit}>Cancel</button>
                <span style={{ flex: 1 }} />
                <span className="text-xs muted">Saving creates v3 (approved)</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const MeetingScreen = ({ id, onNav }) => {
  const m = window.MOCK_DATA.meetings.find((x) => x.id === id) || window.MOCK_DATA.meetings[0];
  const detail = id === 101 ? window.MOCK_DATA.meeting101 : window.MOCK_DATA.meeting101; // demo
  const [expandedIds, setExpandedIds] = React.useState(new Set([3])); // open the headline item by default
  const [editingId, setEditingId] = React.useState(null);
  const [showSummaryRunner, setShowSummaryRunner] = React.useState(false);
  const [briefingStyle, setBriefingStyle] = React.useState("standard");
  const [extractImages, setExtractImages] = React.useState(false);
  const [forceRerun, setForceRerun] = React.useState(false);

  const toggle = (itemId) =>
    setExpandedIds((prev) => {
      const n = new Set(prev);
      if (n.has(itemId)) n.delete(itemId); else n.add(itemId);
      return n;
    });

  const total = detail.agenda.length;
  const withSummary = detail.agenda.filter((i) => i.has_summary).length;
  const docs = detail.agenda.flatMap((i) => i.docs || []).length;

  return (
    <>
      <Topbar
        crumbs={[
          { label: "Overview", onClick: () => onNav({ name: "overview" }) },
          { label: m.venue + " · " + m.type_short },
          { label: m.title },
        ]}
        actions={
          <>
            <button className="btn btn-sm" onClick={() => onNav({ name: "briefing", id: m.id })}>
              <Icon name="book" /> Open briefing
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => setShowSummaryRunner(true)}>
              <Icon name="spark" /> Summarize
            </button>
          </>
        }
      />

      <div className="page-wide" style={{ paddingLeft: 48, paddingRight: 48 }}>
        {/* Meeting head */}
        <div className="meeting-head">
          <div className="meeting-head-left">
            <div className="page-eyebrow">
              <span className="venue-tag" style={{ marginRight: 6 }}>{m.venue}</span>
              <span className="type-tag" style={{ marginRight: 6 }}>{m.type_short}</span>
              {m.external_id}
            </div>
            <h1 className="page-title">{m.type_name}</h1>
            <div className="meeting-head-meta">
              <span><Icon name="calendar" size={13}/> {fmtDate(m.meeting_date, m.end_date)}</span>
              <span><Icon name="globe" size={13}/> {m.location}</span>
              <Pill status={m.status} />
            </div>
            {detail.one_line && (
              <p className="serif meeting-headline">
                {detail.one_line}
              </p>
            )}
          </div>
          <div className="meeting-head-right">
            <div className="stat-block">
              <div className="stat-block-num">{total}</div>
              <div className="stat-block-label">agenda items</div>
            </div>
            <div className="stat-block">
              <div className="stat-block-num">{docs}</div>
              <div className="stat-block-label">documents</div>
            </div>
            <div className="stat-block">
              <div className="stat-block-num">
                <span>{withSummary}</span><span className="muted">/{total}</span>
              </div>
              <div className="stat-block-label">summarized</div>
            </div>
          </div>
        </div>

        {/* Tag rail */}
        {m.tags && m.tags.length > 0 && (
          <div className="row" style={{ gap: 6, flexWrap: "wrap", marginBottom: 24 }}>
            <span className="field-label" style={{ marginBottom: 0, marginRight: 4 }}>Topics</span>
            {m.tags.map((t) => <Tag key={t}>{t}</Tag>)}
            <button className="btn btn-sm btn-ghost"><Icon name="plus" size={11}/></button>
          </div>
        )}

        {/* Briefing card preview */}
        <div className="briefing-card" onClick={() => onNav({ name: "briefing", id: m.id })}>
          <div className="briefing-card-left">
            <div className="page-eyebrow" style={{ marginBottom: 6 }}>Meeting briefing · v2</div>
            <h2 className="briefing-card-title serif">
              Capacity Accreditation Phase 2 advances to NPC; ESI design framework approved; FCA 19 parameters finalized
            </h2>
            <div className="row" style={{ marginTop: 12, gap: 14 }}>
              <span className="text-xs muted"><Icon name="dot" size={11}/> {window.MOCK_DATA.briefing101.word_count} words · {window.MOCK_DATA.briefing101.reading_time} min read</span>
              <span className="text-xs muted">claude-sonnet-4.5</span>
              <span className="text-xs muted">Generated May 13 18:42</span>
            </div>
          </div>
          <div className="briefing-card-right">
            <button className="btn btn-sm">
              <Icon name="download" /> Download .docx
            </button>
            <button className="btn btn-sm btn-accent">
              Read briefing <Icon name="arrow-r" size={12}/>
            </button>
          </div>
        </div>

        {/* Summary runner (collapsible) */}
        {showSummaryRunner && (
          <div className="summary-runner">
            <div className="row" style={{ marginBottom: 14 }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>Re-summarize this meeting</h3>
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm btn-ghost" onClick={() => setShowSummaryRunner(false)}>
                <Icon name="x" size={12}/>
              </button>
            </div>
            <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
              <div style={{ minWidth: 200 }}>
                <label className="field-label">Briefing style</label>
                <div className="seg">
                  <button className={briefingStyle === "standard" ? "on" : ""} onClick={() => setBriefingStyle("standard")}>Standard</button>
                  <button className={briefingStyle === "detailed" ? "on" : ""} onClick={() => setBriefingStyle("detailed")}>Detailed</button>
                </div>
              </div>
              <div className="row" style={{ gap: 6, alignItems: "center" }}>
                <input type="checkbox" id="ei" checked={extractImages} onChange={(e) => setExtractImages(e.target.checked)} />
                <label htmlFor="ei" className="text-sm">Extract images & charts</label>
              </div>
              <div className="row" style={{ gap: 6, alignItems: "center" }}>
                <input type="checkbox" id="fr" checked={forceRerun} onChange={(e) => setForceRerun(e.target.checked)} />
                <label htmlFor="fr" className="text-sm">Force re-run all levels</label>
              </div>
              <span style={{ flex: 1 }} />
              <span className="text-xs muted">Est. cost: ~$2.40 · ~3 min</span>
              <button className="btn btn-sm btn-accent">
                <Icon name="play" size={11}/> Run
              </button>
            </div>
          </div>
        )}

        {/* Agenda */}
        <div className="section-h" style={{ marginTop: 32 }}>
          <h2>Agenda</h2>
          <span className="meta">{total} items · {docs} documents</span>
        </div>
        <div className="agenda-list">
          {detail.agenda.map((item, i) => (
            <AgendaItem
              key={item.id}
              item={item}
              idx={i}
              expanded={expandedIds.has(item.id)}
              onToggle={() => toggle(item.id)}
              isEditing={editingId === item.id}
              onEdit={(id) => setEditingId(id)}
              onSaveEdit={() => setEditingId(null)}
              onCancelEdit={() => setEditingId(null)}
            />
          ))}
        </div>

        <div style={{ height: 64 }} />
      </div>
    </>
  );
};

window.MeetingScreen = MeetingScreen;
