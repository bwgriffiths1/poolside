"""Summarize-job execution: claim, daemon thread, progress, cancel.

Shared by POST /api/meetings/{id}/summarize and the orchestrator's
auto-resummarize path. Job state lives in the summarize_jobs table."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from pipeline import db

from .. import lifecycle

log = logging.getLogger("poolside.jobs")


def _update_job(job_id: int, **fields) -> None:
    """Patch a summarize_jobs row. Only the columns named in `fields` are
    written; everything else stays put."""
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [job_id]
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(f"UPDATE summarize_jobs SET {cols} WHERE id = %s", params)


class _JobCancelled(Exception):
    pass


def _active_job_id(meeting_id: int) -> int | None:
    """Most recent queued/running/cancelling job for this meeting, if any."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT id FROM summarize_jobs
                    WHERE meeting_id = %s
                      AND status IN ('queued', 'running', 'cancelling')
                 ORDER BY started_at DESC
                    LIMIT 1""",
                (meeting_id,),
            )
            row = cur.fetchone()
            return row["id"] if row else None


def _job_status(job_id: int) -> str | None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT status FROM summarize_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return row["status"] if row else None


def _run_summarize_job(
    job_id: int,
    meeting_id: int,
    committee_short: str,
    venue_short: str,
    start_level: int = 1,
    force_rerun: bool = True,
    item_ids: set[int] | None = None,
) -> None:
    """Daemon-thread entry point: drive run_meeting_summarization while
    streaming progress and usage back into the summarize_jobs row.

    `start_level` and `force_rerun` come from the summarize mode chosen by
    the caller (see `StartSummarizeBody`). Defaults match the pre-mode
    behavior — a full re-run from Level 1.
    """
    from pipeline.summarizer import (
        capture_usage,
        make_client,
        run_meeting_summarization,
        totals_from_usage_log,
    )

    _update_job(job_id, status="running")

    # Progress callback: writes to DB *and* checks whether someone hit Cancel
    # since the last call. If so we raise _JobCancelled, which the outer try
    # catches to mark the row 'cancelled'. This is cooperative — the in-flight
    # LLM call still has to finish before the cancel takes effect.
    def progress(msg: str) -> None:
        try:
            _update_job(job_id, progress_text=msg)
        except Exception:
            log.exception("failed to write progress for job %s", job_id)
        if _job_status(job_id) == "cancelling":
            raise _JobCancelled()

    try:
        client = make_client()
        with capture_usage() as usage_log:
            result = run_meeting_summarization(
                meeting_id=meeting_id,
                client=client,
                committee_short=committee_short,
                venue_short=venue_short,
                progress_fn=progress,
                start_level=start_level,
                force_rerun=force_rerun,
                item_ids=item_ids,
            )
        totals = totals_from_usage_log(usage_log)
    except _JobCancelled:
        log.info("summarize job %s cancelled at user request", job_id)
        _update_job(
            job_id,
            status="cancelled",
            progress_text="Cancelled by user.",
            finished_at=datetime.now(timezone.utc),
        )
        return
    except Exception as e:
        log.exception("summarize job %s failed: %s", job_id, e)
        _update_job(
            job_id,
            status="failed",
            error=str(e),
            finished_at=datetime.now(timezone.utc),
        )
        return

    try:
        lifecycle.bump_lifecycle(meeting_id)
    except Exception:
        pass

    _update_job(
        job_id,
        status="complete",
        progress_text="Done.",
        level1_done=int(result.get("level1", 0)),
        level2_done=int(result.get("level2", 0)),
        level3_done=bool(result.get("level3", False)),
        input_tokens=int(totals.get("input_tokens", 0)),
        output_tokens=int(totals.get("output_tokens", 0)),
        cost_usd=float(totals.get("cost_usd", 0.0)),
        error=("; ".join(result.get("errors", []) or []) or None),
        finished_at=datetime.now(timezone.utc),
    )


def start_summarize_job(
    meeting_id: int,
    mode: str = "all",
    item_ids: set[int] | None = None,
    created_by: str = "system",
) -> dict[str, Any] | None:
    """Claim the meeting's active-job slot and launch the daemon thread.

    Shared by POST /summarize and the orchestrator's auto-resummarize path.
    Returns the route-shaped dict ({job_id, already_running, ...}), or None
    when the meeting does not exist. item_ids restricts Level 1/2 to the
    affected agenda items; Level 3 always regenerates when it is set.
    """
    from pipeline.summarizer import estimate_summarization_cost

    row = db.get_meeting(meeting_id)
    if row is None:
        return None

    # Fast path: if a job is already in flight, skip the estimate work and
    # return its id. The real admission guard is the atomic INSERT below —
    # this check alone would be a check-then-insert race.
    existing = _active_job_id(meeting_id)
    if existing is not None:
        return {"job_id": existing, "already_running": True}

    # Compute estimate; safe to fail silently — cost is best-effort. For
    # item-scoped runs the estimator has no scoped mode, so a whole-meeting
    # figure would mislead — store no estimate instead.
    est: dict[str, Any] = {
        "estimated_input_tokens": None,
        "estimated_output_tokens": None,
        "estimated_cost_usd": None,
    }
    if item_ids is None:
        try:
            est = estimate_summarization_cost(meeting_id, mode=mode)
        except Exception:
            log.exception("pre-flight estimate failed for %s", meeting_id)

    # Atomic claim against uq_summarize_jobs_one_active (migration 010):
    # if another request won the race, no row is inserted and we report the
    # winner's job instead of starting a second thread.
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO summarize_jobs
                       (meeting_id, status, estimated_input_tokens,
                        estimated_output_tokens, estimated_cost_usd, created_by)
                   VALUES (%s, 'queued', %s, %s, %s, %s)
                   ON CONFLICT (meeting_id)
                       WHERE status IN ('queued', 'running', 'cancelling')
                       DO NOTHING
                RETURNING id""",
                (
                    meeting_id,
                    est.get("estimated_input_tokens"),
                    est.get("estimated_output_tokens"),
                    est.get("estimated_cost_usd"),
                    created_by,
                ),
            )
            row_claimed = cur.fetchone()
    if row_claimed is None:
        existing = _active_job_id(meeting_id)
        return {"job_id": existing, "already_running": True}
    job_id = row_claimed["id"]

    venue_short = row.get("venue_short") or "ISO-NE"
    committee_short = row.get("type_short") or "MC"

    # Translate mode to the run_meeting_summarization params.
    # "all"      → start_level=1, force_rerun=True   (full re-run)
    # "missing"  → start_level=1, force_rerun=False  (skip items with summaries)
    # "briefing" → start_level=3, force_rerun=True   (briefing only)
    mode_to_args = {
        "all":      (1, True),
        "missing":  (1, False),
        "briefing": (3, True),
    }
    start_level, force_rerun = mode_to_args[mode]

    t = threading.Thread(
        target=_run_summarize_job,
        args=(job_id, meeting_id, committee_short, venue_short,
              start_level, force_rerun, item_ids),
        name=f"summarize-job-{job_id}",
        daemon=True,
    )
    t.start()

    return {
        "job_id": job_id,
        "already_running": False,
        "mode": mode,
        "estimated_cost_usd": est.get("estimated_cost_usd"),
        "estimated_input_tokens": est.get("estimated_input_tokens"),
        "estimated_output_tokens": est.get("estimated_output_tokens"),
    }
