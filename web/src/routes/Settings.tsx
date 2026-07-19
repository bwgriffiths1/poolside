import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { api, type AppConfig, type AppConfigCommittee } from "../lib/api";
import { qk } from "../lib/queries";
import { toast } from "../lib/toast";

function clamp(n: number, lo: number, hi: number): number {
  if (Number.isNaN(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

function emptyRow(): AppConfigCommittee {
  return { name: "", short: "", url: "", active: true };
}

const PREF_ROWS: { key: "briefing_ready" | "weekly_digest"; label: string; hint: string }[] = [
  {
    key: "briefing_ready",
    label: "Briefing ready",
    hint: "Email me when a meeting I watch gets an approved briefing.",
  },
  {
    key: "weekly_digest",
    label: "Weekly digest",
    hint: "Monday morning week-ahead: upcoming meetings + last week's new briefings.",
  },
];

function NotificationPrefs() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: qk.myPrefs,
    queryFn: () => api.getMyPrefs(),
  });

  const update = useMutation({
    mutationFn: (patch: Partial<Record<string, boolean>>) =>
      api.updateMyPrefs({ email_prefs: patch }),
    onSuccess: (fresh) => qc.setQueryData(qk.myPrefs, fresh),
    onError: (e: Error) => toast.error(`Couldn't save preference: ${e.message}`),
  });

  return (
    <section style={{ marginBottom: 28 }}>
      <h2 className="section-head">Email notifications</h2>
      {data && !data.mail_configured && (
        <p className="muted text-sm" style={{ marginBottom: 10 }}>
          Email sending isn't configured on the server yet (RESEND_API_KEY) —
          your choices are saved and take effect once it is.
        </p>
      )}
      {isLoading && <div className="muted text-sm">Loading…</div>}
      {data && (
        <div className="settings-table">
          {PREF_ROWS.map((row) => (
            <label
              key={row.key}
              className="settings-row"
              style={{ alignItems: "center", cursor: "pointer" }}
            >
              <input
                type="checkbox"
                checked={!!data.email_prefs[row.key]}
                disabled={update.isPending}
                onChange={(e) => update.mutate({ [row.key]: e.target.checked })}
              />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>{row.label}</div>
                <div className="muted text-xs">{row.hint}</div>
              </div>
            </label>
          ))}
        </div>
      )}
    </section>
  );
}

export function Settings() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: qk.appConfig,
    queryFn: () => api.getConfig(),
  });

  const [draft, setDraft] = useState<AppConfig | null>(null);

  useEffect(() => {
    if (data) setDraft({ ...data, committees: data.committees.map((c) => ({ ...c })) });
  }, [data]);

  const save = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("nothing to save");
      const cleaned: AppConfig = {
        lookahead_days: clamp(Number(draft.lookahead_days), 7, 365),
        committees: draft.committees
          .map((c) => ({
            name: c.name.trim(),
            short: c.short.trim(),
            url: c.url.trim(),
            active: !!c.active,
          }))
          .filter((c) => c.name || c.url),
      };
      return api.saveConfig(cleaned);
    },
    onSuccess: (fresh) => {
      qc.setQueryData(qk.appConfig, fresh);
      setDraft({ ...fresh, committees: fresh.committees.map((c) => ({ ...c })) });
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  const dirty =
    !!draft &&
    !!data &&
    JSON.stringify(draft) !== JSON.stringify(data);

  const updateCommittee = (i: number, patch: Partial<AppConfigCommittee>) => {
    if (!draft) return;
    const next = draft.committees.slice();
    next[i] = { ...next[i], ...patch };
    setDraft({ ...draft, committees: next });
  };

  const addRow = () => {
    if (!draft) return;
    setDraft({ ...draft, committees: [...draft.committees, emptyRow()] });
  };

  const removeRow = (i: number) => {
    if (!draft) return;
    const next = draft.committees.slice();
    next.splice(i, 1);
    setDraft({ ...draft, committees: next });
  };

  return (
    <>
      <Topbar
        crumbs={[{ label: "Settings" }]}
        actions={
          <>
            <button
              className="btn btn-sm"
              disabled={!dirty || save.isPending}
              onClick={() =>
                data && setDraft({ ...data, committees: data.committees.map((c) => ({ ...c })) })
              }
            >
              Discard
            </button>
            <button
              className="btn btn-sm btn-primary"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
            >
              <Icon name="check" /> {save.isPending ? "Saving…" : "Save"}
            </button>
          </>
        }
      />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Account · Settings</div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">
            Your email notifications, the committees the scraper monitors, and
            how far ahead to look.
          </p>
        </div>

        {isLoading && <div className="empty">Loading…</div>}
        {error && (
          <div className="empty" style={{ color: "var(--accent)" }}>
            Could not load settings: {(error as Error).message}
          </div>
        )}

        <NotificationPrefs />

        {draft && (
          <>
            <section style={{ marginBottom: 28 }}>
              <h2 className="section-head">Scraper</h2>
              <div className="row" style={{ gap: 12, alignItems: "center" }}>
                <label className="field-label" style={{ marginBottom: 0 }}>
                  Lookahead days
                </label>
                <input
                  className="input"
                  type="number"
                  min={7}
                  max={365}
                  step={7}
                  value={draft.lookahead_days}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      lookahead_days: clamp(Number(e.target.value), 7, 365),
                    })
                  }
                  style={{ width: 120 }}
                />
                <span className="muted text-sm">
                  How many calendar days ahead to scan for upcoming meetings (7–365).
                </span>
              </div>
            </section>

            <section>
              <h2 className="section-head">Committees</h2>
              <p className="muted text-sm" style={{ marginBottom: 12 }}>
                Each row defines an ISO-NE committee whose calendar page is scraped.
                "Short" must match a committee short name in the database (MC, RC, NPC, …).
              </p>

              <div className="settings-table">
                <div className="settings-row settings-row-head">
                  <div style={{ flex: 2 }}>Name</div>
                  <div style={{ flex: 0.5 }}>Short</div>
                  <div style={{ flex: 3 }}>Calendar URL</div>
                  <div style={{ flex: 0.4, textAlign: "center" }}>Active</div>
                  <div style={{ flex: 0.4 }}></div>
                </div>
                {draft.committees.map((c, i) => (
                  <div key={i} className="settings-row">
                    <input
                      className="input"
                      style={{ flex: 2 }}
                      value={c.name}
                      placeholder="Markets Committee"
                      onChange={(e) => updateCommittee(i, { name: e.target.value })}
                    />
                    <input
                      className="input"
                      style={{ flex: 0.5 }}
                      value={c.short}
                      placeholder="MC"
                      onChange={(e) => updateCommittee(i, { short: e.target.value })}
                    />
                    <input
                      className="input"
                      style={{ flex: 3 }}
                      value={c.url}
                      placeholder="https://…"
                      onChange={(e) => updateCommittee(i, { url: e.target.value })}
                    />
                    <div style={{ flex: 0.4, textAlign: "center" }}>
                      <input
                        type="checkbox"
                        checked={c.active}
                        onChange={(e) => updateCommittee(i, { active: e.target.checked })}
                      />
                    </div>
                    <div style={{ flex: 0.4 }}>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        title="Remove row"
                        onClick={() => removeRow(i)}
                      >
                        <Icon name="x" size={12} />
                      </button>
                    </div>
                  </div>
                ))}
                <div style={{ marginTop: 8 }}>
                  <button className="btn btn-sm" onClick={addRow}>
                    <Icon name="plus" size={12} /> Add committee
                  </button>
                </div>
              </div>
            </section>

            <div style={{ height: 64 }} />
          </>
        )}
      </div>
    </>
  );
}
