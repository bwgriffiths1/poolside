"""Adapters from pipeline/db.py rows to API response shapes.

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
    from pipeline import db

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


def _briefing_doc(row: dict[str, Any], item_id: str, item_title: str) -> schemas.BriefingDoc:
    filename = row.get("filename") or row.get("original_filename") or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return schemas.BriefingDoc(
        id=row["id"],
        filename=filename,
        type=ext,
        source_url=row.get("source_url"),
        ceii=bool(row.get("is_ceii")),
        item_id=item_id,
        item=item_title,
    )


# Leading item number on a sub-heading inside a section body, e.g.
# "7.a — Resource Qualification Process Follow Up". Sub-items are written as
# headings within their parent's body rather than as their own sections, so
# this is how their materials find the right anchor.
_SUBHEAD_ITEM_ID = re.compile(r"^(\d+(?:\.[0-9A-Za-z]+)+)\s*[:—–\-]")


def _norm_item_id(item_id: str) -> str:
    """Agenda writes '1.A', the briefing writes '1.a' — compare case-blind."""
    return item_id.strip().lower()


def _owning_anchor(item_id: str, anchor_ids: list[str]) -> str | None:
    """Which heading should list a document filed under `item_id`?

    Exact match wins, so 7.a's materials sit under the 7.a sub-heading rather
    than piling up on section 7. Otherwise the document rolls up to the
    nearest ancestor the briefing actually wrote up — 7.a.ii lands on 7.a if
    that heading exists, else on 7. Sub-items the briefing skipped therefore
    still surface their materials instead of vanishing into the bottom list.
    """
    if item_id in anchor_ids:
        return item_id
    ancestors = [a for a in anchor_ids if a and item_id.startswith(a + ".")]
    return max(ancestors, key=len) if ancestors else None


def attach_briefing_docs(briefing: schemas.Briefing, meeting_id: int) -> None:
    """Distribute a meeting's documents across the parsed briefing headings.

    Mutates `briefing` in place. Every numbered heading is an anchor — the
    sections themselves plus the numbered sub-headings inside their bodies —
    and each document is filed under the closest one, so materials sit with
    the discussion they back. Anything matching no heading (meeting-level
    files, agenda items the briefing skipped) lands in `.other_docs`.

    Every renderer — web reader, public share, Word export — consumes this one
    distribution so the three cannot drift.
    """
    from pipeline import db

    anchors: dict[str, Any] = {}
    for section in briefing.sections:
        section.docs = []
        if section.item_id:
            anchors.setdefault(_norm_item_id(section.item_id), section)
        for block in section.body:
            if getattr(block, "kind", "") != "h":
                continue
            block.docs = []
            m = _SUBHEAD_ITEM_ID.match(block.text or "")
            if not m:
                continue
            block.item_id = m.group(1)
            anchors.setdefault(_norm_item_id(block.item_id), block)

    anchor_ids = list(anchors)
    others: list[schemas.BriefingDoc] = []

    for ar in db.get_agenda_items(meeting_id):
        item_id = str(ar.get("item_id") or "")
        item_title = ar.get("title") or ""
        owner = _owning_anchor(_norm_item_id(item_id), anchor_ids) if item_id else None
        target = anchors[owner].docs if owner else others
        for row in db.get_documents_for_item(ar["id"]):
            target.append(_briefing_doc(row, item_id, item_title))

    # A document assigned to several agenda items can reach one anchor twice.
    for holder in (*anchors.values(), briefing):
        docs = holder.docs if holder is not briefing else others
        seen: set[int] = set()
        deduped = [d for d in docs if not (d.id in seen or seen.add(d.id))]
        if holder is briefing:
            briefing.other_docs = deduped
        else:
            holder.docs = deduped


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


# ── Monthly roundups ────────────────────────────────────────────────────


def _month_key(d: Any) -> str:
    """DATE (first-of-month) → 'YYYY-MM'."""
    return str(d)[:7]


def _month_label(d: Any) -> str:
    """DATE (first-of-month) → 'June 2026'."""
    if isinstance(d, date):
        return d.strftime("%B %Y")
    try:
        parsed = date.fromisoformat(str(d)[:10])
        return parsed.strftime("%B %Y")
    except ValueError:
        return str(d)


def roundup_source_row(row: dict[str, Any]) -> schemas.RoundupSource:
    return schemas.RoundupSource(
        meeting_id=row["id"],
        type_short=row.get("type_short") or "",
        type_name=row.get("type_name") or "",
        meeting_date=_iso(row.get("meeting_date")) or "",
        end_date=_iso(row.get("end_date")),
        title=row.get("title") or "",
    )


def roundup_row(row: dict[str, Any],
                sources: list[dict[str, Any]] | None = None,
                include_report: bool = True) -> schemas.Roundup:
    return schemas.Roundup(
        id=row["id"],
        venue=row.get("venue_short") or "",
        month=_month_key(row["month"]),
        month_label=_month_label(row["month"]),
        status=row.get("status") or "draft",
        model_id=row.get("model_id"),
        report_md=(row.get("report_md") if include_report else None),
        error_message=row.get("error_message"),
        progress_text=row.get("progress_text"),
        input_tokens=row.get("input_tokens"),
        output_tokens=row.get("output_tokens"),
        cost_usd=(float(row["cost_usd"]) if row.get("cost_usd") is not None else None),
        created_by=row.get("created_by"),
        created_at=_iso(row.get("created_at")) or "",
        updated_at=_iso(row.get("updated_at")) or "",
        sources=[roundup_source_row(s) for s in (sources or [])],
    )


def roundup_month_row(month_row: dict[str, Any] | None,
                      roundup: dict[str, Any] | None) -> schemas.RoundupMonth:
    """Merge a list_roundup_months row with its roundup row (either may be
    None — a roundup can outlive its briefings, and most months have no
    roundup yet)."""
    month_val = (month_row or {}).get("month") or (roundup or {}).get("month")
    return schemas.RoundupMonth(
        month=_month_key(month_val),
        month_label=_month_label(month_val),
        briefing_count=int((month_row or {}).get("briefing_count") or 0),
        committees=sorted((month_row or {}).get("committees") or []),
        roundup=(roundup_row(roundup, include_report=False) if roundup else None),
    )
