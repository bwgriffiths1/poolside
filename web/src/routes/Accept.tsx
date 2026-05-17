import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function Accept() {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();

  const preview = useQuery({
    queryKey: ["token-preview", token],
    queryFn: () => api.publicTokenPreview(token as string),
    enabled: !!token,
    retry: false,
  });

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await api.publicTokenAccept(token as string, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't set password.");
    } finally {
      setSubmitting(false);
    }
  };

  if (preview.isLoading) {
    return (
      <div className="login-shell">
        <div className="muted">Verifying link…</div>
      </div>
    );
  }
  if (preview.isError || !preview.data) {
    return (
      <div className="login-shell">
        <div className="login-card">
          <div className="login-brand">
            Poolside<span className="mark-accent">.</span>
          </div>
          <div className="login-sub">Link unavailable</div>
          <p className="muted text-sm" style={{ margin: "16px 0 0" }}>
            {(preview.error as Error)?.message ??
              "This link is missing, has been revoked, used, or has expired."}
          </p>
        </div>
      </div>
    );
  }
  if (done) {
    return (
      <div className="login-shell">
        <div className="login-card">
          <div className="login-brand">
            Poolside<span className="mark-accent">.</span>
          </div>
          <div className="login-sub">
            {preview.data.purpose === "invite"
              ? "Account ready"
              : "Password updated"}
          </div>
          <p className="muted text-sm" style={{ margin: "16px 0 24px" }}>
            You can now sign in with{" "}
            <strong>{preview.data.email}</strong> and your new password.
          </p>
          <button
            type="button"
            className="btn btn-primary login-submit"
            onClick={() => navigate("/login", { replace: true })}
          >
            Go to sign in
          </button>
        </div>
      </div>
    );
  }

  const purposeLabel =
    preview.data.purpose === "invite" ? "Set your password" : "Reset your password";

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="login-brand">
          Poolside<span className="mark-accent">.</span>
        </div>
        <div className="login-sub">{purposeLabel}</div>

        <p className="muted text-sm" style={{ margin: "8px 0 16px" }}>
          {preview.data.purpose === "invite"
            ? "You've been invited to Poolside. Choose a password to finish setting up your account."
            : "Pick a new password to regain access to your account."}
        </p>

        <label className="login-label">Email</label>
        <input
          type="email"
          className="login-input"
          value={preview.data.email}
          disabled
          autoComplete="username"
        />

        <label className="login-label">New password</label>
        <input
          type="password"
          className="login-input"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />

        <label className="login-label">Confirm password</label>
        <input
          type="password"
          className="login-input"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />

        {error && <div className="login-error">{error}</div>}

        <button
          type="submit"
          className="btn btn-primary login-submit"
          disabled={submitting}
        >
          {submitting
            ? "Saving…"
            : preview.data.purpose === "invite"
            ? "Create account"
            : "Update password"}
        </button>
      </form>
    </div>
  );
}
