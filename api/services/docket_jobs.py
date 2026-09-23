"""Docket-job execution: claim, daemon thread, progress, cancel.

Mirror of api/services/jobs.py for FERC dockets. Two modes on one table:
  sync  — crawl eLibrary, enrich + summarize new filings, then auto-chain
          the state-of-play rollup when new summaries landed
  brief — same crawl first (a regen must see anything new on eLibrary),
          then regenerate the state-of-play unconditionally; if FERC is
          down, the regen falls back to the stored filings

Job state lives in docket_jobs (migration 014); admission is the atomic
INSERT against uq_docket_jobs_one_active."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from pipeline import db

log = logging.getLogger("poolside.docket_jobs")


def _update_job(job_id: int, **fields) -> None:
    """Patch a docket_jobs row. Only the columns named in `fields` are
    written; everything else stays put."""
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [job_id]
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(f"UPDATE docket_jobs SET {cols} WHERE id = %s", params)


class _JobCancelled(Exception):
    pass


def active_job_id(docket_id: int) -> int | None:
    """Most recent queued/running/cancelling job for this docket, if any."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT id FROM docket_jobs
                    WHERE docket_id = %s
                      AND status IN ('queued', 'running', 'cancelling')
                 ORDER BY started_at DESC
                    LIMIT 1""",
                (docket_id,),
            )
            row = cur.fetchone()
            return row["id"] if row else None


def _job_status(job_id: int) -> str | None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT status FROM docket_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return row["status"] if row else None


def get_job(job_id: int) -> dict | None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT * FROM docket_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def request_cancel(job_id: int) -> bool:
    """Flip an active job to 'cancelling'. The thread notices at its next
    progress callback (cooperative — the in-flight call finishes first)."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """UPDATE docket_jobs SET status = 'cancelling'
                    WHERE id = %s AND status IN ('queued', 'running')""",
                (job_id,),
            )
            return cur.rowcount > 0


def _run_docket_job(job_id: int, docket_id: int, mode: str) -> None:
    """Daemon-thread entry point: drive the sync and/or brief while
    streaming progress and usage back into the docket_jobs row."""
    from pipeline.docket_brief import run_docket_brief
    from pipeline.docket_ingest import sync_docket
    from pipeline.summarizer import capture_usage, totals_from_usage_log

    _update_job(job_id, status="running")

    # Progress callback: writes to DB *and* checks whether someone hit Cancel
    # since the last call (jobs.py pattern — cooperative cancellation).
    def progress(msg: str) -> None:
        try:
            _update_job(job_id, progress_text=msg)
        except Exception:
            log.exception("failed to write progress for job %s", job_id)
        if _job_status(job_id) == "cancelling":
            raise _JobCancelled()

    totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    filings_found = 0
    filings_summarized = 0
    errors: list[str] = []

    try:
        if mode not in ("sync", "brief"):
            raise ValueError(f"Unknown docket job mode: {mode}")

        # Both modes crawl eLibrary first, so a state-of-play regen never
        # builds on a stale filing list. capture_usage doesn't nest (inner
        # scope detaches the outer bucket), so the brief runs OUTSIDE the
        # capture and reports its own totals.
        usage_log: list[dict] = []
        result = None
        try:
            with capture_usage() as usage_log:
                result = sync_docket(docket_id, progress=progress)
        except _JobCancelled:
            raise
        except Exception as e:
            # A FERC outage shouldn't block an explicit regen — it can
            # still run from the stored filings. A plain sync has nothing
            # left to do without the crawl, so there it stays fatal.
            if mode == "sync":
                raise
            log.exception("docket job %s: eLibrary check failed; "
                          "regenerating from stored filings", job_id)
            errors.append(f"eLibrary check failed ({e}); state of play "
                          "regenerated from previously stored filings")
        t = totals_from_usage_log(usage_log)
        totals["input_tokens"] += int(t.get("input_tokens", 0))
        totals["output_tokens"] += int(t.get("output_tokens", 0))
        totals["cost_usd"] += float(t.get("cost_usd", 0.0))
        if result is not None:
            filings_found = result["filings_found"]
            filings_summarized = result["filings_summarized"]
            errors.extend(result["errors"])

        # sync refreshes the state of play only when new material landed;
        # brief is an explicit request, so it always regenerates.
        if mode == "brief" or filings_summarized > 0:
            progress("Updating the state of play…")
            bt = run_docket_brief(docket_id, progress=progress)
            totals["input_tokens"] += bt["input_tokens"]
            totals["output_tokens"] += bt["output_tokens"]
            totals["cost_usd"] += bt["cost_usd"]
    except _JobCancelled:
        log.info("docket job %s cancelled at user request", job_id)
        _update_job(
            job_id,
            status="cancelled",
            progress_text="Cancelled by user.",
            filings_found=filings_found,
            filings_summarized=filings_summarized,
            finished_at=datetime.now(timezone.utc),
        )
        return
    except Exception as e:
        log.exception("docket job %s failed: %s", job_id, e)
        _update_job(
            job_id,
            status="failed",
            error=str(e),
            finished_at=datetime.now(timezone.utc),
        )
        return

    _update_job(
        job_id,
        status="complete",
        progress_text="Done.",
        filings_found=filings_found,
        filings_summarized=filings_summarized,
        input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"],
        cost_usd=totals["cost_usd"],
        error=("; ".join(errors) or None),
        finished_at=datetime.now(timezone.utc),
    )


def start_docket_job(docket_id: int, mode: str = "sync",
                     created_by: str = "system") -> dict[str, Any] | None:
    """Claim the docket's active-job slot and launch the daemon thread.
    Returns {job_id, already_running, mode}, or None when the docket does
    not exist."""
    if db.get_docket(docket_id) is None:
        return None

    # Fast path; the real admission guard is the atomic INSERT below.
    existing = active_job_id(docket_id)
    if existing is not None:
        return {"job_id": existing, "already_running": True}

    # Atomic claim against uq_docket_jobs_one_active (migration 014).
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO docket_jobs (docket_id, mode, status, created_by)
                   VALUES (%s, %s, 'queued', %s)
                   ON CONFLICT (docket_id)
                       WHERE status IN ('queued', 'running', 'cancelling')
                       DO NOTHING
                RETURNING id""",
                (docket_id, mode, created_by),
            )
            row_claimed = cur.fetchone()
    if row_claimed is None:
        existing = active_job_id(docket_id)
        return {"job_id": existing, "already_running": True}
    job_id = row_claimed["id"]

    t = threading.Thread(
        target=_run_docket_job,
        args=(job_id, docket_id, mode),
        name=f"docket-job-{job_id}",
        daemon=True,
    )
    t.start()

    return {"job_id": job_id, "already_running": False, "mode": mode}
