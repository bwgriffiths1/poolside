"""Ranked full-text retrieval over summary bodies.

The one implementation of "find summaries matching a query", shared by the
/api/search endpoints (command palette + Search screen) and /api/ask
(retrieval for cited Q&A). Queries the tsvector index from migration 004.

Each hit resolves to a meeting briefing or an agenda item; snippets come
back HTML-safe (escaped, with <b> highlight tags) because both consumers
render them with dangerouslySetInnerHTML.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

from pipeline import db

# Words that carry no retrieval signal for the OR-relaxed fallback query.
_STOPWORDS = frozenset(
    "the a an of for on in to and or is are was were be been what when where"
    " who whom how why which does do did has have had latest status stand"
    " stands standing about with between current recent update updates news"
    " tell me show".split()
)


def or_query(question: str) -> str:
    """Relax a natural-language question into `term or term or ...`.

    websearch_to_tsquery ANDs plain terms, so a full question usually
    matches nothing; OR-ing the substantive terms is the recall fallback.
    """
    terms = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]+", question)
             if t.lower() not in _STOPWORDS]
    # De-dup preserving order.
    seen: set[str] = set()
    keep = []
    for t in terms:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            keep.append(t)
    return " or ".join(keep)


def search_summary_hits(
    q: str,
    limit: int = 15,
    from_date: date | None = None,
    to_date: date | None = None,
    type_short: str | None = None,
    tag: str | None = None,
    presenter: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Ranked summary hits for a websearch-syntax query. See module docstring."""
    q = (q or "").strip()
    if not q:
        return []

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
                -- Highlight with inert markers, NOT <b> tags: the source text
                -- is scraped/user-edited markdown, and the frontend renders
                -- this snippet as HTML. We escape it in Python below and only
                -- then turn the markers into real <b> tags.
                'StartSel=@@HLS@@, StopSel=@@HLE@@, MaxFragments=1, MaxWords=22, MinWords=10, ShortWord=2'
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

    # Normalize dates to ISO strings, and make snippets safe to render as
    # HTML: escape everything, then convert the highlight markers to <b>.
    for r in rows:
        d = r.get("meeting_date")
        if d is not None and hasattr(d, "isoformat"):
            r["meeting_date"] = d.isoformat()
        snippet = html.escape(r.get("snippet") or "", quote=False)
        r["snippet"] = snippet.replace("@@HLS@@", "<b>").replace("@@HLE@@", "</b>")

    return rows


def retrieve_for_question(question: str, limit: int = 12,
                          **filters: Any) -> list[dict[str, Any]]:
    """Retrieval for Q&A: strict websearch first, OR-relaxed fallback when
    the AND semantics leave fewer than 3 hits. De-duped, rank order kept."""
    hits = search_summary_hits(question, limit=limit, **filters)
    if len(hits) < 3:
        relaxed = or_query(question)
        if relaxed and relaxed.lower() != question.strip().lower():
            seen = {(h["entity_type"], h["entity_id"]) for h in hits}
            for h in search_summary_hits(relaxed, limit=limit, **filters):
                key = (h["entity_type"], h["entity_id"])
                if key not in seen and len(hits) < limit:
                    seen.add(key)
                    hits.append(h)
    return hits
