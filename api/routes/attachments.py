"""Meeting file attachments — the "Files" portal.

Lets a user upload arbitrary files against a meeting (hand-written briefings,
scanned notes, ad-hoc reference docs) and download them back verbatim. Bytes
live in Postgres `meeting_attachments.data` (BYTEA), mirroring the storage
approach of editor_images.

Upload is base64-over-JSON — matching editor_images — so no `python-multipart`
dependency is needed. Files are capped at 25 MB.
"""
from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from pipeline import db
from ..auth import current_user

router = APIRouter(tags=["attachments"])

MAX_BYTES = 25 * 1024 * 1024  # 25 MB


@router.get("/api/meetings/{meeting_id}/attachments")
def list_attachments(meeting_id: int) -> dict[str, Any]:
    """Return attachment metadata for a meeting (no file bytes)."""
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {"attachments": db.get_meeting_attachments(meeting_id)}


@router.post("/api/meetings/{meeting_id}/attachments")
def upload_attachment(
    meeting_id: int,
    body: dict[str, Any] = Body(...),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Accept a base64-encoded file for a meeting.

    Body:
      filename:  str            (required)
      data_b64:  base64 bytes   (required; may include a data: URL prefix)
      mime_type: str            (optional; default application/octet-stream)
      note:      str            (optional caption)
    """
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    filename = (body.get("filename") or "").strip()
    data_b64 = body.get("data_b64")
    mime_type = (body.get("mime_type") or "").strip() or "application/octet-stream"
    note = body.get("note")
    if isinstance(note, str):
        note = note.strip() or None

    if not filename:
        raise HTTPException(status_code=400, detail="filename required")
    if not isinstance(data_b64, str) or not data_b64:
        raise HTTPException(status_code=400, detail="data_b64 required")

    # Strip a leading "data:<mime>;base64," prefix if the client sent one.
    if "," in data_b64 and data_b64.lstrip().startswith("data:"):
        data_b64 = data_b64.split(",", 1)[1]

    try:
        raw = base64.b64decode(data_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad base64: {e}")
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (25 MB max)")

    return db.create_meeting_attachment(
        meeting_id=meeting_id,
        filename=filename,
        mime_type=mime_type,
        data=raw,
        note=note,
        uploaded_by=user.get("email"),
    )


@router.get("/api/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int) -> Response:
    """Stream an attachment's bytes as a file download."""
    row = db.get_meeting_attachment(attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    raw = bytes(row["data"]) if isinstance(row["data"], memoryview) else row["data"]
    filename = row["filename"] or f"attachment-{attachment_id}"
    # RFC 5987 encoding so non-ASCII filenames survive the header.
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=raw,
        media_type=row["mime_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/api/attachments/{attachment_id}")
def delete_attachment(attachment_id: int) -> dict[str, bool]:
    """Delete an attachment."""
    if not db.delete_meeting_attachment(attachment_id):
        raise HTTPException(status_code=404, detail="Attachment not found")
    return {"deleted": True}
