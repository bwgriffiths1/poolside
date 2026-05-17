// Overview — calendar/list of meetings with status pills + filters
// Exports: OverviewScreen

const fmtDate = (iso, end) => {
  const opts = { month: "short", day: "numeric" };
  const d = new Date(iso + "T12:00:00");
  const y = d.getFullYear();
  if (!end || end === iso) return d.toLocaleDateString("en-US", { ...opts, year: "numeric" });
  const e = new Date(end + "T12:00:00");
  if (d.getMonth() === e.getMonth()) {
    return `${d.toLocaleDateString("en-US", opts)}–${e.getDate()}, ${y}`;
  }
  return `${d.toLocaleDateString("en-US", opts)} – ${e.toLocaleDateString("en-US", opts)}, ${y}`;
};

const dayBucket = (iso) => {
  const today = new Date("2026-05-14T00:00:00").getTime();
  const d = new Date(iso + "T00:00:00").getTime();
  const diff = Math.round((d - today) / (1000 * 60 * 60 * 24));
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff > 1 && diff <= 7) return "This week";
  if (diff > 7 && diff <= 30) return "Later this month";
  if (diff > 30) return "Future";
  if (diff < 0 && diff >= -7) return "Last week";
  if (diff < -7 && diff >= -30) return "Earlier this month";
  return "Earlier";
};

const StatusLegend = () => (
  <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
    {["scheduled", "materials", "summarized", "updated"].map((s) => (
      <Pill key={s} status={s} />
    ))}
  </div>
);

const MeetingRow = ({ m, onOpen, view }) => {
  if (view === "list") {
    return (
      <button className="mtg-row" onClick={() => onOpen(m)}>
        <div className="mtg-row-date">
          <div className="mtg-row-month">{new Date(m.meeting_date + "T12:00:00").toLocaleDateString("en-US", { month: "short" }).toUpperCase()}</div>
          <div className="mtg-row-day">{new Date(m.meeting_date + "T12:00:00").getDate()}</div>
        </div>
        <div className="mtg-row-venue">
          <span className="venue-tag">{m.venue}</span>
          <span className="type-tag">{m.type_short}</span>
        </div>
        <div className="mtg-row-title">
          <div className="title-line">{m.type_name}</div>
          <div className="meta-line">{m.location} · {fmtDate(m.meeting_date, m.end_date)}</div>
        </div>
        <div className="mtg-row-stats">
          {m.doc_count > 0 && <span className="stat"><span className="mono">{m.doc_count}</span> docs</span>}
          {m.item_count > 0 && <span className="stat"><span className="mono">{m.item_count}</span> items</span>}
        </div>
        <div className="mtg-row-tags">
          {m.tags.slice(0, 2).map((t) => <Tag key={t}>{t}</Tag>)}
          {m.tags.length > 2 && <span className="muted text-xs">+{m.tags.length - 2}</span>}
        </div>
        <div className="mtg-row-status">
          <Pill status={m.status} />
        </div>
        <div className="mtg-row-chev"><Icon name="chev-r" size={14}/></div>
      </button>
    );
  }
  // card view
  return (
    <button className="mtg-card" onClick={() => onOpen(m)}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <div className="row" style={{ gap: 6 }}>
          <span className="venue-tag">{m.venue}</span>
          <span className="type-tag">{m.type_short}</span>
        </div>
        <Pill status={m.status} />
      </div>
      <div className="mtg-card-title">{m.type_name}</div>
      <div className="mtg-card-date">{fmtDate(m.meeting_date, m.end_date)}</div>
      <div className="mtg-card-loc text-xs muted">{m.location}</div>
      <div className="mtg-card-meta">
        {m.doc_count > 0 && <span><span className="mono">{m.doc_count}</span> docs</span>}
        {m.item_count > 0 && <span><span className="mono">{m.item_count}</span> items</span>}
      </div>
      {m.tags.length > 0 && (
        <div className="row" style={{ gap: 4, marginTop: 10, flexWrap: "wrap" }}>
          {m.tags.slice(0, 3).map((t) => <Tag key={t}>{t}</Tag>)}
        </div>
      )}
    </button>
  );
};

const OverviewScreen = ({ onNav }) => {
  const [view, setView] = React.useState("list");
  const [venueFilter, setVenueFilter] = React.useState("All");
  const [statusFilter, setStatusFilter] = React.useState("All");

  const meetings = window.MOCK_DATA.meetings;
  const today = "2026-05-14";

  const filtered = meetings.filter((m) => {
    if (venueFilter !== "All" && m.venue !== venueFilter) return false;
    if (statusFilter !== "All" && m.status !== statusFilter) return false;
    return true;
  });

  const upcoming = filtered.filter((m) => m.meeting_date >= today)
                           .sort((a,b) => a.meeting_date.localeCompare(b.meeting_date));
  const past = filtered.filter((m) => m.meeting_date < today)
                       .sort((a,b) => b.meeting_date.localeCompare(a.meeting_date));

  // KPIs
  const summarizedThisMonth = meetings.filter((m) =>
    m.status === "summarized" && m.meeting_date.startsWith("2026-05")
  ).length;
  const awaitingMaterials = meetings.filter((m) => m.status === "scheduled").length;
  const pendingReview = meetings.filter((m) => m.status === "materials").length;

  const openMeeting = (m) => {
    if (m.status === "summarized" || m.status === "updated") {
      onNav({ name: "meeting", id: m.id });
    } else {
      onNav({ name: "meeting", id: m.id });
    }
  };

  return (
    <>
      <Topbar
        crumbs={[{ label: "Overview" }]}
        actions={
          <>
            <button className="btn btn-sm" onClick={() => onNav({ name: "add" })}>
              <Icon name="refresh" /> Refresh calendars
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => onNav({ name: "add" })}>
              <Icon name="plus" /> Add meeting
            </button>
          </>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Meeting calendar · ISO-NE, NYISO</div>
          <h1 className="page-title">Good morning, Ben.</h1>
          <p className="page-subtitle">
            {pendingReview > 0 ? <><span style={{color: "var(--ink)"}}>{pendingReview} meetings</span> with materials ready to summarize. </> : null}
            {summarizedThisMonth} briefings published this month.
          </p>
        </div>

        {/* KPI strip */}
        <div className="kpi-grid">
          <div className="kpi">
            <div className="kpi-label">Upcoming</div>
            <div className="kpi-num">{upcoming.length}</div>
            <div className="kpi-sub">next 30 days</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">Materials ready</div>
            <div className="kpi-num">{pendingReview}</div>
            <div className="kpi-sub">awaiting briefing</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">Summarized</div>
            <div className="kpi-num">{summarizedThisMonth}</div>
            <div className="kpi-sub">this month</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">Hours saved</div>
            <div className="kpi-num">~64</div>
            <div className="kpi-sub">vs. manual briefing time</div>
          </div>
        </div>

        {/* Filter bar */}
        <div className="filter-bar">
          <div className="row" style={{ gap: 6 }}>
            <div className="seg">
              {["All", "ISO-NE", "NYISO"].map((v) => (
                <button key={v} onClick={() => setVenueFilter(v)} className={venueFilter === v ? "on" : ""}>{v}</button>
              ))}
            </div>
            <div className="seg">
              {[["All","All"],["scheduled","Scheduled"],["materials","Materials"],["summarized","Summarized"],["updated","Updated"]].map(([k,l]) => (
                <button key={k} onClick={() => setStatusFilter(k)} className={statusFilter === k ? "on" : ""}>{l}</button>
              ))}
            </div>
          </div>
          <div className="spacer" />
          <div className="seg">
            <button onClick={() => setView("list")} className={view === "list" ? "on" : ""}><Icon name="list" /> List</button>
            <button onClick={() => setView("card")} className={view === "card" ? "on" : ""}><Icon name="dot" /> Cards</button>
          </div>
        </div>

        {/* Upcoming */}
        <div className="section-h">
          <h2>Upcoming</h2>
          <span className="meta">{upcoming.length} meetings</span>
        </div>
        {view === "list" ? (
          <div className="mtg-list">
            {upcoming.length === 0 ? <div className="empty">Nothing on the calendar.</div> :
              upcoming.map((m) => <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="list" />)}
          </div>
        ) : (
          <div className="mtg-cards">
            {upcoming.map((m) => <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="card" />)}
          </div>
        )}

        {/* Past */}
        <div className="section-h">
          <h2>Recent</h2>
          <span className="meta">{past.length} meetings</span>
        </div>
        {view === "list" ? (
          <div className="mtg-list">
            {past.map((m) => <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="list" />)}
          </div>
        ) : (
          <div className="mtg-cards">
            {past.map((m) => <MeetingRow key={m.id} m={m} onOpen={openMeeting} view="card" />)}
          </div>
        )}

        <div style={{ height: 48 }} />
      </div>
    </>
  );
};

window.OverviewScreen = OverviewScreen;
