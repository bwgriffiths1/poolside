"""Document-extracted images served by id.

When the summarizer extracts a chart/diagram from a PDF or PPTX, the bytes
go to object storage (document_images.storage_key — see pipeline/storage.py)
or, for legacy rows, base64 in document_images.image_b64, and the summary
text gets a marker comment like `<!-- image_id:441 -->`. This route exposes
those images so the rendered summary can show them as inline `<img>` tags.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from pipeline import db, storage
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

    raw = storage.get_image_bytes(rows[0])
    if raw is None:
        # Missing bytes or a transient storage failure — 404 without the
        # cache header below, so browsers retry rather than pin the failure.
        raise HTTPException(status_code=404, detail="Image bytes not stored")

    return Response(
        content=raw,
        media_type="image/png",
        headers={
            # The bytes for a given image_id are immutable, so cache hard.
            "Cache-Control": "public, max-age=86400",
        },
    )
