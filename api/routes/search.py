"""Full-text search across summary bodies.

Queries the tsvector index added in pipeline/migrations/004_summary_fulltext.sql.
The result rows resolve back to either a meeting briefing or an agenda item,
both of which are reachable by URL from the command palette.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from pipeline import db_new as db
from ..auth import current_user

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
    q = (q or "").strip()
    if not q:
        return []

    # Build the status filter for the CTE. 'approved' is strict; otherwise
    # we use the existing approved-or-draft set.
    if status == "approved":
        status_clause = "status = 'approved'"
    elif status == "draft":
        status_clause = "status = 'draft'"
    else:
        status_clause = "status IN ('draft', 'approved')"

    params: dict[str, Any] = {"q": q, "limit": limit}
    extra_where: list[str] = []

    if from_date is not None:
        extra_where.append("m.meeting_date >= %(from_date)s")
        params["from_date"] = from_date
    if to_date is not None:
        extra_where.append("m.meeting_date <= %(to_date)s")
        params["to_date"] = to_date
    if type_short:
        extra_where.append("mt.short_name = %(type_short)s")
        params["type_short"] = type_short
    if presenter:
        extra_where.append("ai.presenter ILIKE %(presenter_pat)s")
        params["presenter_pat"] = f"%{presenter}%"
    if tag:
        # Match any tag-typed entity_tag on either the meeting or the agenda
        # item the hit belongs to.
        extra_where.append(
            "EXISTS ("
            "  SELECT 1 FROM entity_tags et JOIN tags t ON t.id = et.tag_id "
            "   WHERE t.name = %(tag)s "
            "     AND ((et.entity_type='agenda_item' AND et.entity_id = ai.id) "
            "       OR (et.entity_type='meeting'     AND et.entity_id = m.id))"
            ")"
        )
        params["tag"] = tag

    where_extra_sql = ("AND " + " AND ".join(extra_where)) if extra_where else ""

    sql = f"""
        WITH current_versions AS (
            SELECT DISTINCT ON (entity_type, entity_id)
                id, entity_type, entity_id, detailed, one_line, detailed_tsv
            FROM summary_versions
            WHERE {status_clause}
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
            ai.title          AS item_title,
            ai.presenter      AS presenter,
            ai.org            AS organization
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
        WHERE TRUE
          {where_extra_sql}
        ORDER BY rank DESC, m.meeting_date DESC
        LIMIT %(limit)s
    """

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]

    # Normalize dates to ISO strings.
    for r in rows:
        d = r.get("meeting_date")
        if d is not None and hasattr(d, "isoformat"):
            r["meeting_date"] = d.isoformat()

    return rows
