import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { api, type ShareToken } from "../lib/api";
import { toast } from "../lib/toast";

/**
 * Public-share-link manager, used by the Briefing reader and the Docket
 * page. The caller supplies the entity-specific list/create calls; revoke
 * is entity-agnostic (/share-tokens/:id). Links open `/#/share/<token>`,
 * which renders the matching read-only view without login.
 */
export function ShareLinkModal({
  label,
  queryKey,
  list,
  create,
  onClose,
}: {
  /** Noun for the heading + blurb: "briefing" | "docket". */
  label: string;
  queryKey: readonly unknown[];
  list: () => Promise<ShareToken[]>;
  create: (expires_days: number | null) => Promise<ShareToken>;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey, queryFn: list });
  const createMut = useMutation({
    mutationFn: create,
    onSuccess: () => qc.invalidateQueries({ queryKey }),
    onError: (e: Error) => toast.error(`Create failed: ${e.message}`),
  });
  const revoke = useMutation({
    mutationFn: (token_id: number) => api.revokeShareLink(token_id),
    onSuccess: () => qc.invalidateQueries({ queryKey }),
  });

  const [expiry, setExpiry] = useState<"30" | "90" | "never">("30");

  const onCreate = () => {
    const days = expiry === "never" ? null : Number(expiry);
    createMut.mutate(days);
  };

  const baseUrl = () => {
    // Use the same origin the user is on; hash router → /#/share/<token>.
    return `${window.location.origin}/#/share`;
  };

  const isActive = (t: ShareToken): boolean => {
    if (t.revoked_at) return false;
    if (t.expires_at && new Date(t.expires_at).getTime() < Date.now()) return false;
    return true;
  };

  const copy = async (t: ShareToken) => {
    const url = `${baseUrl()}/${t.token}`;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // Fallback: prompt — better than silent failure.
      window.prompt("Copy this link:", url);
    }
  };

  return (
    <div
      className="cmd-palette-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="share-modal" role="dialog" aria-label={`Share ${label}`}>
        <div className="share-modal-head">
          <h3 style={{ margin: 0, fontSize: 14 }}>Share this {label}</h3>
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            <Icon name="x" size={12} />
          </button>
        </div>

        <p className="muted text-sm" style={{ margin: "8px 0 14px" }}>
          A share link opens this {label} without requiring login. Anyone
          with the URL can read it until you revoke or it expires.
        </p>

        <div className="row" style={{ gap: 8, marginBottom: 14 }}>
          <label className="field-label" style={{ marginBottom: 0 }}>
            Expires
          </label>
          <select
            className="select"
            value={expiry}
            onChange={(e) => setExpiry(e.target.value as "30" | "90" | "never")}
            style={{ width: 140 }}
          >
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="never">Never</option>
          </select>
          <span style={{ flex: 1 }} />
          <button
            className="btn btn-sm btn-accent"
            onClick={onCreate}
            disabled={createMut.isPending}
          >
            <Icon name="plus" size={12} />{" "}
            {createMut.isPending ? "Creating…" : "Create link"}
          </button>
        </div>

        {tokens.isLoading ? (
          <div className="muted text-sm">Loading…</div>
        ) : (tokens.data ?? []).length === 0 ? (
          <div className="muted text-sm">No share links yet.</div>
        ) : (
          <div className="share-list">
            {(tokens.data ?? []).map((t) => (
              <div
                key={t.id}
                className={`share-row ${isActive(t) ? "" : "inactive"}`}
              >
                <div className="share-row-main">
                  <div className="share-row-url mono text-xs">
                    {baseUrl()}/{t.token.slice(0, 10)}…
                  </div>
                  <div className="muted text-xs" style={{ marginTop: 2 }}>
                    Created {new Date(t.created_at).toLocaleDateString()} ·{" "}
                    {t.revoked_at
                      ? "revoked"
                      : t.expires_at
                      ? `expires ${new Date(t.expires_at).toLocaleDateString()}`
                      : "no expiry"}
                  </div>
                </div>
                {isActive(t) ? (
                  <>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => copy(t)}
                      title="Copy URL"
                    >
                      <Icon name="copy" size={12} /> Copy
                    </button>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => {
                        if (confirm("Revoke this share link?")) {
                          revoke.mutate(t.id);
                        }
                      }}
                      title="Revoke"
                    >
                      <Icon name="trash" size={12} />
                    </button>
                  </>
                ) : (
                  <span className="muted text-xs">
                    {t.revoked_at ? "Revoked" : "Expired"}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
