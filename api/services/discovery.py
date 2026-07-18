"""Calendar discovery + bulk materials refresh.

Called by the APScheduler crons and by the POST /api/admin/{discover,refresh}
endpoints — same code path for both, as before, but the scheduler no longer
imports route modules to get at it."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from pipeline import db_new as db
from pipeline import scraper as pl_scraper

from .. import lifecycle, orchestrator

log = logging.getLogger("poolside.discovery")


def _load_config() -> dict:
    from pipeline import appconfig
    return appconfig.get_config()


def discover_all_venues() -> dict[str, Any]:
    """Scrape configured ISO-NE committee calendars; create stub rows for
    any unknown meetings. Returns the count of new meetings per venue.

    ISO-NE is the only venue with a live scraper. To add another venue,
    give it a discovery block here, a scraper module in pipeline/, and a
    prompt set — see the 2026-07 architecture review for the adapter shape.
    """
    cfg = _load_config()
    results: dict[str, int] = {}

    # ISO-NE
    iso_new = 0
    events_seen = 0
    for committee in cfg.get("committees", []):
        if not committee.get("active", True):
            continue
        try:
            events = pl_scraper.scrape_calendar(
                committee, lookahead_days=cfg.get("lookahead_days", 60)
            )
            events_seen += len(events)
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

    # Only stamp when the scrape demonstrably worked. The ISO-NE calendar
    # always has upcoming events across the configured committees, so zero
    # events parsed means broken markup/network, not a quiet week — and the
    # 48h drift alarm in api/scheduler.py keys off this stamp. Stamping
    # unconditionally (the old behavior) made that alarm unfireable.
    if events_seen > 0:
        _stamp_venue_scrape("ISO-NE")
    else:
        log.warning(
            "discover: 0 events parsed across all committees — "
            "NOT stamping last_scraped_at (drift alarm will fire after 48h)"
        )
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
