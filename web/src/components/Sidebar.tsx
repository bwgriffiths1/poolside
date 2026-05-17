import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Icon, type IconName } from "./Icon";
import { NotificationBell } from "./NotificationBell";
import { api } from "../lib/api";
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
    ],
  },
  {
    label: "Pipeline",
    items: [
      { to: "/add", icon: "plus", label: "Add Meeting" },
      { to: "/prompts", icon: "library", label: "Prompt Library" },
    ],
  },
  {
    label: "Dev",
    items: [
      { to: "/deepdive", icon: "spark", label: "Deep Dive" },
      { to: "/bulk", icon: "refresh", label: "Bulk Summarize" },
    ],
  },
  {
    label: "Account",
    items: [
      { to: "/admin", icon: "spark", label: "Admin · Usage" },
      { to: "/settings", icon: "settings", label: "Settings" },
    ],
  },
];

interface SidebarProps {
  user: CurrentUser;
  onOpenPalette: () => void;
}

export function Sidebar({ user, onOpenPalette }: SidebarProps) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const onLogout = async () => {
    try {
      await api.logout();
    } catch { /* ignore — clear local state regardless */ }
    qc.clear();
    navigate("/login", { replace: true });
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="mark">
          Poolside<span className="mark-accent">.</span>
        </span>
      </div>

      <button className="cmd-trigger" type="button" onClick={onOpenPalette}>
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
          <NotificationBell />
          <button
            type="button"
            className="user-logout"
            onClick={onLogout}
            title="Sign out"
            aria-label="Sign out"
          >
            <Icon name="logout" size={14} />
          </button>
        </div>
      </div>
    </aside>
  );
}
