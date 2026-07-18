"""Editor image uploads — clipboard paste into the lush editor.

The flow:
  1. User pastes a screenshot in the editor.
  2. The browser extracts a Blob from the clipboard and POSTs base64 here.
  3. We persist bytes in `editor_images`, scoped to the meeting + entity.
  4. We return a URL like `/api/editor-images/{id}` which the editor inserts
     into the markdown as `![pasted](/api/editor-images/{id})`.
  5. The preview pane + Briefing reader render the URL via <img>.
"""
from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from pipeline import db

router = APIRouter(prefix="/api/editor-images", tags=["editor-images"])


@router.post("")
def upload(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Accept a base64-encoded image from the editor's paste handler.

    Body:
      entity_type: 'meeting' | 'agenda_item'  (required)
      entity_id:   int                        (required)
      image_b64:   base64 image bytes         (required)
      mime_type:   default 'image/png'
      filename:    optional client-supplied label
    """
    entity_type = body.get("entity_type")
    entity_id = body.get("entity_id")
    image_b64 = body.get("image_b64")
    mime_type = body.get("mime_type") or "image/png"
    filename = body.get("filename")

    if entity_type not in ("meeting", "agenda_item"):
        raise HTTPException(status_code=400, detail="Invalid entity_type")
    if not isinstance(entity_id, int):
        raise HTTPException(status_code=400, detail="entity_id required (int)")
    if not isinstance(image_b64, str) or not image_b64:
        raise HTTPException(status_code=400, detail="image_b64 required")

    # Strip a leading "data:image/png;base64," prefix if present.
    if "," in image_b64 and image_b64.lstrip().startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]

    try:
        raw = base64.b64decode(image_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad base64: {e}")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (8 MB max)")

    # Resolve meeting_id for cascading deletes
    if entity_type == "meeting":
        if db.get_meeting(entity_id) is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        meeting_id = entity_id
    else:
        item = db.get_agenda_item(entity_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Agenda item not found")
        meeting_id = item["meeting_id"]

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO editor_images
                    (meeting_id, entity_type, entity_id, filename, mime_type, data)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (meeting_id, entity_type, entity_id, filename, mime_type, psycopg2_bytes(raw)),
            )
            row = cur.fetchone()

    return {
        "id": row["id"],
        "url": f"/api/editor-images/{row['id']}",
        "mime_type": mime_type,
        "size": len(raw),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@router.get("/{image_id}")
def fetch(image_id: int) -> Response:
    """Stream raw bytes for an editor image.

    Long browser cache — image content is content-addressed by id and never
    mutated (re-pastes create a new row).
    """
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "SELECT mime_type, data FROM editor_images WHERE id = %s",
                (image_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")
    raw = bytes(row["data"]) if isinstance(row["data"], memoryview) else row["data"]
    return Response(
        content=raw,
        media_type=row["mime_type"] or "image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def psycopg2_bytes(raw: bytes):
    """psycopg2 wants a Binary adapter for bytea params."""
    import psycopg2
    return psycopg2.Binary(raw)
