import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { qk } from "../lib/queries";

export function Login() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setError("Please enter both email and password.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const user = await api.login(email.trim(), password);
      qc.setQueryData(qk.me, user);
      navigate("/overview", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="login-brand">
          Poolside<span className="mark-accent">.</span>
        </div>
        <div className="login-sub">Sign in to continue</div>

        <label className="login-label">Email</label>
        <input
          type="email"
          className="login-input"
          autoFocus
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <label className="login-label">Password</label>
        <input
          type="password"
          className="login-input"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <div className="login-error">{error}</div>}

        <button
          type="submit"
          className="btn btn-primary login-submit"
          disabled={submitting}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
