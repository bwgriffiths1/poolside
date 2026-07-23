import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { Segmented } from "../Segmented";
import { api, type SummarizeMode } from "../../lib/api";
import { qk, useCan } from "../../lib/queries";
import { toast } from "../../lib/toast";
import type { AgendaItem } from "../../types";

/** The "Summarize this meeting" modal: mode selector, live cost estimate,
 *  and the refresh-materials / start buttons. Mounted only while open, so the
 *  smart mode default is computed exactly when the user opens it. */
export function SummarizeRunner({
  meetingId,
  agenda,
  hasBriefing,
  onClose,
  onStart,
  isStarting,
}: {
  meetingId: number;
  agenda: AgendaItem[];
  hasBriefing: boolean;
  onClose: () => void;
  onStart: (mode: SummarizeMode) => void;
  isStarting: boolean;
}) {
  const qc = useQueryClient();
  const { canEdit } = useCan();
  const withSummary = agenda.filter((i) => i.has_summary).length;

  // Meetings with any existing summary work default to "missing" — the
  // cheap-and-cheerful gap-fill option — while fresh meetings get "all".
  const [mode, setMode] = useState<SummarizeMode>(() =>
    withSummary > 0 || hasBriefing ? "missing" : "all",
  );

  // Re-fetches when the user flips the mode selector so the displayed cost
  // reflects what they're about to run.
  const estimate = useQuery({
    queryKey: qk.summarizeEstimate(meetingId, mode),
    queryFn: () => api.estimateSummarize(meetingId, mode),
    staleTime: 60_000,
  });

  const refreshMeeting = useMutation({
    mutationFn: () => api.refreshMeeting(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
      qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
    },
    onError: (e: Error) => toast.error(`Refresh failed: ${e.message}`),
  });

  // Read-only roles get no run/refresh controls (job status lives in
  // SummarizeJobBanner, rendered by Meeting.tsx, so viewers still see it).
  if (!canEdit) return null;

  return (
    <div className="summary-runner">
      <div className="row" style={{ marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>
          Summarize this meeting
        </h3>
        <span style={{ flex: 1 }} />
        <button
          className="btn btn-sm btn-ghost"
          onClick={onClose}
          disabled={isStarting || refreshMeeting.isPending}
        >
          <Icon name="x" size={12} />
        </button>
      </div>
      <div style={{ marginBottom: 14 }}>
        <Segmented<SummarizeMode>
          value={mode}
          onChange={setMode}
          options={[
            { value: "all", label: "Summarize all" },
            {
              value: "missing",
              label: "Summarize missing",
              disabled: withSummary === 0 && !hasBriefing,
            },
            {
              value: "briefing",
              label: "Briefing only",
              disabled: withSummary === 0,
            },
          ]}
        />
      </div>

      <div
        className="text-sm muted"
        style={{ marginBottom: 14, lineHeight: 1.5 }}
      >
        {mode === "all" && (
          <>
            Runs the full three-level pipeline: summarize each document,
            roll up per agenda item, then write the meeting briefing.
            Existing summaries are overwritten.
          </>
        )}
        {mode === "missing" && (
          <>
            Only items that don't already have a summary will be
            processed. The meeting briefing is then regenerated if any
            new item summaries were produced.
          </>
        )}
        {mode === "briefing" && (
          <>
            Reuses existing item-level summaries and regenerates only the
            top-line meeting briefing — useful after editing the briefing
            prompt or tweaking item summaries.
          </>
        )}
        {" "}The job runs in the background — you can close this modal
        and come back.
      </div>

      <div
        style={{
          background: "var(--bg-sunk)",
          border: "1px solid var(--border-soft)",
          borderRadius: "var(--radius)",
          padding: "10px 12px",
          marginBottom: 14,
        }}
      >
        {estimate.isLoading && (
          <div className="text-sm muted">Loading estimate…</div>
        )}
        {estimate.isError && (
          <div className="text-sm" style={{ color: "var(--accent)" }}>
            Couldn't load estimate: {(estimate.error as Error).message}
          </div>
        )}
        {estimate.data && (
          <>
            <div style={{ fontSize: 14 }}>
              <span className="muted">Est. cost </span>
              <strong>
                ≈ ${estimate.data.estimated_cost_usd.toFixed(4)}
              </strong>
            </div>
            <div className="muted text-xs" style={{ marginTop: 4 }}>
              ~{estimate.data.estimated_input_tokens.toLocaleString()} input
              tokens · ~
              {estimate.data.estimated_output_tokens.toLocaleString()}{" "}
              output tokens · {estimate.data.items_planned} LLM call(s)
            </div>
            {estimate.data.docs_without_text > 0 && (
              <div className="muted text-xs" style={{ marginTop: 4 }}>
                Note: {estimate.data.docs_without_text} document(s)
                haven't been text-extracted yet, so the estimate is rough
                — actuals may differ.
              </div>
            )}
            {estimate.data.committee_stats &&
              estimate.data.committee_stats.count > 0 && (
                <div
                  className="muted text-xs"
                  style={{
                    marginTop: 8,
                    paddingTop: 8,
                    borderTop: "1px solid var(--border-soft)",
                  }}
                >
                  Typical for this committee: $
                  {estimate.data.committee_stats.avg_cost_usd.toFixed(4)}{" "}
                  · ~
                  {Math.max(
                    1,
                    Math.round(
                      estimate.data.committee_stats.avg_duration_seconds / 60,
                    ),
                  )}{" "}
                  min ({estimate.data.committee_stats.count} prior run
                  {estimate.data.committee_stats.count === 1 ? "" : "s"})
                </div>
              )}
          </>
        )}
      </div>

      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button
          className="btn btn-sm"
          disabled={refreshMeeting.isPending || isStarting}
          onClick={() => refreshMeeting.mutate()}
          title="Pull latest documents and re-parse the agenda; does NOT call the LLM."
        >
          <Icon name="refresh" size={11} />{" "}
          {refreshMeeting.isPending
            ? "Refreshing…"
            : "Refresh materials only"}
        </button>
        <span style={{ flex: 1 }} />
        <button
          className="btn btn-sm btn-accent"
          disabled={isStarting || refreshMeeting.isPending}
          onClick={() => onStart(mode)}
        >
          <Icon name="spark" size={11} />{" "}
          {isStarting
            ? "Starting…"
            : mode === "missing"
            ? "Summarize missing items"
            : mode === "briefing"
            ? "Regenerate briefing"
            : "Summarize all"}
        </button>
      </div>
    </div>
  );
}
