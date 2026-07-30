"""Calendar discovery + bulk materials refresh.

Called by the APScheduler crons and by the POST /api/admin/{discover,refresh}
endpoints — same code path for both, as before, but the scheduler no longer
imports route modules to get at it."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from pipeline import db
from pipeline import scraper as pl_scraper
from pipeline.dedupe import materials_match

from .. import lifecycle, orchestrator

log = logging.getLogger("poolside.discovery")


def _load_config() -> dict:
    from pipeline import appconfig
    return appconfig.get_config()


def discover_all_venues() -> dict[str, Any]:
    """Scrape every venue's calendars; create stub rows for any unknown
    meetings. Returns the count of new meetings per venue.

    Two venues have live scrapers: ISO-NE (calendar pages → stubs, docs
    arrive via refresh) and PJM (committee pages → meetings + docs in one
    pass; see discover_pjm). To add another, give it a discovery block
    here, a scraper module in pipeline/, and a prompt set — see the 2026-07
    architecture review for the adapter shape.
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
                if _reconcile_isone_event(committee, ev):
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

    # PJM. Isolated in its own try so a PJM-side failure (markup change,
    # network) can't discard the ISO-NE results we just gathered — the
    # per-venue count simply stays 0 and the exception is logged.
    try:
        results.update(discover_pjm().get("discovered", {}))
    except Exception as e:
        log.exception("PJM discover failed: %s", e)
        results.setdefault("PJM", 0)

    return {"discovered": results}


def _reconcile_isone_event(committee: dict, ev: dict) -> bool:
    """Match one scraped calendar group to an existing meeting and widen it,
    or create a new meeting. Returns True when a meeting was created.

    Matching is two-stage: any shared day-event ID first; failing that, the
    materials guard — ISO occasionally re-posts a day under a brand-new event
    ID (June 11 2026 MC arrived as 162644 beside 160096/97), and rows created
    before external_ids existed only know their first day's ID.
    """
    ev_ids = [str(i) for i in (ev.get("all_event_ids") or []) if i]
    if not ev_ids:
        primary = str(ev.get("primary_event_id") or "")
        ev_ids = [primary] if primary else []
    dates = ev.get("dates") or []
    if not ev_ids or not dates:
        return False
    start, end = dates[0], dates[-1]

    mt_id = _resolve_meeting_type_id("ISO-NE", committee["short"], committee["name"])

    existing = db.find_meeting_by_event_ids(mt_id, ev_ids)
    if existing is None:
        existing = _find_same_materials_meeting(mt_id, ev_ids, start, end)
    if existing is not None:
        # Same meeting seen again — under fresh day-event IDs, or with days
        # that were past/beyond the horizon on an earlier scrape. Widen the
        # span (never shrink) and remember the new IDs instead of duplicating.
        db.extend_meeting_span(
            existing["id"], str(start), str(end), add_event_ids=ev_ids,
        )
        return False

    _create_discovered_meeting(
        meeting_type_id=mt_id,
        external_id=ev_ids[0],
        title=ev.get("title") or committee["name"],
        meeting_date=start,
        end_date=end if end != start else None,
        location=ev.get("location") or "",
        external_ids=ev_ids,
    )
    return True


def _find_same_materials_meeting(
    mt_id: int, ev_ids: list[str], start: date, end: date,
) -> dict | None:
    """The dedupe rule at discovery time: a same-committee meeting whose span
    overlaps this group's and whose stored documents match what the event's
    documents API returns IS this meeting."""
    candidates = db.find_overlapping_meetings(
        mt_id, str(start), str(end), slack_days=1,
    )
    if not candidates:
        return None
    try:
        scraped = pl_scraper.fetch_event_docs(ev_ids[0])
    except Exception as e:
        log.warning("materials guard: doc fetch failed for event %s: %s", ev_ids[0], e)
        return None
    scraped_urls = {d["url"] for d in scraped if d.get("url")}
    if not scraped_urls:
        return None
    for cand in candidates:
        if materials_match(scraped_urls, db.get_document_urls(cand["id"])):
            log.info(
                "discover: event %s shares materials with meeting %s (%s) — merging",
                ev_ids[0], cand["id"], cand.get("meeting_date"),
            )
            return cand
    return None


def _resolve_meeting_type_id(venue_short: str, committee_short: str,
                             committee_name: str) -> int:
    """Find or create the meeting_type for a venue + committee."""
    types = db.get_meeting_types(venue_short_name=venue_short)
    mt = next((t for t in types if t["short_name"] == committee_short), None)
    if mt is not None:
        return mt["id"]
    venues = db.get_venues()
    venue = next((v for v in venues if v["short_name"] == venue_short), None)
    if venue is None:
        raise RuntimeError(f"Unknown venue {venue_short}")
    # create_meeting_type / upsert_meeting return full row dicts — take
    # the ids. (Passing the dicts through crashed every genuinely-new
    # meeting's bump_lifecycle with "can't adapt type 'dict'", silently
    # swallowed by the per-committee except in discover_all_venues.)
    return db.create_meeting_type(
        venue_id=venue["id"], name=committee_name, short_name=committee_short
    )["id"]


def _create_discovered_meeting(
    meeting_type_id: int,
    external_id: str,
    title: str,
    meeting_date: date | None,
    end_date: date | None,
    location: str,
    external_ids: list[str] | None = None,
) -> int:
    """Write a stub meeting row at lifecycle_status='discovered'."""
    meeting_id = db.upsert_meeting(
        meeting_type_id=meeting_type_id,
        external_id=external_id,
        title=title,
        meeting_date=meeting_date or date.today(),
        end_date=end_date,
        location=location,
        external_ids=external_ids,
    )["id"]
    lifecycle.bump_lifecycle(meeting_id)
    return meeting_id


def _stamp_venue_scrape(venue_short: str) -> None:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "UPDATE venues SET last_scraped_at = NOW() WHERE short_name = %s",
                (venue_short,),
            )


def discover_pjm() -> dict[str, Any]:
    """Scrape configured PJM committee pages; upsert meetings + documents.

    Called by discover_all_venues() (so the daily cron keeps PJM current)
    and directly by POST /api/pjm/discover for an on-demand run.

    Unlike the ISO-NE calendar path (stubs only; docs arrive via refresh),
    one PJM committee-page fetch carries the full materials list, so
    documents are upserted in the same pass. Zero LLM calls. Idempotent —
    a re-run reports 0 new. Upcoming meetings (registration table) become
    stub rows under the same pjm-{slug}-{yyyymmdd} external-id scheme, so
    a stub converges onto its accordion entry once materials post.
    """
    from pipeline import pjm_scraper

    cfg = _load_config()
    new_meetings = 0
    meetings_seen = 0
    detail: list[dict[str, Any]] = []

    for committee in (cfg.get("pjm") or {}).get("committees", []):
        if not committee.get("active", True):
            continue
        try:
            html = pjm_scraper.fetch_committee_page(committee["url"])
            parsed = pjm_scraper.parse_committee_page(html, committee["url"])
        except Exception as e:
            log.exception("PJM scrape failed for %s: %s", committee.get("short"), e)
            detail.append({"committee": committee.get("short"), "error": str(e)})
            continue

        entries = [
            {**m, "location": "", "kind": "materials"} for m in parsed["meetings"]
        ] + [
            {"date": u["date"], "title": u["title"], "documents": [],
             "location": u.get("location", ""), "kind": "upcoming"}
            for u in parsed["upcoming"]
        ]
        meetings_seen += len(parsed["meetings"])

        mt_id = _resolve_meeting_type_id("PJM", committee["short"], committee["name"])

        seen_external_ids: set[str] = set()
        for entry in entries:
            external_id = pjm_scraper.pjm_external_id(committee["slug"], entry["date"])
            if external_id in seen_external_ids:
                continue  # accordion + upcoming rows for the same date
            seen_external_ids.add(external_id)

            existing = db.find_meeting_by_event_ids(mt_id, [external_id])
            created = existing is None
            if created:
                meeting_id = _create_discovered_meeting(
                    meeting_type_id=mt_id,
                    external_id=external_id,
                    title=entry["title"] or committee["name"],
                    meeting_date=entry["date"],
                    end_date=None,
                    location=entry.get("location") or "",
                )
                new_meetings += 1
            else:
                meeting_id = existing["id"]

            existing_filenames = db.get_existing_filenames(meeting_id)
            new_docs: list[str] = []
            for doc in entry["documents"]:
                if doc["filename"] in existing_filenames:
                    continue
                db.upsert_document(
                    meeting_id=meeting_id,
                    filename=doc["filename"],
                    file_type=doc.get("ext") or "",
                    source_url=doc["url"],
                    ceii_skipped=False,
                )
                new_docs.append(doc["filename"])
            # Re-bump after doc upserts even for just-created meetings:
            # _create_discovered_meeting bumps before any documents exist,
            # which would leave a materials-bearing meeting at 'discovered'.
            if new_docs:
                lifecycle.bump_lifecycle(meeting_id)

            detail.append({
                "committee": committee["short"],
                "external_id": external_id,
                "meeting_id": meeting_id,
                "meeting_date": entry["date"].isoformat(),
                "title": entry["title"],
                "created": created,
                "new_docs": new_docs,
                "doc_count": len(existing_filenames) + len(new_docs),
                "posted_dates": {
                    d["filename"]: d["posted_date"].isoformat()
                    for d in entry["documents"] if d.get("posted_date")
                },
            })

    if meetings_seen > 0:
        _stamp_venue_scrape("PJM")
    else:
        log.warning(
            "PJM discover: 0 meetings parsed — NOT stamping last_scraped_at"
        )
    return {"discovered": {"PJM": new_meetings}, "meetings": detail}


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
