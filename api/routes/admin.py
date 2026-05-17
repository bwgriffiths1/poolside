"""Admin endpoints — manual triggers for cron-style work.

These same functions are what APScheduler will call on its cron tick.
Surface them as POST endpoints so analysts can also kick them off manually
from the UI (or via curl) for testing / on-demand refresh.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pathlib import Path

from pipeline import db_new as db
from pipeline import refresh as pl_refresh
from pipeline import scraper as pl_scraper
from pipeline.ingest import cleanup_zip_expansion

from .. import lifecycle, orchestrator
from ..auth import current_user
from fastapi import Depends

log = logging.getLogger("poolside.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ─── Discovery ───────────────────────────────────────────────────────────────


@router.post("/discover")
def discover_all_venues() -> dict[str, Any]:
    """Scrape configured ISO-NE committee calendars; create stub rows for
    any unknown meetings. Returns the count of new meetings per venue.

    NYISO is currently out of scope for the Vite UI — its scraper still
    lives in v2_pages/ingest_meeting.py (Streamlit-side). Reintroduce a
    venue key here when the NYISO ingest flow is ported.
    """
    cfg = _load_config()
    results: dict[str, int] = {}

    # ISO-NE
    iso_new = 0
    for committee in cfg.get("committees", []):
        if not committee.get("active", True):
            continue
        try:
            events = pl_scraper.scrape_calendar(
                committee, lookahead_days=cfg.get("lookahead_days", 60)
            )
            for ev in events:
                ev_id = str(ev.get("primary_event_id") or "")
                if not ev_id:
                    continue
                # Idempotent: check by external_id
                existing = _find_meeting_by_external_id(ev_id)
                if existing is None:
                    _create_discovered_meeting(
                        venue_short="ISO-NE",
                        committee_short=committee["short"],
                        committee_name=committee["name"],
                        external_id=ev_id,
                        title=ev.get("title") or committee["name"],
                        meeting_date=ev["dates"][0] if ev.get("dates") else None,
                        end_date=ev["dates"][-1] if len(ev.get("dates") or []) > 1 else None,
                        location=ev.get("location") or "",
                    )
                    iso_new += 1
        except Exception as e:
            log.exception("ISO-NE scrape failed for %s: %s", committee.get("short"), e)
    results["ISO-NE"] = iso_new

    _stamp_venue_scrape("ISO-NE")
    return {"discovered": results}


def _find_meeting_by_external_id(external_id: str) -> dict | None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM meetings WHERE external_id = %s LIMIT 1",
                (external_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def _create_discovered_meeting(
    venue_short: str,
    committee_short: str,
    committee_name: str,
    external_id: str,
    title: str,
    meeting_date: date | None,
    end_date: date | None,
    location: str,
) -> int:
    """Write a stub meeting row at lifecycle_status='discovered'."""
    # Find or create the meeting_type
    types = db.get_meeting_types(venue_short_name=venue_short)
    mt = next((t for t in types if t["short_name"] == committee_short), None)
    if mt is None:
        venues = db.get_venues()
        venue = next((v for v in venues if v["short_name"] == venue_short), None)
        if venue is None:
            raise RuntimeError(f"Unknown venue {venue_short}")
        mt_id = db.create_meeting_type(
            venue_id=venue["id"], name=committee_name, short_name=committee_short
        )
    else:
        mt_id = mt["id"]

    meeting_id = db.upsert_meeting(
        meeting_type_id=mt_id,
        external_id=external_id,
        title=title,
        meeting_date=meeting_date or date.today(),
        end_date=end_date,
        location=location,
    )
    lifecycle.bump_lifecycle(meeting_id)
    return meeting_id


def _stamp_venue_scrape(venue_short: str) -> None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "UPDATE venues SET last_scraped_at = NOW() WHERE short_name = %s",
                (venue_short,),
            )


# ─── Materials refresh ───────────────────────────────────────────────────────


@router.post("/refresh")
def refresh_upcoming_meetings() -> dict[str, Any]:
    """For each meeting within [today-3, today+21] not at 'approved',
    fetch latest docs + auto-assign.
    """
    cfg = _load_config()
    today = date.today()
    cur_from = today - timedelta(days=3)
    cur_to = today + timedelta(days=21)

    with db._conn() as conn:
        with db._cursor(conn) as c:
            c.execute("""
                SELECT id FROM meetings
                WHERE meeting_date BETWEEN %s AND %s
                  AND COALESCE(lifecycle_status, 'discovered') != 'approved'
                ORDER BY meeting_date
            """, (cur_from, cur_to))
            ids = [r["id"] for r in c.fetchall()]

    refreshed: list[dict[str, Any]] = []
    for mid in ids:
        try:
            res = orchestrator.refresh_with_agenda(mid, cfg)
            refreshed.append(res)
        except Exception as e:
            log.exception("refresh_with_agenda failed for meeting %s: %s", mid, e)
            refreshed.append({"meeting_id": mid, "error": str(e)})

    return {"refreshed": refreshed, "count": len(refreshed)}


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
def parse_agenda(meeting_id: int) -> dict[str, Any]:
    """Parse the agenda doc for a single meeting, then run assignment over
    docs that were sitting unassigned. Idempotent — refuses if agenda items
    already exist (returns reason)."""
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    cfg = _load_config()
    try:
        result = orchestrator.try_parse_agenda(meeting_id, cfg)
        if result.get("parsed"):
            orchestrator._assign_existing_docs(meeting_id, cfg)
            lifecycle.bump_lifecycle(meeting_id)
        return result
    except Exception as e:
        log.exception("parse_agenda failed for meeting %s: %s", meeting_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ─── Lifecycle introspection ────────────────────────────────────────────────


@router.post("/bump-lifecycle/{meeting_id}")
def bump(meeting_id: int) -> dict[str, str]:
    """Recompute lifecycle_status for a meeting (analyst convenience)."""
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    new_status = lifecycle.bump_lifecycle(meeting_id)
    return {"meeting_id": str(meeting_id), "lifecycle_status": new_status}


@router.post("/cleanup-zip-expansion/{meeting_id}")
def cleanup_zips(
    meeting_id: int,
    _: dict = Depends(current_user),
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
