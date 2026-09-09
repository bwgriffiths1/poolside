"""Ask-job execution: claim, daemon thread, progress, cancel.

Same shape as docket_jobs: the route inserts a queued row and returns at
once; a daemon thread runs the Ask pipeline (api.routes.ask.run_ask) and
records the outcome; the page polls get_job. Cancellation is cooperative
— checked before the model call, which is the only slow step.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from pipeline import db

log = logging.getLogger("poolside.ask_jobs")

TERMINAL = ("complete", "failed", "cancelled")


def _update_job(job_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [job_id]
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(f"UPDATE ask_jobs SET {cols} WHERE id = %s", params)


def _job_status(job_id: int) -> str | None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT status FROM ask_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return row["status"] if row else None


def get_job(job_id: int) -> dict | None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT * FROM ask_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def active_jobs(user_email: str) -> list[dict]:
    """The caller's non-terminal jobs, oldest first — for page reload."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT * FROM ask_jobs
                    WHERE user_email = %s
                      AND status IN ('queued', 'running', 'cancelling')
                    ORDER BY id""",
                (user_email,),
            )
            return [dict(r) for r in cur.fetchall()]


def request_cancel(job_id: int) -> bool:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """UPDATE ask_jobs SET status = 'cancelling'
                    WHERE id = %s AND status IN ('queued', 'running')""",
                (job_id,),
            )
            return cur.rowcount > 0


class _JobCancelled(Exception):
    pass


def _run_ask_job(job_id: int, runner: Callable[..., dict[str, Any]],
                 body: Any, user: dict) -> None:
    """Thread body. `runner` is api.routes.ask.run_ask (injected so tests
    can stub it and so this module doesn't import the route module)."""
    _update_job(job_id, status="running", progress_text="Gathering sources…")

    def progress(text: str) -> None:
        if _job_status(job_id) == "cancelling":
            raise _JobCancelled()
        _update_job(job_id, progress_text=text)

    try:
        result = runner(body, user, progress=progress)
    except _JobCancelled:
        _update_job(job_id, status="cancelled", progress_text="Cancelled",
                    finished_at=datetime.now(timezone.utc))
        return
    except Exception as exc:  # noqa: BLE001 — surfaced on the job row
        log.exception("ask job %s failed", job_id)
        _update_job(job_id, status="failed", error=str(exc)[:2000],
                    progress_text="Failed",
                    finished_at=datetime.now(timezone.utc))
        return
    _update_job(job_id, status="complete", progress_text="Done",
                ask_log_id=result.get("id"),
                finished_at=datetime.now(timezone.utc))


def start_ask_job(body: Any, user: dict,
                  runner: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Insert the queued row and launch the thread. Returns {job_id}."""
    request = body.model_dump(mode="json") if hasattr(body, "model_dump") else dict(body)
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO ask_jobs (user_id, user_email, request, status)
                   VALUES (%s, %s, %s::jsonb, 'queued') RETURNING id""",
                (user.get("id"), user.get("email") or "", json.dumps(request)),
            )
            job_id = cur.fetchone()["id"]
    t = threading.Thread(target=_run_ask_job, args=(job_id, runner, body, user),
                         name=f"ask-job-{job_id}", daemon=True)
    t.start()
    return {"job_id": job_id}
