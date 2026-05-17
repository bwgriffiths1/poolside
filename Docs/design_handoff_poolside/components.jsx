// Sidebar + topbar + small UI primitives shared across screens.
// Exports to window: Sidebar, Topbar, Pill, Tag, Icon, IconBtn

const Icon = ({ name, size = 14 }) => {
  // Hand-coded minimal line icons — all simple geometric, no complex svg.
  const s = size, sw = 1.4;
  const common = { width: s, height: s, viewBox: "0 0 16 16", fill: "none",
                   stroke: "currentColor", strokeWidth: sw, strokeLinecap: "round", strokeLinejoin: "round" };
  switch (name) {
    case "calendar":
      return <svg {...common}><rect x="2" y="3" width="12" height="11" rx="1"/><path d="M2 6h12"/><path d="M5 1.5v3"/><path d="M11 1.5v3"/></svg>;
    case "list":
      return <svg {...common}><path d="M5 4h9"/><path d="M5 8h9"/><path d="M5 12h9"/><circle cx="2.3" cy="4" r="0.6" fill="currentColor"/><circle cx="2.3" cy="8" r="0.6" fill="currentColor"/><circle cx="2.3" cy="12" r="0.6" fill="currentColor"/></svg>;
    case "doc":
      return <svg {...common}><path d="M4 1.5h5l3 3v10h-8z"/><path d="M9 1.5v3h3"/></svg>;
    case "book":
      return <svg {...common}><path d="M3 2.5h6c1 0 2 0.8 2 1.8v9.2c0-0.7-0.8-1.3-1.7-1.3H3z"/><path d="M13 2.5h-2c-1 0-2 0.8-2 1.8v9.2c0-0.7 0.8-1.3 1.7-1.3H13z"/></svg>;
    case "plus":
      return <svg {...common}><path d="M8 3v10"/><path d="M3 8h10"/></svg>;
    case "search":
      return <svg {...common}><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/></svg>;
    case "edit":
      return <svg {...common}><path d="M11 2.5l2.5 2.5L6 12.5 2.5 13.5l1-3.5z"/></svg>;
    case "download":
      return <svg {...common}><path d="M8 2v8"/><path d="M5 7l3 3 3-3"/><path d="M2.5 13h11"/></svg>;
    case "settings":
      return <svg {...common}><circle cx="8" cy="8" r="2.2"/><circle cx="8" cy="8" r="5.5"/></svg>;
    case "library":
      return <svg {...common}><rect x="2" y="2.5" width="3" height="11"/><rect x="6.5" y="2.5" width="3" height="11"/><path d="M11.5 3l2 0.5-2 10-2-0.5z"/></svg>;
    case "check":
      return <svg {...common}><path d="M3 8.5l3 3 7-7"/></svg>;
    case "x":
      return <svg {...common}><path d="M3.5 3.5l9 9"/><path d="M12.5 3.5l-9 9"/></svg>;
    case "chev-r":
      return <svg {...common}><path d="M6 3l5 5-5 5"/></svg>;
    case "chev-d":
      return <svg {...common}><path d="M3 6l5 5 5-5"/></svg>;
    case "arrow-r":
      return <svg {...common}><path d="M3 8h10"/><path d="M9 4l4 4-4 4"/></svg>;
    case "arrow-l":
      return <svg {...common}><path d="M13 8H3"/><path d="M7 4l-4 4 4 4"/></svg>;
    case "filter":
      return <svg {...common}><path d="M2 3h12l-4.5 5.5v4.5l-3 1.5v-6z"/></svg>;
    case "lock":
      return <svg {...common}><rect x="3" y="7" width="10" height="7" rx="1"/><path d="M5.5 7V5a2.5 2.5 0 015 0v2"/></svg>;
    case "external":
      return <svg {...common}><path d="M9 2h5v5"/><path d="M14 2L7 9"/><path d="M12 9v4H3V4h4"/></svg>;
    case "play":
      return <svg {...common}><path d="M4 3l9 5-9 5z"/></svg>;
    case "spark":
      return <svg {...common}><path d="M8 1.5l1.6 4.3 4.4 1.7-4.4 1.7L8 13.5l-1.6-4.3L2 7.5l4.4-1.7z"/></svg>;
    case "refresh":
      return <svg {...common}><path d="M13 8a5 5 0 11-1.5-3.5"/><path d="M13 2.5v3h-3"/></svg>;
    case "tag":
      return <svg {...common}><path d="M2 8.5V2.5h6L14 8l-5.5 5.5z"/><circle cx="5" cy="5.5" r="0.8" fill="currentColor"/></svg>;
    case "users":
      return <svg {...common}><circle cx="6" cy="6" r="2.5"/><path d="M2 13c0-2 2-3.5 4-3.5s4 1.5 4 3.5"/><path d="M10 4a2 2 0 110 4"/><path d="M11.5 12c0-1.4 1-2.5 2.5-2.5"/></svg>;
    case "dot":
      return <svg {...common}><circle cx="8" cy="8" r="1.5" fill="currentColor"/></svg>;
    case "globe":
      return <svg {...common}><circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11"/><path d="M8 2.5c2 2 2 9 0 11"/><path d="M8 2.5c-2 2-2 9 0 11"/></svg>;
    default: return null;
  }
};

const Pill = ({ status, label }) => {
  const labels = {
    scheduled: "Scheduled",
    materials: "Materials Posted",
    summarized: "Summarized",
    updated: "Updated",
  };
  return (
    <span className={`pill ${status}`}>
      <span className="dot" />
      {label || labels[status] || status}
    </span>
  );
};

const Tag = ({ children }) => <span className="tag">{children}</span>;

const IconBtn = ({ icon, label, onClick, active, title }) => (
  <button
    className={`btn btn-sm btn-ghost ${active ? "is-active" : ""}`}
    onClick={onClick}
    title={title || label}
    style={{ gap: 6 }}
  >
    <Icon name={icon} />
    {label && <span>{label}</span>}
  </button>
);

// ─── Sidebar ──────────────────────────────────────────────────────────────────
const Sidebar = ({ route, onNav }) => {
  const groups = [
    { label: "Work", items: [
      { id: "overview", icon: "calendar", label: "Overview" },
      { id: "meeting",  icon: "list",     label: "Meetings" },
      { id: "briefing", icon: "book",     label: "Briefings" },
      { id: "deepdive", icon: "spark",    label: "Deep Dive" },
    ]},
    { label: "Pipeline", items: [
      { id: "add",      icon: "plus",     label: "Add Meeting" },
      { id: "bulk",     icon: "refresh",  label: "Bulk Summarize" },
      { id: "prompts",  icon: "library",  label: "Prompt Library" },
    ]},
    { label: "Account", items: [
      { id: "settings", icon: "settings", label: "Settings" },
    ]},
  ];

  const activeId = (() => {
    if (route.name === "meeting") return "meeting";
    if (route.name === "briefing") return "briefing";
    if (route.name === "add") return "add";
    return route.name;
  })();

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="mark">
          Poolside<span className="mark-accent">.</span>
        </span>
        <span className="ver">v0.4</span>
      </div>

      <div className="cmd-trigger" style={{ margin: "0 var(--pad-5) var(--pad-3)" }}>
        <Icon name="search" />
        <span className="lbl">Search meetings…</span>
        <span style={{ flex: 1 }} />
        <span className="kbd">⌘K</span>
      </div>

      {groups.map((g) => (
        <div key={g.label}>
          <div className="sidebar-group-label">{g.label}</div>
          {g.items.map((it) => (
            <button
              key={it.id}
              className={`sidebar-link ${activeId === it.id ? "active" : ""}`}
              onClick={() => onNav({ name: it.id })}
            >
              <span className="glyph"><Icon name={it.icon} /></span>
              <span>{it.label}</span>
            </button>
          ))}
        </div>
      ))}

      <div className="sidebar-foot">
        <div className="user-chip">
          <div className="user-avatar">BG</div>
          <div className="user-meta">
            <div className="name">Ben Griffiths</div>
            <div className="email">ben@poolside.io</div>
          </div>
        </div>
      </div>
    </aside>
  );
};

// ─── Topbar ───────────────────────────────────────────────────────────────────
const Topbar = ({ crumbs, actions }) => (
  <div className="topbar">
    <div className="topbar-crumbs">
      {crumbs.map((c, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span className="sep">/</span>}
          {c.onClick && i < crumbs.length - 1 ? (
            <button className="btn btn-ghost btn-sm" style={{ padding: "2px 4px", fontFamily: "inherit", letterSpacing: "inherit", color: "var(--muted)", textTransform: "inherit" }} onClick={c.onClick}>{c.label}</button>
          ) : (
            <span className={i === crumbs.length - 1 ? "crumb-cur" : ""}>{c.label}</span>
          )}
        </React.Fragment>
      ))}
    </div>
    <div className="topbar-actions">{actions}</div>
  </div>
);

Object.assign(window, { Icon, Pill, Tag, IconBtn, Sidebar, Topbar });
