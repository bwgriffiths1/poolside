"""Initiatives view — aggregate agenda items by their initiative_code tag.

Initiative codes (CAR-SA, GISWG, etc.) get tagged on agenda items at ingest
time via pipeline/ingest._tag_initiative_codes. This route exposes them as
a first-class cross-meeting view, plus a cached synthesized "story so far"
brief per initiative (initiative_briefs row; monthly_roundups pattern — the
UI polls GET /{code} while brief.status == 'generating').
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pipeline import db
from .. import adapters
from ..auth import current_user

log = logging.getLogger("poolside.initiatives")

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


def _iso(v: Any) -> Any:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def brief_is_stale(brief: dict | None, item_count: int,
                   latest_meeting_date: str | None) -> bool:
    """A complete brief is stale once the tagged-item set has moved past its
    generation-time snapshot — more/fewer items, or a newer meeting date."""
    if not brief or brief.get("status") != "complete":
        return False
    src_count = brief.get("source_item_count")
    if src_count is not None and src_count != item_count:
        return True
    src_latest = _iso(brief.get("source_latest_meeting_date"))
    if latest_meeting_date and src_latest and latest_meeting_date > src_latest:
        return True
    return False


def _brief_payload(brief: dict | None, item_count: int,
                   latest_meeting_date: str | None) -> dict[str, Any] | None:
    if brief is None:
        return None
    cost = brief.get("cost_usd")
    return {
        "status": brief.get("status"),
        "brief_md": adapters.resolve_image_refs(brief.get("brief_md") or "")
                    or None,
        "error_message": brief.get("error_message"),
        "model_id": brief.get("model_id"),
        "cost_usd": float(cost) if cost is not None else None,
        "generated_at": _iso(brief.get("generated_at")),
        "source_item_count": brief.get("source_item_count"),
        "source_latest_meeting_date": _iso(brief.get("source_latest_meeting_date")),
        "stale": brief_is_stale(brief, item_count, latest_meeting_date),
    }


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
                    MAX(m.meeting_date)           AS latest_meeting_date,
                    ib.status       AS brief_status
                FROM tags t
                JOIN entity_tags et
                       ON et.tag_id = t.id AND et.entity_type = 'agenda_item'
                JOIN agenda_items ai
                       ON ai.id = et.entity_id
                JOIN meetings m
                       ON m.id = ai.meeting_id
                LEFT JOIN initiative_briefs ib
                       ON ib.tag_id = t.id
                WHERE t.tag_type = 'initiative'
                GROUP BY t.id, t.name, t.description, ib.status
                ORDER BY latest_meeting_date DESC NULLS LAST, t.name
                """,
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["latest_meeting_date"] = _iso(r.get("latest_meeting_date"))
    return rows


@router.get("/{code}")
def get_initiative(
    code: str,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    """Drill-in: every agenda item tagged with this initiative code,
    newest-first, with a one-paragraph summary excerpt + the meeting it
    belongs to — plus the cached brief (and its staleness) when one exists.
    """
    tag = db.get_initiative_tag(code)
    if not tag:
        raise HTTPException(status_code=404, detail="Initiative not found")

    rows = db.get_initiative_items(tag["id"])

    items = []
    for r in rows:
        items.append({
            "meeting_id": r["meeting_id"],
            "meeting_date": _iso(r.get("meeting_date")),
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

    latest = max((i["meeting_date"] for i in items if i["meeting_date"]),
                 default=None)
    brief = db.get_initiative_brief(tag["id"])

    return {
        "code": tag["name"],
        "description": tag.get("description"),
        "items": items,
        "item_count": len(items),
        "meeting_count": len({i["meeting_id"] for i in items}),
        "brief": _brief_payload(brief, len(items), latest),
    }


def _run_brief_job(tag_id: int) -> None:
    """Daemon-thread entry point. run_initiative_brief owns status
    transitions; this wrapper only catches catastrophic failures."""
    try:
        from pipeline.initiative_brief import run_initiative_brief

        run_initiative_brief(tag_id)
    except Exception as e:  # pragma: no cover — belt and braces
        log.exception("initiative brief job %s crashed: %s", tag_id, e)
        try:
            db.update_initiative_brief(tag_id, status="error",
                                       error_message=str(e))
        except Exception:
            log.exception("failed to record crash for brief %s", tag_id)


@router.post("/{code}/brief", status_code=202)
def generate_brief(
    code: str,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Create (or reuse) the brief row for this initiative and kick off
    generation in a daemon thread. Poll GET /api/initiatives/{code} while
    brief.status == 'generating'. Re-posting for an in-flight brief returns
    the running row untouched; posting on a complete/error brief regenerates
    in place.
    """
    tag = db.get_initiative_tag(code)
    if not tag:
        raise HTTPException(status_code=404, detail="Initiative not found")

    items = db.get_initiative_items(tag["id"])
    if not items:
        raise HTTPException(
            status_code=400,
            detail=f"No agenda items tagged {code} — nothing to synthesize.",
        )

    created_by = (user.get("email") if isinstance(user, dict) else None) or "unknown"
    db.ensure_initiative_brief(tag["id"], created_by=created_by)

    claimed = db.claim_initiative_brief(tag["id"])
    if claimed is not None:
        t = threading.Thread(
            target=_run_brief_job,
            args=(tag["id"],),
            name=f"initiative-brief-{tag['id']}",
            daemon=True,
        )
        t.start()

    latest = max((_iso(i.get("meeting_date")) for i in items
                  if i.get("meeting_date")), default=None)
    fresh = db.get_initiative_brief(tag["id"])
    return {
        "code": tag["name"],
        "brief": _brief_payload(fresh, len(items), latest),
    }
