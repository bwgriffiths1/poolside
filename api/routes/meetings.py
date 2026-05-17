"""Meetings endpoints — wire pipeline/db_new.py to the frontend contract."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from pipeline import db_new as db
from .. import adapters, lifecycle, schemas
from ..auth import current_user

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
log = logging.getLogger("poolside.meetings")


@router.get("", response_model=list[schemas.MeetingListItem])
def list_meetings(
    past_days: int = Query(730, ge=0, le=3650),
    future_days: int = Query(365, ge=0, le=3650),
    venue: str | None = Query(None),
) -> list[schemas.MeetingListItem]:
    rows = db.list_meetings_overview(
        venue_short=venue, past_days=past_days, future_days=future_days
    )
    out: list[schemas.MeetingListItem] = []
    for row in rows:
        # tags: pull from tag_links via meeting
        try:
            tag_rows = db.get_tags_for_entity("meeting", row["id"])
            tags = [t["name"] for t in tag_rows]
        except Exception:
            tags = []
        # item_count: rough count of agenda rows
        try:
            agenda_rows = db.get_agenda_items(row["id"])
            item_count = len(agenda_rows)
        except Exception:
            item_count = 0
        out.append(adapters.meeting_list_row(row, tags=tags, item_count=item_count))
    return out


@router.get("/{meeting_id}", response_model=schemas.MeetingDetail)
def get_meeting(meeting_id: int) -> schemas.MeetingDetail:
    row = db.get_meeting(meeting_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Idempotently materialize any missing parent items (e.g. "7" when only
    # 7.a–7.l exist) into real DB rows so they can be edited.
    adapters.materialize_missing_parents(meeting_id)

    # Backfill fields list_meetings_overview gives us but get_meeting may not.
    docs = db.get_documents_for_meeting(meeting_id)
    has_summary = bool(db.get_current_summary("meeting", meeting_id))
    enriched = {
        **row,
        "venue_short": row.get("venue_short") or row.get("venue"),
        "type_short": row.get("type_short"),
        "type_name": row.get("type_name"),
        "doc_count": len(docs),
        "has_summary": has_summary,
        "has_manual": False,  # TODO: derive from summary_versions.is_manual
    }
    try:
        tags = [t["name"] for t in db.get_tags_for_entity("meeting", meeting_id)]
    except Exception:
        tags = []
    agenda_rows = db.get_agenda_items(meeting_id)
    item_count = len(agenda_rows)
    base = adapters.meeting_list_row(enriched, tags=tags, item_count=item_count)

    summary = db.get_current_summary("meeting", meeting_id) or {}

    agenda_items: list[schemas.AgendaItem] = []
    for ar in agenda_rows:
        doc_rows = db.get_documents_for_item(ar["id"])
        item_docs = [adapters.document_row(d) for d in doc_rows]
        item_summary = db.get_current_summary("agenda_item", ar["id"])
        agenda_items.append(adapters.agenda_item_row(ar, item_docs, item_summary))

    return schemas.MeetingDetail(
        **base.model_dump(),
        one_line=summary.get("one_line", "") or "",
        agenda=adapters.synthesize_missing_parents(agenda_items),
    )


@router.delete("/{meeting_id}")
def delete_meeting(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Hard-delete a meeting and everything that hangs off it.

    `ON DELETE CASCADE` on the documents / agenda_items / summary_versions /
    summarize_jobs / share_tokens / etc. foreign keys means we only have to
    DELETE one row. The agenda + docs + summaries + jobs go with it.
    """
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("DELETE FROM meetings WHERE id = %s", (meeting_id,))
    return {"deleted": True, "meeting_id": meeting_id}


@router.delete("/{meeting_id}/documents")
def delete_all_documents(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Wipe every document row for this meeting. Use when the scraper
    pulled garbage or you want to re-discover materials from scratch.
    Cascades remove item_documents and document_images rows.
    """
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "DELETE FROM documents WHERE meeting_id = %s", (meeting_id,)
            )
            removed = cur.rowcount or 0
    return {"removed_documents": int(removed)}


@router.get("/{meeting_id}/agenda", response_model=list[schemas.AgendaItem])
def get_meeting_agenda(meeting_id: int) -> list[schemas.AgendaItem]:
    items: list[schemas.AgendaItem] = []
    for ar in db.get_agenda_items(meeting_id):
        doc_rows = db.get_documents_for_item(ar["id"])
        docs = [adapters.document_row(d) for d in doc_rows]
        summary = db.get_current_summary("agenda_item", ar["id"])
        items.append(adapters.agenda_item_row(ar, docs, summary))
    return adapters.synthesize_missing_parents(items)


@router.get("/{meeting_id}/summarize/estimate")
def estimate_meeting_summarize(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Pre-flight cost estimate for the meeting summarize pipeline.

    Heuristic — see pipeline.summarizer.estimate_summarization_cost. Returns
    approximate input/output token counts and USD cost, plus a per-level
    breakdown. Also returns `committee_stats` summarizing past completed
    summarize_jobs for meetings in the same committee, so the UI can show
    "typical cost / typical duration" alongside the estimate.
    """
    from pipeline.summarizer import estimate_summarization_cost

    row = db.get_meeting(meeting_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    try:
        estimate = estimate_summarization_cost(meeting_id)
    except Exception as e:
        log.exception("estimate failed for %s: %s", meeting_id, e)
        raise HTTPException(status_code=500, detail=str(e))

    committee_short = row.get("type_short")
    venue_short = row.get("venue_short")
    estimate["committee_stats"] = _committee_summarize_stats(
        committee_short, venue_short
    )
    return estimate


def _committee_summarize_stats(
    committee_short: str | None, venue_short: str | None
) -> dict[str, Any] | None:
    """Look up completed summarize_jobs across meetings in the same
    venue+committee. Returns avg cost + avg duration (sec) + count, or None
    when no prior runs exist."""
    if not committee_short or not venue_short:
        return None
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS n,
                    AVG(sj.cost_usd)::float AS avg_cost_usd,
                    AVG(EXTRACT(EPOCH FROM (sj.finished_at - sj.started_at)))::float
                        AS avg_duration_seconds
                FROM summarize_jobs sj
                JOIN meetings m       ON m.id  = sj.meeting_id
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v         ON v.id  = mt.venue_id
                WHERE sj.status = 'complete'
                  AND sj.finished_at IS NOT NULL
                  AND mt.short_name = %s
                  AND v.short_name  = %s
                """,
                (committee_short, venue_short),
            )
            row = cur.fetchone()
    if not row or not row["n"]:
        return None
    return {
        "count": int(row["n"]),
        "avg_cost_usd": float(row["avg_cost_usd"] or 0),
        "avg_duration_seconds": float(row["avg_duration_seconds"] or 0),
    }


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


def _job_status(job_id: int) -> str | None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT status FROM summarize_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return row["status"] if row else None


def _run_summarize_job(job_id: int, meeting_id: int, committee_short: str, venue_short: str) -> None:
    """Daemon-thread entry point: drive run_meeting_summarization while
    streaming progress and usage back into the summarize_jobs row."""
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


@router.post("/{meeting_id}/summarize", status_code=202)
def start_summarize(
    meeting_id: int,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Kick off a background summarize job for this meeting.

    Returns the job_id and the pre-flight estimate. The actual run happens
    in a daemon thread; poll GET /api/jobs/{job_id} for progress.
    """
    from pipeline.summarizer import estimate_summarization_cost

    row = db.get_meeting(meeting_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Refuse to stack jobs: if one is already in flight, return its id.
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT id FROM summarize_jobs
                    WHERE meeting_id = %s
                      AND status IN ('queued', 'running')
                 ORDER BY started_at DESC
                    LIMIT 1""",
                (meeting_id,),
            )
            existing = cur.fetchone()
    if existing:
        return {"job_id": existing["id"], "already_running": True}

    # Compute estimate; safe to fail silently — cost is best-effort.
    try:
        est = estimate_summarization_cost(meeting_id)
    except Exception:
        log.exception("pre-flight estimate failed for %s", meeting_id)
        est = {
            "estimated_input_tokens": None,
            "estimated_output_tokens": None,
            "estimated_cost_usd": None,
        }

    created_by = (user.get("email") if isinstance(user, dict) else None) or "unknown"

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO summarize_jobs
                       (meeting_id, status, estimated_input_tokens,
                        estimated_output_tokens, estimated_cost_usd, created_by)
                   VALUES (%s, 'queued', %s, %s, %s, %s)
                RETURNING id""",
                (
                    meeting_id,
                    est.get("estimated_input_tokens"),
                    est.get("estimated_output_tokens"),
                    est.get("estimated_cost_usd"),
                    created_by,
                ),
            )
            job_id = cur.fetchone()["id"]

    venue_short = row.get("venue_short") or "ISO-NE"
    committee_short = row.get("type_short") or "MC"

    t = threading.Thread(
        target=_run_summarize_job,
        args=(job_id, meeting_id, committee_short, venue_short),
        name=f"summarize-job-{job_id}",
        daemon=True,
    )
    t.start()

    return {
        "job_id": job_id,
        "already_running": False,
        "estimated_cost_usd": est.get("estimated_cost_usd"),
        "estimated_input_tokens": est.get("estimated_input_tokens"),
        "estimated_output_tokens": est.get("estimated_output_tokens"),
    }
