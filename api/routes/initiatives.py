"""Initiatives view — aggregate agenda items by their initiative_code tag.

Initiative codes (CAR-SA, GISWG, etc.) get tagged on agenda items at ingest
time via pipeline/ingest._tag_initiative_codes. This route exposes them as
a first-class cross-meeting view.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pipeline import db
from .. import adapters
from ..auth import current_user

router = APIRouter(prefix="/api/initiatives", tags=["initiatives"])


def _snip(md: str, max_chars: int = 280) -> str:
    """Trim a summary body to a one-paragraph snippet for the drill-in view."""
    if not md:
        return ""
    text = md.strip()
    # Skip leading markdown headings — they're rarely informative inline.
    while text.startswith("#"):
        nl = text.find("\n")
        if nl < 0:
            break
        text = text[nl + 1 :].lstrip()
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


@router.get("")
def list_initiatives(_: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """Return every initiative-tagged code along with item count, meeting
    count, and the most recent meeting date that touched it.

    Ordered by most-recent-touched first so the strategically-interesting
    threads bubble up.
    """
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                SELECT
                    t.id            AS tag_id,
                    t.name          AS code,
                    t.description   AS description,
                    COUNT(DISTINCT ai.id)         AS item_count,
                    COUNT(DISTINCT ai.meeting_id) AS meeting_count,
                    MAX(m.meeting_date)           AS latest_meeting_date
                FROM tags t
                JOIN entity_tags et
                       ON et.tag_id = t.id AND et.entity_type = 'agenda_item'
                JOIN agenda_items ai
                       ON ai.id = et.entity_id
                JOIN meetings m
                       ON m.id = ai.meeting_id
                WHERE t.tag_type = 'initiative'
                GROUP BY t.id, t.name, t.description
                ORDER BY latest_meeting_date DESC NULLS LAST, t.name
                """,
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        d = r.get("latest_meeting_date")
        if d is not None and hasattr(d, "isoformat"):
            r["latest_meeting_date"] = d.isoformat()
    return rows


@router.get("/{code}")
def get_initiative(
    code: str,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Drill-in: every agenda item tagged with this initiative code,
    newest-first, with a one-paragraph summary excerpt + the meeting it
    belongs to.
    """
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM tags WHERE name = %s AND tag_type = 'initiative'",
                (code,),
            )
            tag = cur.fetchone()
            if not tag:
                raise HTTPException(status_code=404, detail="Initiative not found")

            cur.execute(
                """
                SELECT
                    ai.id           AS item_db_id,
                    ai.item_id,
                    ai.title        AS item_title,
                    ai.presenter,
                    ai.org           AS organization,
                    ai.vote_status,
                    m.id            AS meeting_id,
                    m.title         AS meeting_title,
                    m.meeting_date,
                    mt.short_name   AS type_short,
                    mt.name         AS type_name,
                    v.short_name    AS venue,
                    sv.detailed     AS summary_detailed,
                    sv.one_line     AS summary_one_line,
                    sv.status       AS summary_status,
                    sv.version      AS summary_version
                FROM entity_tags et
                JOIN agenda_items ai   ON ai.id = et.entity_id
                JOIN meetings m        ON m.id  = ai.meeting_id
                JOIN meeting_types mt  ON mt.id = m.meeting_type_id
                JOIN venues v          ON v.id  = mt.venue_id
                LEFT JOIN LATERAL (
                    SELECT detailed, one_line, status, version
                      FROM summary_versions
                     WHERE entity_type = 'agenda_item'
                       AND entity_id   = ai.id
                       AND status != 'superseded'
                  ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                           version DESC
                     LIMIT 1
                ) sv ON true
                WHERE et.tag_id = %s
                  AND et.entity_type = 'agenda_item'
                ORDER BY m.meeting_date DESC, ai.seq
                """,
                (tag["id"],),
            )
            rows = [dict(r) for r in cur.fetchall()]

    items = []
    for r in rows:
        d = r.get("meeting_date")
        items.append({
            "meeting_id": r["meeting_id"],
            "meeting_date": d.isoformat() if d is not None and hasattr(d, "isoformat") else d,
            "meeting_title": r.get("meeting_title"),
            "venue": r.get("venue"),
            "type_short": r.get("type_short"),
            "type_name": r.get("type_name"),
            "item_id": r.get("item_id"),
            "item_title": r.get("item_title"),
            "presenter": r.get("presenter"),
            "organization": r.get("organization"),
            "vote_status": r.get("vote_status"),
            "summary_version": r.get("summary_version"),
            "summary_status": r.get("summary_status"),
            "summary_snippet": _snip(
                adapters.resolve_image_refs(
                    r.get("summary_detailed") or r.get("summary_one_line") or ""
                )
            ),
        })

    return {
        "code": dict(tag)["name"],
        "description": dict(tag).get("description"),
        "items": items,
        "item_count": len(items),
        "meeting_count": len({i["meeting_id"] for i in items}),
    }
