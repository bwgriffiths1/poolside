"""Manual meeting ingest by URL.

Mirrors the Streamlit `v2_pages/ingest_meeting.py` flow but exposes it as a
single REST call so the Vite Add Meeting page can drive it.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from pipeline import db
from pipeline.ingest import ingest_meeting
from pipeline.scraper import fetch_event_docs, fetch_event_metadata
from ..auth import current_user

log = logging.getLogger("poolside.manual_ingest")

router = APIRouter(prefix="/api/admin", tags=["manual_ingest"])

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _parse_event_id(raw: str) -> str | None:
    """Accept a bare numeric id ('215042') or any URL containing eventId=N."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return raw
    try:
        qs = parse_qs(urlparse(raw).query)
        if "eventId" in qs and qs["eventId"]:
            return qs["eventId"][0]
    except Exception:
        pass
    m = re.search(r"eventId=(\d+)", raw)
    return m.group(1) if m else None


class IngestByUrlRequest(BaseModel):
    url: str
    committee_short: str | None = None  # override if the scraper can't infer


class IngestByUrlResult(BaseModel):
    meeting_id: int
    external_id: str
    committee_short: str
    docs: int
    already_existed: bool


@router.post("/ingest-by-url", response_model=IngestByUrlResult)
def ingest_by_url(
    body: IngestByUrlRequest,
    _: dict = Depends(current_user),
) -> IngestByUrlResult:
    event_id = _parse_event_id(body.url)
    if not event_id:
        raise HTTPException(
            status_code=400,
            detail="Could not extract an ISO-NE eventId from that URL.",
        )

    meta = fetch_event_metadata(event_id)
    if not meta or not meta.get("start_date"):
        raise HTTPException(
            status_code=404,
            detail=f"ISO-NE has no metadata for event {event_id}.",
        )

    committee_short = (body.committee_short or "").strip().upper() or None
    if not committee_short:
        # Try to infer from scraped committee name against config.yaml.
        committee_name = (meta.get("committee") or "").strip()
        cfg = _load_config()
        for c in cfg.get("committees", []):
            if c.get("name", "").lower() == committee_name.lower():
                committee_short = c.get("short")
                break

    if not committee_short:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not infer committee short name from the page. "
                "Please pass committee_short explicitly (e.g. 'MC')."
            ),
        )

    # Check whether the meeting already exists, so we can flag it.
    venue = db.get_venue("ISO-NE")
    already = None
    if venue:
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute(
                    """SELECT m.id FROM meetings m
                         JOIN meeting_types mt ON mt.id = m.meeting_type_id
                        WHERE m.external_id = %s
                          AND mt.venue_id = %s
                          AND mt.short_name = %s
                        LIMIT 1""",
                    (event_id, venue["id"], committee_short),
                )
                row = cur.fetchone()
                already = dict(row) if row else None

    docs = fetch_event_docs(event_id)
    dates = [meta["start_date"]]
    if meta.get("end_date"):
        dates.append(meta["end_date"])

    meeting_dict = {
        "primary_event_id": event_id,
        "committee_short": committee_short,
        "dates": dates,
        "location": meta.get("location") or "",
        "title": None,
    }

    cfg = _load_config()
    try:
        meeting_id = ingest_meeting(meeting_dict, docs, cfg, venue_short="ISO-NE")
    except Exception as e:
        log.exception("manual ingest_meeting failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    if meeting_id is None:
        raise HTTPException(
            status_code=500,
            detail="Ingest failed — see server logs.",
        )

    return IngestByUrlResult(
        meeting_id=meeting_id,
        external_id=event_id,
        committee_short=committee_short,
        docs=len(docs),
        already_existed=already is not None,
    )
