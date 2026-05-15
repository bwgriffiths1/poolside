import { NavLink, useLocation } from "react-router-dom";
import { Icon, type IconName } from "./Icon";
import type { CurrentUser } from "../types";

interface NavItem {
  to: string;
  icon: IconName;
  label: string;
  matchPrefix?: string;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const GROUPS: NavGroup[] = [
  {
    label: "Work",
    items: [
      { to: "/overview", icon: "calendar", label: "Overview" },
      { to: "/meetings", icon: "list", label: "Meetings", matchPrefix: "/meeting" },
      { to: "/briefings", icon: "book", label: "Briefings", matchPrefix: "/briefing" },
      { to: "/deepdive", icon: "spark", label: "Deep Dive" },
    ],
  },
  {
    label: "Pipeline",
    items: [
      { to: "/add", icon: "plus", label: "Add Meeting" },
      { to: "/bulk", icon: "refresh", label: "Bulk Summarize" },
      { to: "/prompts", icon: "library", label: "Prompt Library" },
    ],
  },
  {
    label: "Account",
    items: [{ to: "/settings", icon: "settings", label: "Settings" }],
  },
];

interface SidebarProps {
  user: CurrentUser;
}

export function Sidebar({ user }: SidebarProps) {
  const { pathname } = useLocation();

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="mark">
          Poolside<span className="mark-accent">.</span>
        </span>
        <span className="ver">v0.4</span>
      </div>

      <button className="cmd-trigger" type="button">
        <Icon name="search" />
        <span className="lbl">Search meetings…</span>
        <span style={{ flex: 1 }} />
        <span className="kbd">⌘K</span>
      </button>

      {GROUPS.map((g) => (
        <div key={g.label}>
          <div className="sidebar-group-label">{g.label}</div>
          {g.items.map((it) => {
            const active = it.matchPrefix
              ? pathname.startsWith(it.matchPrefix)
              : pathname === it.to || pathname.startsWith(`${it.to}/`);
            return (
              <NavLink
                key={it.to}
                to={it.to}
                className={`sidebar-link ${active ? "active" : ""}`}
              >
                <span className="glyph">
                  <Icon name={it.icon} />
                </span>
                <span>{it.label}</span>
              </NavLink>
            );
          })}
        </div>
      ))}

      <div className="sidebar-foot">
        <div className="user-chip">
          <div className="user-avatar">{user.initials}</div>
          <div className="user-meta">
            <div className="name">{user.name}</div>
            <div className="email">{user.email}</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
