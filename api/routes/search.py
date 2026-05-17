"""Full-text search across summary bodies.

Queries the tsvector index added in pipeline/migrations/004_summary_fulltext.sql.
The result rows resolve back to either a meeting briefing or an agenda item,
both of which are reachable by URL from the command palette.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from pipeline import db_new as db
from ..auth import current_user

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/summaries")
def search_summaries(
    q: str = Query("", description="search terms; uses Postgres websearch_to_tsquery"),
    limit: int = Query(15, ge=1, le=50),
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
    q = (q or "").strip()
    if not q:
        return []

    sql = """
        WITH current_versions AS (
            SELECT DISTINCT ON (entity_type, entity_id)
                id, entity_type, entity_id, detailed, one_line, detailed_tsv
            FROM summary_versions
            WHERE status IN ('draft', 'approved')
              AND detailed_tsv @@ websearch_to_tsquery('english', %(q)s)
            ORDER BY entity_type, entity_id,
                CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                version DESC
        )
        SELECT
            cv.entity_type,
            cv.entity_id,
            ts_rank_cd(cv.detailed_tsv, websearch_to_tsquery('english', %(q)s)) AS rank,
            ts_headline(
                'english',
                COALESCE(cv.detailed, cv.one_line, ''),
                websearch_to_tsquery('english', %(q)s),
                'MaxFragments=1, MaxWords=22, MinWords=10, ShortWord=2'
            ) AS snippet,
            m.id              AS meeting_id,
            m.title           AS meeting_title,
            m.meeting_date    AS meeting_date,
            v.short_name      AS venue,
            mt.short_name     AS type_short,
            ai.item_id        AS item_id,
            ai.title          AS item_title
        FROM current_versions cv
        LEFT JOIN agenda_items ai
               ON cv.entity_type = 'agenda_item' AND ai.id = cv.entity_id
        JOIN meetings m
          ON m.id = CASE
                       WHEN cv.entity_type = 'meeting' THEN cv.entity_id
                       ELSE ai.meeting_id
                    END
        JOIN meeting_types mt ON mt.id = m.meeting_type_id
        JOIN venues v         ON v.id  = mt.venue_id
        ORDER BY rank DESC, m.meeting_date DESC
        LIMIT %(limit)s
    """

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(sql, {"q": q, "limit": limit})
            rows = [dict(r) for r in cur.fetchall()]

    # Normalize dates to ISO strings.
    for r in rows:
        d = r.get("meeting_date")
        if d is not None and hasattr(d, "isoformat"):
            r["meeting_date"] = d.isoformat()

    return rows
