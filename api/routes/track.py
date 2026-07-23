"""Read-analytics beacon — the frontend fires one POST per page visit.

Mounted _ANY in api/main.py: recording that a viewer READ something is
itself a read event, so every role may post here. Deliberately a frontend
beacon rather than server-side capture on GETs — react-query refetches,
prefetches and StrictMode double-mounts would inflate server-side counts,
and db.record_page_view's write-side dedupe window absorbs what's left.
Excluded from the audit middleware (it would double-log every view).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from pipeline import db

from ..auth import current_user

router = APIRouter(prefix="/api/track", tags=["track"])

VALID_ENTITY_TYPES = ("meeting", "briefing", "docket", "roundup", "deep_dive")


@router.post("/view", status_code=204)
def track_view(
    body: dict[str, Any] = Body(...),
    user: dict = Depends(current_user),
) -> Response:
    entity_type = body.get("entity_type")
    if entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"entity_type must be one of: {', '.join(VALID_ENTITY_TYPES)}",
        )
    try:
        entity_id = int(body.get("entity_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="entity_id must be an integer")

    db.record_page_view(
        user_id=user["id"],
        user_email=user.get("email") or "",
        entity_type=entity_type,
        entity_id=entity_id,
    )
    return Response(status_code=204)
