"""Admin endpoints — manual triggers for cron-style work.

These same functions are what APScheduler will call on its cron tick.
Surface them as POST endpoints so analysts can also kick them off manually
from the UI (or via curl) for testing / on-demand refresh.

Role gating is deliberately mixed here (router mounts _EDIT in main.py):
the GETs (scheduler, venues) feed Overview/Add for every role, and
refresh-materials is part of the editor summarize workflow — but the
scraper-wide / destructive ops below each carry require_admin.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pathlib import Path

from pipeline import db
from pipeline.ingest import cleanup_zip_expansion

from .. import lifecycle, orchestrator
from ..services import discovery
from ..auth import require_admin
from fastapi import Depends

log = logging.getLogger("poolside.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


def _load_config() -> dict:
    from pipeline import appconfig
    return appconfig.get_config()


# ─── Discovery ───────────────────────────────────────────────────────────────


@router.post("/discover")
def discover_all_venues(_: dict = Depends(require_admin)) -> dict[str, Any]:
    """Scrape configured committee calendars; create stub meeting rows.
    Thin wrapper — logic lives in api/services/discovery.py (shared with
    the scheduler crons)."""
    return discovery.discover_all_venues()


@router.post("/images/prune")
def prune_images(user: dict = Depends(require_admin)) -> dict[str, Any]:
    """On-demand run of the weekly image prune: delete extracted images no
    stored markdown references (>30 days old, source_url-backed — a
    regenerable cache), then VACUUM FULL so the disk actually shrinks.
    Synchronous; the whole thing is a few seconds."""
    from datetime import datetime, timezone

    from pipeline import appconfig, storage

    result = db.prune_unreferenced_document_images(older_than_days=30)
    objects_deleted = storage.delete_images(result.pop("storage_keys", []))
    if result["deleted"]:
        db.vacuum_document_images()
    stamp = {
        "at": datetime.now(timezone.utc).isoformat(),
        "deleted": result["deleted"],
        "freed_bytes": result["freed_bytes"],
        "objects_deleted": objects_deleted,
        "by": (user.get("email") if isinstance(user, dict) else None) or "admin",
    }
    appconfig.set_config_key("image_prune_last", stamp, updated_by="admin")
    return {**result, "objects_deleted": objects_deleted, "stats": db.image_stats()}


# ─── Materials refresh ───────────────────────────────────────────────────────


@router.post("/refresh")
def refresh_upcoming_meetings(_: dict = Depends(require_admin)) -> dict[str, Any]:
    """Refresh docs + assignment for meetings in the upcoming window.
    Thin wrapper — logic lives in api/services/discovery.py."""
    return discovery.refresh_upcoming_meetings()


@router.post("/refresh-materials/{meeting_id}")
def refresh_one(meeting_id: int) -> dict[str, Any]:
    """End-to-end refresh for a single meeting (called from the UI [Re-check] button).

    Chains: scrape new docs → if no agenda parsed, parse it → run assignment
    over existing-but-unassigned docs → bump lifecycle.
    """
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    cfg = _load_config()
    try:
        return orchestrator.refresh_with_agenda(meeting_id, cfg)
    except Exception as e:
        log.exception("refresh_with_agenda failed for meeting %s: %s", meeting_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse-agenda/{meeting_id}")
def parse_agenda(meeting_id: int, _: dict = Depends(require_admin)) -> dict[str, Any]:
    """Parse the agenda doc for a single meeting, then run assignment over
    docs that were sitting unassigned. Idempotent — refuses if agenda items
    already exist (returns reason)."""
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    cfg = _load_config()
    try:
        result = orchestrator.try_parse_agenda(meeting_id, cfg)
        if result.get("parsed"):
            orchestrator.assign_existing_docs(meeting_id, cfg)
            lifecycle.bump_lifecycle(meeting_id)
        return result
    except Exception as e:
        log.exception("parse_agenda failed for meeting %s: %s", meeting_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ─── Lifecycle introspection ────────────────────────────────────────────────


@router.post("/bump-lifecycle/{meeting_id}")
def bump(meeting_id: int, _: dict = Depends(require_admin)) -> dict[str, str]:
    """Recompute lifecycle_status for a meeting (analyst convenience)."""
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    new_status = lifecycle.bump_lifecycle(meeting_id)
    return {"meeting_id": str(meeting_id), "lifecycle_status": new_status}


@router.post("/cleanup-zip-expansion/{meeting_id}")
def cleanup_zips(
    meeting_id: int,
    _: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Undo a prior zip pre-expansion for this meeting.

    Zip handling now happens inline at summarize time (the summarizer opens
    zips transparently). This endpoint deletes child document rows produced
    by the old `expand-zips` action and un-ignores the original zip docs.
    Idempotent — safe to call on meetings that were never pre-expanded.
    """
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    try:
        result = cleanup_zip_expansion(meeting_id)
    except Exception as e:
        log.exception("cleanup_zip_expansion failed for meeting %s: %s", meeting_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    return {"meeting_id": meeting_id, **result}


@router.get("/scheduler")
def scheduler_status() -> dict[str, Any]:
    from ..scheduler import get_scheduler_status

    return get_scheduler_status()


@router.get("/venues")
def list_venues_with_scrape() -> list[dict[str, Any]]:
    """Surface last_scraped_at per venue — used by the Add Meeting screen."""
    venues = db.get_venues()
    out: list[dict[str, Any]] = []
    for v in venues:
        out.append({
            "id": v["id"],
            "short_name": v["short_name"],
            "name": v.get("name") or v["short_name"],
            "last_scraped_at": v.get("last_scraped_at"),
        })
    return out
