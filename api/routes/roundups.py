"""Monthly roundup endpoints — cross-committee "state of play" per month.

Generation runs in a daemon thread (single LLM call over the month's
briefings); status lives on the monthly_roundups row itself, so the UI just
polls GET /api/roundups/{id} while status == 'generating'. No jobs table.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from pipeline import db_new as db

from .. import adapters, schemas
from ..auth import current_user

log = logging.getLogger("poolside.roundups")

router = APIRouter(prefix="/api/roundups", tags=["roundups"])

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month_start(month: str) -> date:
    if not _MONTH_RE.match(month or ""):
        raise HTTPException(status_code=400,
                            detail="month must be formatted YYYY-MM")
    year, mon = month.split("-")
    return date(int(year), int(mon), 1)


def _run_roundup_job(roundup_id: int) -> None:
    """Daemon-thread entry point. run_monthly_roundup owns status transitions;
    this wrapper only catches catastrophic failures (import errors etc.)."""
    try:
        from pipeline.roundup import run_monthly_roundup

        run_monthly_roundup(roundup_id)
    except Exception as e:  # pragma: no cover — belt and braces
        log.exception("roundup job %s crashed: %s", roundup_id, e)
        try:
            db.update_monthly_roundup(roundup_id, status="error",
                                      error_message=str(e))
        except Exception:
            log.exception("failed to record crash for roundup %s", roundup_id)


@router.get("", response_model=list[schemas.RoundupMonth])
def list_roundups(venue: str = "ISO-NE") -> list[schemas.RoundupMonth]:
    """Month-by-month overview: every month with at least one briefing,
    merged with any generated roundup (report body omitted)."""
    months = db.list_roundup_months(venue)
    rows = db.list_monthly_roundups(venue)
    roundup_by_month = {adapters._month_key(r["month"]): r for r in rows}

    out: list[schemas.RoundupMonth] = []
    seen: set[str] = set()
    for m in months:
        key = adapters._month_key(m["month"])
        seen.add(key)
        out.append(adapters.roundup_month_row(m, roundup_by_month.get(key)))
    # Roundups whose source briefings have since disappeared still show up.
    for key, r in roundup_by_month.items():
        if key not in seen:
            out.append(adapters.roundup_month_row(None, r))

    out.sort(key=lambda x: x.month, reverse=True)
    return out


@router.get("/{roundup_id}", response_model=schemas.Roundup)
def get_roundup(roundup_id: int) -> schemas.Roundup:
    row = db.get_monthly_roundup(roundup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Roundup not found")
    sources = db.get_roundup_meetings(roundup_id)
    return adapters.roundup_row(row, sources=sources)


class GenerateRoundupBody(BaseModel):
    venue: str = "ISO-NE"
    month: str  # "YYYY-MM"


@router.post("/generate", status_code=202, response_model=schemas.Roundup)
def generate_roundup(
    body: GenerateRoundupBody = Body(...),
    user: dict = Depends(current_user),
) -> schemas.Roundup:
    """Create (or reuse) the (venue, month) roundup row and kick off
    generation in a daemon thread. Poll GET /api/roundups/{id} for progress.
    Re-posting for an in-flight month returns the running row untouched;
    posting for a complete/error month regenerates in place.
    """
    month_start = _month_start(body.month)
    venue = db.get_venue(body.venue)
    if venue is None:
        raise HTTPException(status_code=404, detail=f"Unknown venue {body.venue!r}")

    briefings = db.get_month_briefings(body.venue, month_start)
    if not briefings:
        raise HTTPException(
            status_code=400,
            detail=f"No briefings found for {body.venue} {body.month} — "
                   "summarize at least one meeting first.",
        )

    created_by = (user.get("email") if isinstance(user, dict) else None) or "unknown"
    existing = db.get_roundup_by_month(venue["id"], month_start)
    row = existing or db.create_monthly_roundup(
        venue["id"], month_start, created_by=created_by,
    )

    # Atomic admission: flip to 'generating' only if no live claim holds the
    # row (a 'generating' row older than the stale window counts as dead and
    # is taken over). Two concurrent POSTs → exactly one thread, one LLM call.
    claimed = db.claim_monthly_roundup(
        row["id"],
        progress_text=f"Queued — {len(briefings)} briefing(s) to synthesize...",
    )
    if claimed is None:
        fresh = db.get_monthly_roundup(row["id"]) or row
        sources = db.get_roundup_meetings(row["id"])
        return adapters.roundup_row(fresh, sources=sources)

    t = threading.Thread(
        target=_run_roundup_job,
        args=(row["id"],),
        name=f"roundup-job-{row['id']}",
        daemon=True,
    )
    t.start()

    fresh = db.get_monthly_roundup(row["id"]) or row
    sources = db.get_roundup_meetings(row["id"])
    return adapters.roundup_row(fresh, sources=sources)


@router.delete("/{roundup_id}")
def delete_roundup(roundup_id: int) -> dict[str, Any]:
    row = db.get_monthly_roundup(roundup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Roundup not found")
    db.delete_monthly_roundup(roundup_id)
    return {"deleted": True, "roundup_id": roundup_id}
