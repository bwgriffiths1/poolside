import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DocketJob } from "../lib/api";
import { qk } from "../lib/queries";
import { toast } from "../lib/toast";

function isTerminal(status: string | undefined): boolean {
  return status === "complete" || status === "failed" || status === "cancelled";
}

/** The docket-job state machine (useSummarizeJob's shape): recovers an
 *  in-flight sync/brief job on mount, polls it while active, refreshes
 *  docket data + toasts once when it reaches a terminal state, and exposes
 *  startSync / startBrief / cancel / dismiss. */
export function useDocketJob(docketId: number) {
  const qc = useQueryClient();
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  // Jobs whose terminal state we've already handled — so invalidation +
  // completion toast fire exactly once per job.
  const settledRef = useRef<Set<number>>(new Set());

  // Recover any in-flight job for this docket on mount.
  useEffect(() => {
    let cancelled = false;
    setActiveJobId(null);
    api
      .getDocketActiveJob(docketId)
      .then((j) => {
        if (!cancelled && j && !isTerminal(j.status)) setActiveJobId(j.id);
      })
      .catch(() => {
        /* no-op — no recoverable job */
      });
    return () => {
      cancelled = true;
    };
  }, [docketId]);

  const jobQuery = useQuery({
    queryKey: qk.docketJob(activeJobId),
    queryFn: () => api.getDocketJob(activeJobId as number),
    enabled: activeJobId != null,
    refetchInterval: (q) => {
      const data = q.state.data as DocketJob | undefined;
      if (!data) return 3000;
      return isTerminal(data.status) ? false : 3000;
    },
    // A crawl takes minutes (FERC's API runs 15-60s per call) — keep
    // polling while the tab is backgrounded so completion lands promptly.
    refetchIntervalInBackground: true,
  });
  const job = activeJobId != null ? jobQuery.data : undefined;

  // When the polled job hits a terminal state, refresh data + toast once.
  useEffect(() => {
    if (!job || !isTerminal(job.status)) return;
    if (settledRef.current.has(job.id)) return;
    settledRef.current.add(job.id);
    qc.invalidateQueries({ queryKey: qk.docket(docketId) });
    qc.invalidateQueries({ queryKey: qk.dockets });
    if (job.status === "complete") {
      const what =
        job.mode === "brief"
          ? "State of play updated."
          : `Sync complete — ${job.filings_found} new filing(s), ` +
            `${job.filings_summarized} summarized.`;
      const cost =
        job.cost_usd != null && job.cost_usd > 0
          ? `\nCost $${Number(job.cost_usd).toFixed(4)} · ` +
            `${job.input_tokens.toLocaleString()} in / ` +
            `${job.output_tokens.toLocaleString()} out tokens`
          : "";
      toast.success(`${what}${cost}`, 12_000);
    }
  }, [job, qc, docketId]);

  const startSync = useMutation({
    mutationFn: () => api.syncDocket(docketId),
    onSuccess: (res) => setActiveJobId(res.job_id),
    onError: (e: Error) => toast.error(`Could not start sync: ${e.message}`),
  });

  const startBrief = useMutation({
    mutationFn: () => api.generateStateOfPlay(docketId),
    onSuccess: (res) => setActiveJobId(res.job_id),
    onError: (e: Error) =>
      toast.error(`Could not start state of play: ${e.message}`),
  });

  const cancel = useMutation({
    mutationFn: (jobId: number) => api.cancelDocketJob(jobId),
    onError: (e: Error) => toast.error(`Cancel failed: ${e.message}`),
  });

  return {
    /** The tracked job — while queued/running, and after it settles (until dismissed). */
    job,
    /** Adopt a job started elsewhere (e.g. the add-docket flow). */
    track: (jobId: number) => setActiveJobId(jobId),
    startSync: () => startSync.mutate(),
    isStartingSync: startSync.isPending,
    startBrief: () => startBrief.mutate(),
    isStartingBrief: startBrief.isPending,
    cancel: (jobId: number) => cancel.mutate(jobId),
    isCancelling: cancel.isPending,
    dismiss: () => setActiveJobId(null),
  };
}
