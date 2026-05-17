"""Background-summarization job endpoints.

The job rows are written by the daemon thread in api/routes/meetings.py.
These routes are read-only: poll a job by id, or look up the active job
(if any) for a meeting.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pipeline import db_new as db
from ..auth import current_user

router = APIRouter(prefix="/api", tags=["jobs"])


def _serialize_job(row: dict) -> dict[str, Any]:
    """Convert a summarize_jobs row to a JSON-safe dict."""
    if not row:
        return {}
    out = dict(row)
    # Postgres NUMERIC / TIMESTAMPTZ → strings the JSON encoder handles natively
    for k in ("cost_usd", "estimated_cost_usd"):
        if out.get(k) is not None:
            out[k] = float(out[k])
    for k in ("started_at", "finished_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat() if hasattr(out[k], "isoformat") else str(out[k])
    return out


@router.get("/jobs/{job_id}")
def get_job(job_id: int, _: dict = Depends(current_user)) -> dict[str, Any]:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT * FROM summarize_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(dict(row))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Request that a running summarize job stop.

    Cooperative: the daemon thread polls the row's status between progress
    callbacks. The current in-flight LLM call has to finish before the
    cancel takes effect, so expect a delay of seconds to tens of seconds.
    """
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT status FROM summarize_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            cur_status = row["status"]
            if cur_status in ("complete", "failed", "cancelled"):
                return {"job_id": job_id, "status": cur_status, "changed": False}
            cur.execute(
                """UPDATE summarize_jobs
                       SET status = 'cancelling'
                     WHERE id = %s
                       AND status IN ('queued', 'running')""",
                (job_id,),
            )
            changed = bool(cur.rowcount)
    return {
        "job_id": job_id,
        "status": "cancelling" if changed else cur_status,
        "changed": changed,
    }


@router.get("/meetings/{meeting_id}/active-job")
def get_active_job(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> dict[str, Any] | None:
    """Return the most recent queued/running/cancelling job for this meeting, or null."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT * FROM summarize_jobs
                    WHERE meeting_id = %s
                      AND status IN ('queued', 'running', 'cancelling')
                 ORDER BY started_at DESC
                    LIMIT 1""",
                (meeting_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return _serialize_job(dict(row))
