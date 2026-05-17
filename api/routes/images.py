"""Document-extracted images served by id.

When the summarizer extracts a chart/diagram from a PDF or PPTX, the bytes
land in `document_images.image_b64` and the summary text gets a marker
comment like `<!-- image_id:441 -->`. This route exposes those images so
the rendered summary can show them as inline `<img>` tags.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from pipeline import db_new as db
from ..auth import current_user

router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("/{image_id}")
def get_image(
    image_id: int,
    _: dict = Depends(current_user),
) -> Response:
    rows = db.get_images_by_ids([image_id])
    if not rows:
        raise HTTPException(status_code=404, detail="Image not found")

    row = rows[0]
    b64 = row.get("image_b64") or ""
    if not b64:
        raise HTTPException(status_code=404, detail="Image bytes not stored")

    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Corrupt image bytes: {e}")

    return Response(
        content=raw,
        media_type="image/png",
        headers={
            # The bytes for a given image_id are immutable, so cache hard.
            "Cache-Control": "public, max-age=86400",
        },
    )
