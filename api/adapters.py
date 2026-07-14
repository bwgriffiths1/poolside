"""Adapters from pipeline/db_new.py rows to API response shapes.

Single source of truth for status derivation and field mapping.
"""
from __future__ import annotations

import re
from typing import Any
from datetime import date

from . import schemas


# The summarizer embeds extracted figures as marker comments like
# `<!-- image_id:441 -->`. The frontend renders markdown but doesn't know
# how to resolve those — so we rewrite each into a standard image syntax
# pointing at /api/images/N. The image route serves the PNG bytes.
_IMAGE_REF_RE = re.compile(r"<!--\s*image_id:(\d+)\s*-->")


def resolve_image_refs(md: str) -> str:
    """Replace `<!-- image_id:N -->` comments with `![figure](/api/images/N)`
    so the rendered markdown picks them up as inline images.
    """
    if not md or "<!--" not in md:
        return md or ""
    return _IMAGE_REF_RE.sub(
        lambda m: f"![figure {m.group(1)}](/api/images/{m.group(1)})", md
    )


# Map the explicit DB lifecycle_status enum to the frontend's pill statuses.
_LIFECYCLE_TO_PILL = {
    "discovered": "scheduled",
    "agenda_posted": "scheduled",      # agenda but no docs yet — still "scheduled"-looking
    "materials_posted": "materials",
    "summarized": "summarized",
    "approved": "updated",
}


def derive_status(row: dict[str, Any]) -> schemas.MeetingStatus:
    """Map the meeting's lifecycle_status to the four-state UI pill.

    Prefer the explicit DB column when it's set (post-migration 001);
    fall back to deriving from observed data for rows still at the default.
    """
    lc = row.get("lifecycle_status")
    if lc and lc in _LIFECYCLE_TO_PILL:
        return _LIFECYCLE_TO_PILL[lc]  # type: ignore[return-value]
    # Legacy fallback — same rules as before migration 001.
    if row.get("has_manual"):
        return "updated"
    if row.get("has_summary"):
        return "summarized"
    if (row.get("doc_count") or 0) > 0:
        return "materials"
    return "scheduled"


def _iso(d: Any) -> str | None:
    if d is None:
        return None
    if isinstance(d, str):
        return d
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def meeting_list_row(row: dict[str, Any], tags: list[str] | None = None,
                     item_count: int | None = None) -> schemas.MeetingListItem:
    lc = row.get("lifecycle_status") or "discovered"
    return schemas.MeetingListItem(
        id=row["id"],
        venue=row["venue_short"],
        type_short=row["type_short"],
        type_name=row["type_name"],
        title=row["title"] or "",
        meeting_date=_iso(row["meeting_date"]) or "",
        end_date=_iso(row.get("end_date")),
        location=row.get("location") or "",
        external_id=row.get("external_id") or "",
        status=derive_status(row),
        lifecycle_status=lc,
        last_scraped_at=_iso(row.get("last_scraped_at")),
        agenda_parsed_at=_iso(row.get("agenda_parsed_at")),
        doc_count=row.get("doc_count") or 0,
        unassigned_doc_count=row.get("unassigned_doc_count") or 0,
        item_count=item_count if item_count is not None else (row.get("item_count") or 0),
        tags=tags or [],
    )


def materialize_missing_parents(meeting_id: int) -> int:
    """Normalize the meeting's agenda tree: create placeholder parents for
    orphaned sub-items, link parent_id/depth, and order parents immediately
    before their children. Idempotent; also self-heals meetings ingested
    before the hierarchy pass existed (late-appended parents at the end of
    the agenda). Returns the number of parents created."""
    from pipeline import db_new as db

    return db.ensure_agenda_hierarchy(meeting_id)


def synthesize_missing_parents(items: list[schemas.AgendaItem]) -> list[schemas.AgendaItem]:
    """Legacy shim — kept so callers still compile while we transition.

    The new approach materializes parents in the DB (see materialize_missing_parents
    above), so this function should be a no-op once meetings have been migrated.
    For now it returns the input unchanged.
    """
    return items


def document_row(row: dict[str, Any]) -> schemas.DocumentRef:
    filename = row.get("filename") or row.get("original_filename") or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return schemas.DocumentRef(
        id=row["id"],
        filename=filename,
        type=ext,
        assigned=bool(row.get("agenda_item_id")),
        ceii=bool(row.get("is_ceii")),
        source_url=row.get("source_url"),
        manual=bool(row.get("manual")),
    )


def _first_sentence(text: str, max_len: int = 180) -> str:
    """Pull a one-liner preview from the start of a markdown body."""
    if not text:
        return ""
    # Strip leading markdown noise (headings, blockquotes) and bullet markers.
    cleaned: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if cleaned:
                break
            continue
        if s.startswith("#"):
            continue
        if s.startswith(">"):
            s = s.lstrip(">").strip()
        if s.startswith(("- ", "* ")):
            s = s[2:]
        # Strip bold/italic markers for the preview.
        s = s.replace("**", "").replace("__", "")
        cleaned.append(s)
        if len(" ".join(cleaned)) >= max_len:
            break
    joined = " ".join(cleaned)
    if not joined:
        return ""
    # Take up to the first sentence terminator.
    import re as _re
    m = _re.search(r"[.!?](\s|$)", joined)
    if m and m.start() < max_len:
        return joined[: m.start() + 1]
    if len(joined) > max_len:
        return joined[: max_len - 1].rstrip() + "…"
    return joined


def agenda_item_row(row: dict[str, Any], docs: list[schemas.DocumentRef],
                    summary: dict[str, Any] | None,
                    initiative_codes: list[str] | None = None) -> schemas.AgendaItem:
    item_id = row.get("item_id") or ""
    depth = item_id.count(".") if item_id else 0
    s = summary or {}
    one_line = (s.get("one_line") or "").strip()
    detailed = resolve_image_refs(s.get("detailed") or "")
    # Fall back to deriving a preview from the detailed body when one_line is empty.
    if not one_line and detailed:
        one_line = _first_sentence(detailed)
    return schemas.AgendaItem(
        id=row["id"],
        item_id=item_id,
        depth=depth,
        title=row.get("title") or "",
        presenter=row.get("presenter"),
        org=row.get("organization"),
        time_slot=row.get("time_slot"),
        vote_status=row.get("vote_status"),
        has_summary=summary is not None,
        wmpp_id=row.get("wmpp_id"),
        docs=docs,
        one_line=one_line,
        detailed=detailed,
        summary_version=s.get("version"),
        summary_status=s.get("status"),
        summary_updated_at=_iso(s.get("created_at")),
        summary_is_manual=bool(s.get("is_manual")),
        initiative_codes=initiative_codes or [],
    )
