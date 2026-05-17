"""Briefing reader endpoint — fetch stored markdown, parse to typed AST."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response

from pipeline import db_new as db
from pipeline.briefing import generate_docx_bytes
from datetime import datetime, timezone

from fastapi import Depends

from .. import adapters, briefing_parser, schemas
from ..auth import current_user
from .notifications import fan_out_to_watchers

router = APIRouter(prefix="/api/meetings", tags=["briefings"])


@router.get("/{meeting_id}/briefing", response_model=schemas.Briefing)
def get_briefing(meeting_id: int) -> schemas.Briefing:
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    summary = db.get_current_summary("meeting", meeting_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="No briefing for this meeting")

    md = adapters.resolve_image_refs(
        summary.get("detailed") or summary.get("one_line") or ""
    )

    venue_short = meeting.get("venue_short") or meeting.get("venue") or ""
    venue_name = meeting.get("venue_name") or venue_short
    location = meeting.get("location") or ""
    type_name = meeting.get("type_name") or ""

    # Leave word_count / reading_time absent so briefing_parser computes them
    # from the rendered markdown (~250 words/minute is its default cadence).
    meta = {
        "title": f"{type_name} — {meeting.get('meeting_date', '')}",
        "subtitle": f"{venue_name} · {location}",
        "headline": summary.get("one_line") or "",
        "generated_at": str(summary.get("created_at", "")),
        "model": summary.get("model") or summary.get("created_by") or "",
    }

    return briefing_parser.parse_briefing_markdown(md, meta)


@router.get("/{meeting_id}/briefing.docx")
def export_briefing_docx(meeting_id: int) -> Response:
    """Render the current briefing markdown to a NEPOOL-branded .docx and stream it."""
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    summary = db.get_current_summary("meeting", meeting_id)
    if summary is None or not summary.get("detailed"):
        raise HTTPException(status_code=404, detail="No briefing for this meeting")

    docx_bytes = generate_docx_bytes(
        briefing_text=summary["detailed"],
        committee=meeting.get("type_name") or "Committee",
        meeting_dates=[str(meeting.get("meeting_date", ""))],
    )

    ext_id = meeting.get("external_id") or meeting_id
    filename = f"Briefing_{ext_id}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
        },
    )


# ── Approve / unapprove ────────────────────────────────────────────────


@router.get("/{meeting_id}/briefing/approval")
def get_briefing_approval(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> dict:
    """Return the current briefing's approval metadata so the UI can decide
    whether to show Approve vs. Unapprove (and by whom / when)."""
    summary = db.get_current_summary("meeting", meeting_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="No briefing for this meeting")
    approved_at = summary.get("approved_at")
    return {
        "version": summary.get("version"),
        "status": summary.get("status"),
        "approved_by": summary.get("approved_by"),
        "approved_at": approved_at.isoformat()
            if approved_at is not None and hasattr(approved_at, "isoformat") else approved_at,
    }


@router.post("/{meeting_id}/briefing/approve")
def approve_briefing(
    meeting_id: int,
    user: dict = Depends(current_user),
) -> dict:
    """Stamp the current briefing as approved, bump meeting lifecycle, and
    fan out notifications to watchers."""
    summary = db.get_current_summary("meeting", meeting_id)
    if summary is None or not summary.get("detailed"):
        raise HTTPException(status_code=404, detail="No briefing to approve")

    now = datetime.now(timezone.utc)
    approver = user.get("email") or user.get("name") or "unknown"

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """UPDATE summary_versions
                       SET status = 'approved',
                           approved_by = %s,
                           approved_at = %s
                     WHERE id = %s
                       AND status != 'superseded'
                 RETURNING *""",
                (approver, now, summary["id"]),
            )
            row = cur.fetchone()
            cur.execute(
                """UPDATE meetings SET lifecycle_status = 'approved' WHERE id = %s""",
                (meeting_id,),
            )

    if row is None:
        raise HTTPException(status_code=500, detail="Approval failed")

    meeting = db.get_meeting(meeting_id) or {}
    fan_out_to_watchers(
        meeting_id,
        "briefing_approved",
        payload={
            "meeting_id": meeting_id,
            "title": meeting.get("title") or meeting.get("type_name") or "",
            "venue": meeting.get("venue_short") or meeting.get("venue") or "",
            "committee": meeting.get("type_short") or "",
            "meeting_date": str(meeting.get("meeting_date") or ""),
            "approved_by": approver,
        },
        exclude_user_id=user["id"],
    )

    return {
        "status": "approved",
        "approved_by": approver,
        "approved_at": now.isoformat(),
        "version": row["version"],
    }


@router.post("/{meeting_id}/briefing/unapprove")
def unapprove_briefing(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> dict:
    """Roll the current briefing back from approved → draft (e.g. typo
    found after publish). Does not touch share tokens — those stay live."""
    summary = db.get_current_summary("meeting", meeting_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="No briefing")

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """UPDATE summary_versions
                       SET status = 'draft',
                           approved_by = NULL,
                           approved_at = NULL
                     WHERE id = %s""",
                (summary["id"],),
            )
            cur.execute(
                """UPDATE meetings SET lifecycle_status = 'summarized' WHERE id = %s""",
                (meeting_id,),
            )

    return {"status": "draft"}
