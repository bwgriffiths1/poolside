import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type AuditItem } from "../../lib/api";
import { qk } from "../../lib/queries";
import { toast } from "../../lib/toast";

/** Admin → Activity: the audit feed. First page via react-query; older
 *  pages append into local state through the keyset cursor (before_id). */
export function ActivityPanel() {
  const first = useQuery({ queryKey: qk.audit, queryFn: () => api.listAudit() });
  const [older, setOlder] = useState<AuditItem[]>([]);
  const [cursor, setCursor] = useState<number | null | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);

  const items = [...(first.data?.items ?? []), ...older];
  // undefined = not paged yet → fall back to the first page's cursor.
  const nextBefore = cursor === undefined ? first.data?.next_before_id : cursor;

  const loadMore = async () => {
    if (!nextBefore) return;
    setLoadingMore(true);
    try {
      const page = await api.listAudit({ before_id: nextBefore });
      setOlder((prev) => [...prev, ...page.items]);
      setCursor(page.next_before_id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load more.");
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <>
      <h2 className="section-head">Activity</h2>
      <p className="muted text-sm" style={{ marginBottom: 12 }}>
        Every write action, captured automatically — who did what, when.
        Red rows were denied or failed.
      </p>

      {first.isLoading ? (
        <div className="muted">Loading…</div>
      ) : items.length === 0 ? (
        <div className="empty">No activity recorded yet.</div>
      ) : (
        <>
          <div className="usage-table">
            <div className="usage-row usage-row-head">
              <div style={{ flex: 1.6 }}>Action</div>
              <div style={{ flex: 1.2 }}>Who</div>
              <div style={{ flex: 1 }}>When</div>
              <div style={{ flex: 0.5, textAlign: "right" }}>Status</div>
            </div>
            {items.map((a) => (
              <div className="usage-row" key={a.id}>
                <div style={{ flex: 1.6 }}>
                  <div>{a.label}</div>
                  <div className="muted text-xs mono">{a.path}</div>
                </div>
                <div style={{ flex: 1.2 }} className="text-xs">{a.user_email}</div>
                <div style={{ flex: 1 }} className="muted text-xs">
                  {new Date(a.created_at).toLocaleString(undefined, {
                    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                  })}
                </div>
                <div
                  style={{
                    flex: 0.5,
                    textAlign: "right",
                    color: a.status >= 400 ? "var(--danger)" : undefined,
                    fontWeight: a.status >= 400 ? 600 : undefined,
                  }}
                  className="mono text-xs"
                  title={a.status >= 400 ? "Denied or failed" : undefined}
                >
                  {a.status}
                </div>
              </div>
            ))}
          </div>
          {nextBefore != null && (
            <div style={{ marginTop: 10 }}>
              <button className="btn btn-sm" disabled={loadingMore} onClick={loadMore}>
                {loadingMore ? "Loading…" : "Load older"}
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}
