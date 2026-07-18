import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SummarizeJob, type SummarizeMode } from "../lib/api";
import { qk } from "../lib/queries";
import { toast } from "../lib/toast";

function isTerminal(status: string | undefined): boolean {
  return status === "complete" || status === "failed" || status === "cancelled";
}

/** The meeting summarize-job state machine: recovers an in-flight job on
 *  mount, polls it while active, refreshes meeting data + toasts once when
 *  it reaches a terminal state, and exposes start / cancel / dismiss. */
export function useSummarizeJob(
  meetingId: number,
  opts?: { onStarted?: () => void },
) {
  const qc = useQueryClient();
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  // Jobs whose terminal state we've already handled — so invalidation +
  // completion toast fire exactly once per job.
  const settledRef = useRef<Set<number>>(new Set());

  // Recover any in-flight job for this meeting on mount.
  useEffect(() => {
    let cancelled = false;
    setActiveJobId(null);
    api
      .getActiveJob(meetingId)
      .then((j) => {
        if (!cancelled && j && !isTerminal(j.status)) setActiveJobId(j.id);
      })
      .catch(() => {
        /* no-op — no recoverable job */
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId]);

  const jobQuery = useQuery({
    queryKey: qk.job(activeJobId),
    queryFn: () => api.getJob(activeJobId as number),
    enabled: activeJobId != null,
    refetchInterval: (q) => {
      const data = q.state.data as SummarizeJob | undefined;
      if (!data) return 3000;
      return isTerminal(data.status) ? false : 3000;
    },
    // A summarize run takes minutes — keep polling while the tab is
    // backgrounded (same reasoning as the roundup pollers) so completion
    // lands the moment it happens, not on the next focus.
    refetchIntervalInBackground: true,
  });
  const job = activeJobId != null ? jobQuery.data : undefined;

  // When the polled job hits a terminal state, refresh data + toast once.
  useEffect(() => {
    if (!job || !isTerminal(job.status)) return;
    if (settledRef.current.has(job.id)) return;
    settledRef.current.add(job.id);
    qc.invalidateQueries({ queryKey: qk.meeting(meetingId) });
    qc.invalidateQueries({ queryKey: qk.briefing(meetingId) });
    qc.invalidateQueries({ queryKey: qk.meetingDocs(meetingId) });
    qc.invalidateQueries({ queryKey: qk.meetings });
    if (job.status === "complete") {
      toast.success(
        `Summarization complete.\n` +
          `Actual cost $${job.cost_usd.toFixed(4)}.\n` +
          `Input tokens: ${job.input_tokens.toLocaleString()}\n` +
          `Output tokens: ${job.output_tokens.toLocaleString()}`,
        12_000,
      );
    }
  }, [job, qc, meetingId]);

  const start = useMutation({
    mutationFn: (mode: SummarizeMode) => api.startSummarize(meetingId, mode),
    onSuccess: (res) => {
      setActiveJobId(res.job_id);
      opts?.onStarted?.();
    },
    onError: (e: Error) => toast.error(`Could not start summarize: ${e.message}`),
  });

  const cancel = useMutation({
    mutationFn: (jobId: number) => api.cancelJob(jobId),
    onError: (e: Error) => toast.error(`Cancel failed: ${e.message}`),
  });

  return {
    /** The tracked job — while queued/running, and after it settles (until dismissed). */
    job,
    start: (mode: SummarizeMode) => start.mutate(mode),
    isStarting: start.isPending,
    cancel: (jobId: number) => cancel.mutate(jobId),
    isCancelling: cancel.isPending,
    dismiss: () => setActiveJobId(null),
  };
}
