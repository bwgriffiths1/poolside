"""PJM demo surface — the /pjm page shell and its one PJM-specific API.

Demo-phase isolation: everything else the /pjm page does (meeting lists,
documents, parse-agenda, summarize jobs, briefings) goes through the
existing generic endpoints; only committee-page discovery is PJM-specific.
The daily discover cron stays ISO-NE-only — PJM discovery is this button.

`page_router` serves the demo HTML anonymously, exactly like the SPA's
index.html: the page itself is a shell, and every data fetch behind it
requires the session cookie (path="/" — same-origin fetches authenticate).
It must be mounted before the SPA catch-all in api/main.py.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

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


@page_router.get("/pjm", include_in_schema=False)
def pjm_demo_page() -> FileResponse:
    return FileResponse(_DEMO_PAGE, media_type="text/html")
