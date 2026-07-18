"""Ingest endpoints — recent jobs list.

Intentionally minimal: /api/ingest/jobs returns the most recent ingested
meetings as a stand-in until a real job log table is added. (A demo SSE
log-stream endpoint that emitted fabricated progress lines was removed
2026-07 — reintroduce only when wired to real pipeline events.)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pipeline import db

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.get("/jobs")
def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    # No real job log table yet — surface recently-ingested meetings as a stand-in.
    rows = db.list_meetings_overview(past_days=730, future_days=0)
    # Only show meetings that have actually been ingested (docs > 0).
    rows = [r for r in rows if (r.get("doc_count") or 0) > 0]
    rows = sorted(rows, key=lambda r: r.get("meeting_date") or "", reverse=True)[:limit]
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": f"ing-{r['id']}",
            "meeting_id": r["id"],
            "status": "complete",
            "started": str(r.get("meeting_date") or ""),
            "label": f"{r.get('venue_short')} {r.get('type_short')} {r.get('meeting_date')}",
            "docs": r.get("doc_count") or 0,
            "agenda_items": 0,
        })
    return out
