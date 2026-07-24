import type { SummarizeJob } from "../../lib/api";

export function SummarizeJobBanner({
  job,
  onCancel,
  cancelling,
  onDismiss,
}: {
  job: SummarizeJob;
  /** Omitted for read-only roles — the status banner shows without a Cancel control. */
  onCancel?: () => void;
  cancelling: boolean;
  onDismiss: () => void;
}) {
  if (job.status === "complete") return null;
  return (
    <div className={`summary-banner ${job.status === "failed" ? "is-error" : ""}`}>
      <div className="summary-banner-main">
        <div className="summary-banner-title">
          {job.status === "running" && "Summarizing meeting…"}
          {job.status === "queued" && "Queued…"}
          {job.status === "cancelling" && "Cancelling…"}
          {job.status === "cancelled" && "Cancelled"}
          {job.status === "failed" && "Summarization failed"}
        </div>
        <div className="summary-banner-sub">
          {job.status === "failed"
            ? job.error || "Unknown error."
            : job.status === "cancelling"
            ? "Waiting for the current step to finish, then stopping."
            : job.progress_text || "Working…"}
        </div>
      </div>
      {(job.status === "queued" || job.status === "running") && onCancel && (
        <button
          className="btn btn-sm btn-ghost"
          disabled={cancelling}
          onClick={onCancel}
        >
          {cancelling ? "Cancelling…" : "Cancel"}
        </button>
      )}
      {(job.status === "failed" || job.status === "cancelled") && (
        <button className="btn btn-sm btn-ghost" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
}
