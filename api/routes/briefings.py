"""Briefing reader endpoint — fetch stored markdown, parse to typed AST."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response

from pipeline import db_new as db
from pipeline.briefing import generate_docx_bytes
from .. import briefing_parser, schemas

router = APIRouter(prefix="/api/meetings", tags=["briefings"])


@router.get("/{meeting_id}/briefing", response_model=schemas.Briefing)
def get_briefing(meeting_id: int) -> schemas.Briefing:
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    summary = db.get_current_summary("meeting", meeting_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="No briefing for this meeting")

    md = summary.get("detailed") or summary.get("one_line") or ""

    venue_short = meeting.get("venue_short") or meeting.get("venue") or ""
    venue_name = meeting.get("venue_name") or venue_short
    location = meeting.get("location") or ""
    type_name = meeting.get("type_name") or ""

    meta = {
        "title": f"{type_name} — {meeting.get('meeting_date', '')}",
        "subtitle": f"{venue_name} · {location}",
        "headline": summary.get("one_line") or "",
        "generated_at": str(summary.get("created_at", "")),
        "model": summary.get("model") or summary.get("created_by") or "",
        "word_count": 0,
        "reading_time": 0,
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
