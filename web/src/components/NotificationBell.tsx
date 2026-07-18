import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Icon } from "./Icon";
import { api, type NotificationRow } from "../lib/api";
import { qk } from "../lib/queries";
import { formatRel } from "../lib/format";

const POLL_MS = 30_000;

function describe(n: NotificationRow): { title: string; sub: string } {
  if (n.kind === "briefing_approved") {
    const p = n.payload as {
      venue?: string;
      committee?: string;
      meeting_date?: string;
      approved_by?: string;
      title?: string;
    };
    return {
      title: `Briefing approved — ${p.venue ?? ""} ${p.committee ?? ""}`,
      sub: `${p.meeting_date ?? ""} · approved by ${p.approved_by ?? "someone"}`,
    };
  }
  if (n.kind === "drift_alarm") {
    const p = n.payload as { hours_silent?: number };
    return {
      title: "Discovery cron is quiet",
      sub: `${p.hours_silent ?? "?"}h since the last new meeting — scraper may need attention.`,
    };
  }
  if (n.kind === "job_failed") {
    const p = n.payload as { job?: string; error?: string };
    return {
      title: `Scheduled job failed — ${p.job ?? "unknown"}`,
      sub: p.error ?? "",
    };
  }
  if (n.kind === "materials_new") {
    const p = n.payload as {
      label?: string;
      new_doc_count?: number;
      affected_item_ids?: number[];
    };
    const items = p.affected_item_ids?.length
      ? `, ${p.affected_item_ids.length} agenda item(s) affected`
      : "";
    return {
      title: `New materials — ${p.label ?? "meeting"}`,
      sub: `${p.new_doc_count ?? "?"} new document(s)${items} — summaries may be stale.`,
    };
  }
  return { title: n.kind, sub: "" };
}

export function NotificationBell() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  const unread = useQuery({
    queryKey: qk.notificationsUnread,
    queryFn: () => api.unreadCount(),
    refetchInterval: POLL_MS,
    // refetch on focus so opening the tab gets a fresh count
    refetchOnWindowFocus: true,
  });

  const list = useQuery({
    queryKey: qk.notificationsList,
    queryFn: () => api.listNotifications(true),
    enabled: open,
    staleTime: 5_000,
  });

  const markAllRead = useMutation({
    mutationFn: () => api.markNotificationsRead(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.notificationsUnread });
      qc.invalidateQueries({ queryKey: qk.notificationsList });
    },
  });

  // Click outside closes the popover.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  const count = unread.data?.count ?? 0;
  const rows = list.data ?? [];

  const onRowClick = (n: NotificationRow) => {
    if (!n.read_at) {
      // Optimistically mark this one read.
      api.markNotificationsRead([n.id]).then(() => {
        qc.invalidateQueries({ queryKey: qk.notificationsUnread });
        qc.invalidateQueries({ queryKey: qk.notificationsList });
      });
    }
    if (n.meeting_id) navigate(`/meeting/${n.meeting_id}`);
    setOpen(false);
  };

  return (
    <div className="notif-bell-wrap" ref={popoverRef}>
      <button
        type="button"
        className="notif-bell"
        onClick={() => setOpen((o) => !o)}
        aria-label={count > 0 ? `${count} unread notifications` : "Notifications"}
      >
        <Icon name="bell" size={14} />
        {count > 0 && (
          <span className="notif-badge">{count > 99 ? "99+" : count}</span>
        )}
      </button>

      {open && (
        <div className="notif-popover" role="dialog" aria-label="Notifications">
          <div className="notif-popover-head">
            <span className="field-label" style={{ marginBottom: 0 }}>
              Notifications
            </span>
            <span style={{ flex: 1 }} />
            {count > 0 && (
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => markAllRead.mutate()}
                disabled={markAllRead.isPending}
              >
                Mark all read
              </button>
            )}
          </div>
          {list.isLoading ? (
            <div className="notif-empty muted text-sm">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="notif-empty muted text-sm">Nothing yet.</div>
          ) : (
            <div className="notif-list">
              {rows.map((n) => {
                const d = describe(n);
                return (
                  <button
                    key={n.id}
                    type="button"
                    className={`notif-row ${n.read_at ? "read" : "unread"}`}
                    onClick={() => onRowClick(n)}
                  >
                    <div className="notif-row-main">
                      <div className="notif-row-title">{d.title}</div>
                      {d.sub && <div className="notif-row-sub">{d.sub}</div>}
                    </div>
                    <div className="notif-row-time muted text-xs">
                      {formatRel(n.created_at, "")}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
