import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

export interface Crumb {
  label: string;
  to?: string;
}

interface TopbarProps {
  crumbs: Crumb[];
  actions?: ReactNode;
}

export function Topbar({ crumbs, actions }: TopbarProps) {
  const navigate = useNavigate();
  return (
    <div className="topbar">
      <div className="topbar-crumbs">
        {crumbs.map((c, i) => {
          const isLast = i === crumbs.length - 1;
          return (
            <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              {i > 0 && <span className="sep">/</span>}
              {c.to && !isLast ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{
                    padding: "2px 4px",
                    fontFamily: "inherit",
                    letterSpacing: "inherit",
                    color: "var(--muted)",
                    textTransform: "inherit",
                  }}
                  onClick={() => navigate(c.to!)}
                >
                  {c.label}
                </button>
              ) : (
                <span className={isLast ? "crumb-cur" : ""}>{c.label}</span>
              )}
            </span>
          );
        })}
      </div>
      <div className="topbar-actions">{actions}</div>
    </div>
  );
}
