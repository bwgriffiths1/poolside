import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import {
  api,
  type AppUser,
  type UserTokenCreated,
  type UserTokenRow,
} from "../../lib/api";
import { qk, useMe } from "../../lib/queries";
import { toast } from "../../lib/toast";
import type { Role } from "../../types";

const ROLES: Role[] = ["admin", "editor", "viewer"];

/** Admin → Users: account table (role / active), invites with a role
 *  picker, password resets, and the outstanding-token list. Mounted on the
 *  admin-gated /admin route; the backend re-checks admin on every call. */
export function UsersPanel() {
  return (
    <>
      <UserTable />
      <TokensSection />
    </>
  );
}

// ── Accounts ────────────────────────────────────────────────────────────

function UserTable() {
  const qc = useQueryClient();
  const me = useMe().data;
  const users = useQuery({ queryKey: qk.users, queryFn: api.listUsers });

  const update = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { role?: Role; is_active?: boolean } }) =>
      api.updateUser(id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.users }),
    // Guard rails surface here: self-change 400s, last-admin 409s.
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <>
      <h2 className="section-head">Users</h2>
      {users.isLoading ? (
        <div className="muted">Loading…</div>
      ) : (users.data ?? []).length === 0 ? (
        <div className="empty">No accounts yet.</div>
      ) : (
        <div className="usage-table" style={{ marginBottom: 28 }}>
          <div className="usage-row usage-row-head">
            <div style={{ flex: 1.6 }}>Account</div>
            <div style={{ flex: 0.8 }}>Role</div>
            <div style={{ flex: 0.9 }}>Last login</div>
            <div style={{ flex: 0.6 }}>Status</div>
            <div style={{ flex: 0.8, textAlign: "right" }}>Actions</div>
          </div>
          {(users.data ?? []).map((u) => {
            const isSelf = u.id === me?.id;
            return (
              <div className="usage-row" key={u.id} style={u.is_active ? undefined : { opacity: 0.55 }}>
                <div style={{ flex: 1.6 }}>
                  <div>
                    {u.name}
                    {isSelf && <span className="muted text-xs"> · you</span>}
                  </div>
                  <div className="muted text-xs">{u.email}</div>
                </div>
                <div style={{ flex: 0.8 }}>
                  <select
                    className="input"
                    value={u.role}
                    disabled={isSelf || update.isPending}
                    title={isSelf ? "You can't change your own role." : undefined}
                    onChange={(e) =>
                      update.mutate({ id: u.id, patch: { role: e.target.value as Role } })
                    }
                    style={{ padding: "4px 8px", fontSize: 13 }}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                </div>
                <div style={{ flex: 0.9 }} className="muted text-xs">
                  {u.last_login
                    ? new Date(u.last_login).toLocaleString(undefined, {
                        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                      })
                    : "never"}
                </div>
                <div style={{ flex: 0.6 }} className="text-xs mono">
                  {u.is_active ? "active" : "inactive"}
                </div>
                <div style={{ flex: 0.8, textAlign: "right" }}>
                  {!isSelf && (
                    <button
                      className="btn btn-sm btn-ghost"
                      disabled={update.isPending}
                      onClick={() => {
                        if (u.is_active && !confirm(`Deactivate ${u.email}? They are signed out immediately.`)) return;
                        update.mutate({ id: u.id, patch: { is_active: !u.is_active } });
                      }}
                    >
                      {u.is_active ? "Deactivate" : "Reactivate"}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

// ── Invites + password resets ───────────────────────────────────────────

function TokensSection() {
  const qc = useQueryClient();
  const tokens = useQuery({
    queryKey: qk.userTokens,
    queryFn: () => api.listUserTokens(),
  });
  const prefs = useQuery({ queryKey: qk.myPrefs, queryFn: api.getMyPrefs });
  const mailOn = prefs.data?.mail_configured ?? false;

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("viewer");
  const [resetEmail, setResetEmail] = useState("");
  const [showToken, setShowToken] = useState<UserTokenCreated | null>(null);

  const createInvite = useMutation({
    mutationFn: () =>
      api.createInvite({ email: inviteEmail, name: inviteName, role: inviteRole }),
    onSuccess: (row) => {
      setShowToken(row);
      setInviteEmail("");
      setInviteName("");
      setInviteRole("viewer");
      qc.invalidateQueries({ queryKey: qk.userTokens });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const createReset = useMutation({
    mutationFn: () => api.createPasswordReset(resetEmail),
    onSuccess: (row) => {
      setShowToken(row);
      setResetEmail("");
      qc.invalidateQueries({ queryKey: qk.userTokens });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api.revokeUserToken(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.userTokens }),
  });

  // Built from the browser origin so the copied link matches whatever
  // environment the admin is in (the emailed link uses the server's base).
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
      <h2 className="section-head">Invites & resets</h2>
      <p className="muted text-sm" style={{ marginBottom: 12 }}>
        {mailOn
          ? "Invite and reset links are emailed automatically — the copy-URL is always available as a fallback."
          : "Email isn't configured — generate a link below, copy the URL, and forward it to the user yourself."}
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
        <select
          className="input"
          value={inviteRole}
          onChange={(e) => setInviteRole(e.target.value as Role)}
          style={{ flex: "0 0 110px" }}
          title="Role for the new account"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
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
              ? `Invite for ${showToken.email}${showToken.role ? ` (${showToken.role})` : ""}`
              : `Password reset for ${showToken.email}`}
            {showToken.emailed
              ? " — emailed (or share the link below):"
              : " — copy and forward this link:"}
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
            <button className="btn btn-sm" onClick={() => copy(showToken)}>
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
            <div style={{ flex: 0.6 }}>Role</div>
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
              <div style={{ flex: 0.6 }} className="mono text-xs">
                {t.purpose === "invite" ? t.role ?? "viewer" : "—"}
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
