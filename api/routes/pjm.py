"""PJM surface — the /pjm page shell and the PJM-specific APIs.

Everything else the /pjm page does (meeting lists, documents, parse-agenda,
summarize jobs, briefings) goes through the existing generic endpoints; only
committee-page discovery and the retroactive agenda backfill are
PJM-specific. The daily cron covers discovery (discover_all_venues); the
button remains for on-demand runs.

`page_router` serves the demo HTML anonymously, exactly like the SPA's
index.html: the page itself is a shell, and every data fetch behind it
requires the session cookie (path="/" — same-origin fetches authenticate).
It must be mounted before the SPA catch-all in api/main.py.
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pipeline import db

from ..auth import require_admin
from ..services import discovery

log = logging.getLogger("poolside.pjm")

router = APIRouter(prefix="/api/pjm", tags=["pjm"])

page_router = APIRouter(tags=["pjm"])

_DEMO_PAGE = Path(__file__).resolve().parent.parent.parent / "demo" / "pjm.html"


@router.post("/discover")
def discover_pjm(_: dict = Depends(require_admin)) -> dict[str, Any]:
    """Scrape configured PJM committee pages; upsert meetings + documents.
    Zero LLM calls; idempotent (re-run reports 0 new)."""
    return discovery.discover_pjm()


# ── Agenda backfill for retroactively-discovered meetings ────────────────
#
# One run at a time; the thread handle doubles as the admission latch and
# the last completed result is kept for the status endpoint, so the whole
# thing can be driven from a browser console or curl without log access.

_backfill_thread: threading.Thread | None = None
_backfill_state: dict[str, Any] = {"running": False, "last_result": None}


class BackfillBody(BaseModel):
    since: str | None = None  # ISO date; defaults to Jan 1 of this year
    dry_run: bool = False


def _run_backfill(since: date) -> None:
    try:
        result = discovery.backfill_pjm_agendas(since)
        _backfill_state["last_result"] = result
        log.info(
            "PJM agenda backfill done: %s/%s parsed",
            result.get("parsed"), result.get("candidates"),
        )
    except Exception as e:  # pragma: no cover — belt and braces
        log.exception("PJM agenda backfill crashed: %s", e)
        _backfill_state["last_result"] = {"error": str(e)}
    finally:
        _backfill_state["running"] = False


@router.post("/backfill-agendas", status_code=202)
def backfill_agendas(
    body: BackfillBody, _: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Parse agendas for PJM meetings discovered outside the refresh window
    (docs but no agenda items). dry_run lists the candidates and stops; a
    real run works newest-first in a background thread — poll GET
    /api/pjm/backfill-agendas for progress. One Haiku parse per meeting,
    no summaries.
    """
    try:
        since = (date.fromisoformat(body.since) if body.since
                 else date(date.today().year, 1, 1))
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="since must be an ISO date (YYYY-MM-DD)")

    candidates = db.list_venue_meetings_missing_agendas("PJM", since)
    preview = [
        {"meeting_id": m["id"], "type_short": m.get("type_short"),
         "meeting_date": str(m.get("meeting_date")),
         "doc_count": m.get("doc_count")}
        for m in candidates
    ]
    if body.dry_run:
        return {"dry_run": True, "since": since.isoformat(),
                "candidates": len(preview), "meetings": preview}

    global _backfill_thread
    if _backfill_thread is not None and _backfill_thread.is_alive():
        raise HTTPException(status_code=409,
                            detail="A PJM agenda backfill is already running")
    _backfill_state["running"] = True
    _backfill_state["last_result"] = None
    _backfill_thread = threading.Thread(
        target=_run_backfill, args=(since,),
        name="pjm-agenda-backfill", daemon=True,
    )
    _backfill_thread.start()
    return {"started": True, "since": since.isoformat(),
            "candidates": len(preview), "meetings": preview}


@router.get("/backfill-agendas")
def backfill_agendas_status(_: dict = Depends(require_admin)) -> dict[str, Any]:
    """Whether a backfill is running, and the last completed run's summary."""
    return {
        "running": bool(_backfill_thread is not None
                        and _backfill_thread.is_alive()),
        "last_result": _backfill_state["last_result"],
    }


@page_router.get("/pjm", include_in_schema=False)
def pjm_demo_page() -> FileResponse:
    return FileResponse(_DEMO_PAGE, media_type="text/html")
