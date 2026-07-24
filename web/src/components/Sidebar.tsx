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
  /** Minimum role to see this link; absent = every role. */
  requires?: "editor" | "admin";
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
      { to: "/roundups", icon: "globe", label: "Roundups", matchPrefix: "/roundup" },
      { to: "/deep-dives", icon: "spark", label: "Deep Dives", matchPrefix: "/deep-dive" },
      { to: "/elibrary", icon: "doc", label: "FERC eLibrary", matchPrefix: "/docket" },
      { to: "/initiatives", icon: "tag", label: "Initiatives", matchPrefix: "/initiative" },
      { to: "/search", icon: "search", label: "Search" },
      { to: "/ask", icon: "chat", label: "Ask" },
    ],
  },
  {
    label: "Pipeline",
    items: [
      { to: "/add", icon: "plus", label: "Add Meeting", requires: "editor" },
      { to: "/prompts", icon: "library", label: "Prompt Library", requires: "admin" },
    ],
  },
  {
    label: "Account",
    items: [
      { to: "/admin", icon: "spark", label: "Admin", requires: "admin" },
      { to: "/settings", icon: "settings", label: "Settings" },
    ],
  },
];

function canSee(item: NavItem, role: CurrentUser["role"]): boolean {
  if (item.requires === "admin") return role === "admin";
  if (item.requires === "editor") return role === "admin" || role === "editor";
  return true;
}

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

      {GROUPS.map((g) => {
        const visible = g.items.filter((it) => canSee(it, user.role));
        if (!visible.length) return null; // Pipeline vanishes for viewers
        return (
          <div key={g.label}>
            <div className="sidebar-group-label">{g.label}</div>
            {visible.map((it) => {
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
        );
      })}

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
