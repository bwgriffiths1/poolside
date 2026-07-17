import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Topbar } from "../components/Topbar";
import { Icon } from "../components/Icon";
import { Tag } from "../components/Tag";
import { api } from "../lib/api";
import type { RoundupMonth } from "../types";

function StatusCell({
  m,
  onGenerate,
  busy,
}: {
  m: RoundupMonth;
  onGenerate: () => void;
  busy: boolean;
}) {
  const r = m.roundup;
  if (r?.status === "generating") {
    return (
      <span className="ru-status ru-status-generating">
        <Icon name="refresh" size={12} />
        Generating…
      </span>
    );
  }
  if (r?.status === "complete") {
    return (
      <span className="ru-status ru-status-ready">
        <Icon name="check" size={12} />
        Ready
      </span>
    );
  }
  if (r?.status === "error") {
    return <span className="ru-status ru-status-error">Failed — open to retry</span>;
  }
  if (m.briefing_count === 0) {
    return <span className="text-xs muted">No briefings</span>;
  }
  return (
    <button
      className="btn btn-sm"
      disabled={busy}
      onClick={(e) => {
        e.stopPropagation();
        onGenerate();
      }}
    >
      <Icon name="spark" size={12} />
      {busy ? "Starting…" : "Generate"}
    </button>
  );
}

export function Roundups() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [genError, setGenError] = useState<string | null>(null);

  const { data: months = [], isLoading } = useQuery({
    queryKey: ["roundups"],
    queryFn: () => api.roundups(),
    refetchInterval: (query) => {
      const rows = query.state.data;
      return rows?.some((r) => r.roundup?.status === "generating")
        ? 4000
        : false;
    },
    // Generation takes minutes; keep polling while the tab is backgrounded.
    refetchIntervalInBackground: true,
  });

  const generate = useMutation({
    mutationFn: (month: string) => api.generateRoundup("ISO-NE", month),
    onSuccess: (r) => {
      setGenError(null);
      qc.invalidateQueries({ queryKey: ["roundups"] });
      navigate(`/roundup/${r.id}`);
    },
    onError: (e) => setGenError(e instanceof Error ? e.message : String(e)),
  });

  const withRoundups = months.filter((m) => m.roundup || m.briefing_count > 0);

  return (
    <>
      <Topbar crumbs={[{ label: "Roundups" }]} />

      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Monthly state of play · ISO-NE</div>
          <h1 className="page-title">Roundups</h1>
          <p className="page-subtitle">
            One ISO-wide report per month, synthesized from every committee
            briefing — MC, RC, TC and beyond — with month-over-month
            continuity.
          </p>
        </div>

        {genError && (
          <div className="ru-error-banner">
            <Icon name="x" size={12} /> {genError}
          </div>
        )}

        {isLoading ? (
          <div className="empty">Loading…</div>
        ) : withRoundups.length === 0 ? (
          <div className="empty">
            No months with briefings yet — summarize a meeting first.
          </div>
        ) : (
          <div className="ru-list">
            {withRoundups.map((m) => {
              const clickable = !!m.roundup;
              return (
                <button
                  key={m.month}
                  className={`ru-row${clickable ? "" : " ru-row-static"}`}
                  onClick={() => {
                    if (m.roundup) navigate(`/roundup/${m.roundup.id}`);
                  }}
                >
                  <div className="ru-row-month">
                    <div className="ru-row-month-name">{m.month_label}</div>
                    <div className="ru-row-month-meta">
                      {m.briefing_count > 0 ? (
                        <>
                          <span className="mono">{m.briefing_count}</span>{" "}
                          briefing{m.briefing_count === 1 ? "" : "s"}
                        </>
                      ) : (
                        "briefings removed"
                      )}
                    </div>
                  </div>
                  <div className="ru-row-committees">
                    {m.committees.map((c) => (
                      <Tag key={c}>{c}</Tag>
                    ))}
                  </div>
                  <div className="ru-row-status">
                    <StatusCell
                      m={m}
                      busy={
                        generate.isPending && generate.variables === m.month
                      }
                      onGenerate={() => generate.mutate(m.month)}
                    />
                  </div>
                  <div className="ru-row-chev">
                    {clickable && <Icon name="chev-r" size={14} />}
                  </div>
                </button>
              );
            })}
          </div>
        )}

        <div style={{ height: 64 }} />
      </div>
    </>
  );
}
