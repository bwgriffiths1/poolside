"""Full-text search across summary bodies.

Thin route over api/services/search.py — the shared retrieval layer also
used by /api/ask. The result rows resolve back to either a meeting briefing
or an agenda item, both of which are reachable by URL from the command
palette.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from pipeline import db
from ..auth import current_user
from ..services.search import search_summary_hits

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/tags")
def list_tags(_: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """All tag names + types, for the search-filter dropdown.

    Ordered: initiatives first (most useful for cross-meeting filtering),
    then everything else alphabetically.
    """
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT name, tag_type
                     FROM tags
                    WHERE EXISTS (
                        SELECT 1 FROM entity_tags et WHERE et.tag_id = tags.id
                    )
                 ORDER BY CASE tag_type WHEN 'initiative' THEN 0 ELSE 1 END,
                          name"""
            )
            return [dict(r) for r in cur.fetchall()]


@router.get("/summaries")
def search_summaries(
    q: str = Query("", description="search terms; uses Postgres websearch_to_tsquery"),
    limit: int = Query(15, ge=1, le=200),
    from_date: date | None = Query(None, description="meeting_date >= this (ISO)"),
    to_date: date | None = Query(None, description="meeting_date <= this (ISO)"),
    type_short: str | None = Query(None, description="restrict to one committee"),
    tag: str | None = Query(None, description="hit must be tagged with this name"),
    presenter: str | None = Query(None, description="case-insensitive substring on item presenter"),
    status: str | None = Query(None, description="'approved' or 'draft' to restrict by summary state"),
    _: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    """Return ranked summary hits.

    Each result is shaped:
        {
          "entity_type":  "meeting" | "agenda_item",
          "entity_id":    int,
          "meeting_id":   int,
          "meeting_title": str,
          "meeting_date": str,
          "venue":         str,
          "type_short":    str,
          "item_id":       str | null,   # only for agenda_item hits
          "item_title":    str | null,
          "snippet":       str,
        }
    """
    return search_summary_hits(
        q,
        limit=limit,
        from_date=from_date,
        to_date=to_date,
        type_short=type_short,
        tag=tag,
        presenter=presenter,
        status=status,
    )
