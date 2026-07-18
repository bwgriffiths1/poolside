"""Meetings endpoints — wire pipeline/db.py to the frontend contract."""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from pipeline import db
from .. import adapters, schemas
from ..services import jobs as jobs_service
from ..auth import current_user


class StartSummarizeBody(BaseModel):
    """POST body for /meetings/{id}/summarize.

    mode:
      * "all"      — full re-run (Level 1 + 2 + 3), regenerates everything
      * "missing"  — only items without an existing summary; briefing rebuilds
                     if any new item summaries were produced
      * "briefing" — reuse existing item summaries, regenerate only the
                     meeting briefing
    """
    mode: Literal["all", "missing", "briefing"] = "all"

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
    # One batched tags query instead of two extra queries per meeting —
    # this endpoint used to make ~2N pool checkouts per dashboard load.
    # item_count comes from list_meetings_overview's SQL (meeting_list_row
    # falls back to row["item_count"] when item_count isn't passed).
    tags_by_meeting: dict[int, list[str]] = {}
    ids = [row["id"] for row in rows]
    if ids:
        try:
            with db._conn() as conn:
                with db._cursor(conn) as cur:
                    cur.execute(
                        """SELECT et.entity_id, t.name
                             FROM tags t
                             JOIN entity_tags et ON et.tag_id = t.id
                            WHERE et.entity_type = 'meeting'
                              AND et.entity_id = ANY(%s)
                         ORDER BY t.tag_type, t.name""",
                        (ids,),
                    )
                    for r in cur.fetchall():
                        tags_by_meeting.setdefault(r["entity_id"], []).append(r["name"])
        except Exception:
            log.exception("batch tag fetch failed — returning meetings untagged")
    return [
        adapters.meeting_list_row(row, tags=tags_by_meeting.get(row["id"], []))
        for row in rows
    ]


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
        try:
            tag_rows = db.get_tags_for_entity("agenda_item", ar["id"])
            initiative_codes = [
                t["name"] for t in tag_rows if t.get("tag_type") == "initiative"
            ]
        except Exception:
            initiative_codes = []
        agenda_items.append(
            adapters.agenda_item_row(ar, item_docs, item_summary, initiative_codes)
        )

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
    mode: Literal["all", "missing", "briefing"] = Query("all"),
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Pre-flight cost estimate for the meeting summarize pipeline.

    Heuristic — see pipeline.summarizer.estimate_summarization_cost. Returns
    approximate input/output token counts and USD cost, plus a per-level
    breakdown. Also returns `committee_stats` summarizing past completed
    summarize_jobs for meetings in the same committee, so the UI can show
    "typical cost / typical duration" alongside the estimate.

    `mode` mirrors the summarize POST body — "all", "missing", or "briefing".
    """
    from pipeline.summarizer import estimate_summarization_cost

    row = db.get_meeting(meeting_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    try:
        estimate = estimate_summarization_cost(meeting_id, mode=mode)
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


@router.post("/{meeting_id}/summarize", status_code=202)
def start_summarize(
    meeting_id: int,
    body: StartSummarizeBody = Body(default_factory=StartSummarizeBody),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Kick off a background summarize job for this meeting.

    Returns the job_id and the pre-flight estimate. The actual run happens
    in a daemon thread; poll GET /api/jobs/{job_id} for progress.

    The optional `mode` in the body selects how much work to do:
      * "all"      (default) — full re-run, regenerates everything
      * "missing"  — only items lacking summaries; briefing regen if new work
      * "briefing" — reuse item summaries, regenerate only the briefing
    Omitting the body keeps the historical default (full re-run).
    """
    created_by = (user.get("email") if isinstance(user, dict) else None) or "unknown"
    res = jobs_service.start_summarize_job(meeting_id, mode=body.mode, created_by=created_by)
    if res is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return res
