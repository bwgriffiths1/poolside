import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { api, type UserTokenRow } from "../lib/api";

function dollars(n: number, frac = 4): string {
  return `$${n.toFixed(frac)}`;
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function Admin() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["usage-dashboard"],
    queryFn: () => api.usageDashboard(),
  });

  // Pad trailing series to 6 months so the bar chart layout is stable.
  const series = useMemo(() => {
    if (!data) return [];
    const arr = [...data.trailing_six_months];
    return arr;
  }, [data]);

  const maxMonth = series.reduce((m, p) => Math.max(m, p.cost_usd), 0);

  return (
    <>
      <Topbar crumbs={[{ label: "Admin", to: "/admin" }, { label: "Usage" }]} />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Admin · Usage</div>
          <h1 className="page-title">Summarization usage</h1>
          <p className="page-subtitle">
            Token + cost totals derived from completed summarize jobs. Numbers
            reflect actual API usage captured at run time, not the pre-flight
            estimates.
          </p>
        </div>

        {isLoading && <div className="muted">Loading…</div>}
        {error && (
          <div className="empty" style={{ color: "var(--accent)" }}>
            Could not load usage: {(error as Error).message}
          </div>
        )}

        {data && (
          <>
            <section style={{ marginBottom: 28 }}>
              <h2 className="section-head">{data.month_label}</h2>
              <div className="usage-grid">
                <Tile label="Total cost" value={dollars(data.this_month.cost_usd, 2)} sub={`${data.this_month.jobs} job${data.this_month.jobs === 1 ? "" : "s"}`} />
                <Tile label="Input tokens" value={compact(data.this_month.input_tokens)} sub="this month" />
                <Tile label="Output tokens" value={compact(data.this_month.output_tokens)} sub="this month" />
                <Tile
                  label="vs. last month"
                  value={
                    data.last_month.jobs > 0
                      ? `${data.last_month.cost_usd === 0 ? "—" : ((data.this_month.cost_usd / data.last_month.cost_usd - 1) * 100).toFixed(0) + "%"}`
                      : "—"
                  }
                  sub={`Last month: ${dollars(data.last_month.cost_usd, 2)}`}
                />
              </div>
            </section>

            <section style={{ marginBottom: 28 }}>
              <h2 className="section-head">By committee · this month</h2>
              {data.by_committee_this_month.length === 0 ? (
                <div className="empty">No completed summarize jobs yet this month.</div>
              ) : (
                <div className="usage-table">
                  <div className="usage-row usage-row-head">
                    <div style={{ flex: 0.7 }}>Venue</div>
                    <div style={{ flex: 0.7 }}>Committee</div>
                    <div style={{ flex: 0.5, textAlign: "right" }}>Jobs</div>
                    <div style={{ flex: 0.8, textAlign: "right" }}>Cost</div>
                    <div style={{ flex: 2 }} />
                  </div>
                  {data.by_committee_this_month.map((r, i) => {
                    const max = data.by_committee_this_month[0].cost_usd || 1;
                    const pct = Math.max(2, Math.round((r.cost_usd / max) * 100));
                    return (
                      <div className="usage-row" key={i}>
                        <div style={{ flex: 0.7 }} className="mono text-xs">{r.venue}</div>
                        <div style={{ flex: 0.7 }} className="mono text-xs">{r.committee}</div>
                        <div style={{ flex: 0.5, textAlign: "right" }} className="mono">{r.jobs}</div>
                        <div style={{ flex: 0.8, textAlign: "right" }} className="mono">{dollars(r.cost_usd, 2)}</div>
                        <div style={{ flex: 2 }}>
                          <div className="usage-bar">
                            <div className="usage-bar-fill" style={{ width: `${pct}%` }} />
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            <section>
              <h2 className="section-head">Trailing 6 months</h2>
              {series.length === 0 ? (
                <div className="empty">No completed summarize jobs yet.</div>
              ) : (
                <div className="usage-bars">
                  {series.map((p) => {
                    const pct = maxMonth > 0
                      ? Math.max(3, Math.round((p.cost_usd / maxMonth) * 100))
                      : 3;
                    return (
                      <div className="usage-bars-col" key={p.month}>
                        <div className="usage-bars-track" title={`${dollars(p.cost_usd, 2)} · ${p.jobs} job${p.jobs === 1 ? "" : "s"}`}>
                          <div className="usage-bars-fill" style={{ height: `${pct}%` }} />
                        </div>
                        <div className="usage-bars-label muted text-xs">
                          {p.month.slice(5)}
                        </div>
                        <div className="usage-bars-value text-xs mono">
                          {dollars(p.cost_usd, 2)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            <section style={{ marginTop: 48 }}>
              <UserTokensPanel />
            </section>

            <div style={{ height: 64 }} />
          </>
        )}
      </div>
    </>
  );
}

function UserTokensPanel() {
  const qc = useQueryClient();
  const tokens = useQuery({
    queryKey: ["user-tokens"],
    queryFn: () => api.listUserTokens(),
  });
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [resetEmail, setResetEmail] = useState("");
  const [showToken, setShowToken] = useState<UserTokenRow | null>(null);

  const createInvite = useMutation({
    mutationFn: () =>
      api.createInvite({ email: inviteEmail, name: inviteName }),
    onSuccess: (row) => {
      setShowToken(row);
      setInviteEmail("");
      setInviteName("");
      qc.invalidateQueries({ queryKey: ["user-tokens"] });
    },
    onError: (e: Error) => alert(e.message),
  });
  const createReset = useMutation({
    mutationFn: () => api.createPasswordReset(resetEmail),
    onSuccess: (row) => {
      setShowToken(row);
      setResetEmail("");
      qc.invalidateQueries({ queryKey: ["user-tokens"] });
    },
    onError: (e: Error) => alert(e.message),
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api.revokeUserToken(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["user-tokens"] }),
  });

  const urlFor = (t: UserTokenRow) =>
    `${window.location.origin}/#/accept/${t.token}`;

  const copy = async (t: UserTokenRow) => {
    const url = urlFor(t);
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      window.prompt("Copy this URL:", url);
    }
  };

  return (
    <>
      <h2 className="section-head">Users</h2>
      <p className="muted text-sm" style={{ marginBottom: 12 }}>
        Email infrastructure isn't wired yet — generate an invite or reset
        link below, copy the URL, and forward it to the user yourself.
      </p>

      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <input
          className="input"
          placeholder="email@example.com"
          value={inviteEmail}
          onChange={(e) => setInviteEmail(e.target.value)}
          style={{ flex: "1 1 220px", minWidth: 200 }}
        />
        <input
          className="input"
          placeholder="Full name"
          value={inviteName}
          onChange={(e) => setInviteName(e.target.value)}
          style={{ flex: "1 1 200px", minWidth: 180 }}
        />
        <button
          className="btn btn-sm btn-accent"
          disabled={!inviteEmail || !inviteName || createInvite.isPending}
          onClick={() => createInvite.mutate()}
        >
          <Icon name="plus" size={12} /> Invite user
        </button>
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        <input
          className="input"
          placeholder="email of existing user"
          value={resetEmail}
          onChange={(e) => setResetEmail(e.target.value)}
          style={{ flex: "1 1 220px", minWidth: 200 }}
        />
        <button
          className="btn btn-sm"
          disabled={!resetEmail || createReset.isPending}
          onClick={() => createReset.mutate()}
        >
          <Icon name="refresh" size={12} /> Generate password reset
        </button>
      </div>

      {showToken && (
        <div
          style={{
            background: "var(--accent-tint)",
            border: "1px solid var(--accent-soft)",
            padding: "12px 14px",
            borderRadius: "var(--radius)",
            marginBottom: 16,
          }}
        >
          <div className="text-sm" style={{ marginBottom: 6 }}>
            {showToken.purpose === "invite"
              ? `Invite link for ${showToken.email}:`
              : `Password reset for ${showToken.email}:`}
          </div>
          <div
            className="mono text-xs"
            style={{
              wordBreak: "break-all",
              background: "var(--bg-elev)",
              padding: "6px 8px",
              borderRadius: "var(--radius-sm)",
              marginBottom: 8,
            }}
          >
            {urlFor(showToken)}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <button
              className="btn btn-sm"
              onClick={() => copy(showToken)}
            >
              <Icon name="copy" size={12} /> Copy
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => setShowToken(null)}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {tokens.isLoading ? (
        <div className="muted">Loading…</div>
      ) : (tokens.data ?? []).length === 0 ? (
        <div className="empty">No invites or resets yet.</div>
      ) : (
        <div className="usage-table">
          <div className="usage-row usage-row-head">
            <div style={{ flex: 0.7 }}>Kind</div>
            <div style={{ flex: 1.5 }}>Email</div>
            <div style={{ flex: 1 }}>Created</div>
            <div style={{ flex: 0.7 }}>Status</div>
            <div style={{ flex: 0.8, textAlign: "right" }}>Actions</div>
          </div>
          {(tokens.data ?? []).map((t) => (
            <div className="usage-row" key={t.id}>
              <div style={{ flex: 0.7 }} className="mono text-xs">
                {t.purpose === "invite" ? "invite" : "reset"}
              </div>
              <div style={{ flex: 1.5 }}>
                <div>{t.email}</div>
                {t.name && <div className="muted text-xs">{t.name}</div>}
              </div>
              <div style={{ flex: 1 }} className="muted text-xs">
                {new Date(t.created_at).toLocaleString()}
              </div>
              <div style={{ flex: 0.7 }} className="text-xs mono">
                {t.status}
              </div>
              <div
                style={{ flex: 0.8, textAlign: "right", display: "flex", gap: 4, justifyContent: "flex-end" }}
              >
                {t.status === "active" && (
                  <>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => copy(t)}
                      title="Copy URL"
                    >
                      <Icon name="copy" size={12} />
                    </button>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => {
                        if (confirm("Revoke this token?")) revoke.mutate(t.id);
                      }}
                      title="Revoke"
                    >
                      <Icon name="trash" size={12} />
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="usage-tile">
      <div className="usage-tile-label">{label}</div>
      <div className="usage-tile-num">{value}</div>
      <div className="usage-tile-sub">{sub}</div>
    </div>
  );
}
