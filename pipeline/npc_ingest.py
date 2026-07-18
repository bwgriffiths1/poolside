"""
pipeline/npc_ingest.py — Ingest an NPC meeting from combined PDFs.

NPC meetings distribute materials as combined PDFs (initial, supplemental,
composite) that bundle multiple documents into one file. This module:

1. Selects the best combined PDF available (composite > supplemental > initial)
2. Parses it into sections via bookmark structure
3. Creates virtual document rows for each section (with pre-populated raw_content)
4. Parses the agenda into structured items
5. Maps both combined-PDF sections and standalone documents to agenda items

Entry point:  ingest_npc_meeting(meeting_dict, doc_list, config)
"""
import logging
import re
from pathlib import Path

import requests

import pipeline.db as db
from pipeline.agenda_parser import map_docs_to_agenda_items
from pipeline.npc_combined_parser import (
    CombinedSection,
    build_agenda_from_sections,
    parse_agenda_section,
    parse_combined_pdf,
)
from pipeline.summarizer import summarize_agenda_item, summarize_meeting

logger = logging.getLogger(__name__)

# Patterns for identifying combined PDF variants (order = priority)
_COMBINED_PDF_PATTERNS = [
    ("composite", re.compile(r"composite", re.IGNORECASE)),
    ("supplemental", re.compile(r"suppl", re.IGNORECASE)),
    ("initial", re.compile(r"initial", re.IGNORECASE)),
]

# Section types that should be marked as ignored (not summarized)
_SKIP_SECTION_TYPES = {"minutes", "notice"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_combined_pdf(doc_list: list[dict]) -> dict | None:
    """
    Find the best combined PDF from the document list.

    Prefers composite > supplemental > initial. Returns the doc dict or None.
    """
    for _label, pattern in _COMBINED_PDF_PATTERNS:
        for doc in doc_list:
            fn = doc.get("filename", "")
            if fn.lower().endswith(".pdf") and pattern.search(fn):
                return doc
    return None


def _resolve_meeting_type(committee_short: str,
                           venue_short: str = "ISO-NE") -> int | None:
    types = db.get_meeting_types(venue_short_name=venue_short)
    for mt in types:
        if mt["short_name"].upper() == committee_short.upper():
            return mt["id"]
    logger.error("No meeting_type found for %s / %s", committee_short, venue_short)
    return None


def _download_bytes(url: str, session: requests.Session | None = None) -> bytes | None:
    sess = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = sess.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Failed to download %s: %s", url, exc)
        return None


def _slugify(text: str) -> str:
    """Create a filename-safe slug from text."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:60]


def _item_depth(item_id: str) -> int:
    """Depth from item_id: '7'→0, '2A'→0, '2A.sub'→1."""
    return len(item_id.split(".")) - 1


def _parent_item_id(item_id: str) -> str | None:
    parts = item_id.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_npc_meeting(
    meeting_dict: dict,
    doc_list: list[dict],
    config: dict,
    venue_short: str = "ISO-NE",
    overwrite: bool = True,
    session: requests.Session | None = None,
) -> int | None:
    """
    Ingest an NPC meeting from a combined PDF into the database.

    Returns the meetings.id of the upserted row, or None on failure.
    """
    committee_short = meeting_dict.get("committee_short", "NPC")
    event_id = meeting_dict.get("primary_event_id")
    dates = meeting_dict.get("dates", [])
    meeting_date = str(dates[0]) if dates else None

    if not meeting_date:
        logger.error("ingest_npc_meeting: no dates in meeting_dict")
        return None

    meeting_type_id = _resolve_meeting_type(committee_short, venue_short)
    if meeting_type_id is None:
        return None

    # ── 1. Upsert meeting ───────────────────────────────────────────────
    end_date = str(dates[-1]) if len(dates) > 1 else None
    meeting_row = db.upsert_meeting(
        meeting_type_id=meeting_type_id,
        meeting_date=meeting_date,
        end_date=end_date,
        external_id=event_id,
        title=meeting_dict.get("title"),
        meeting_number=meeting_dict.get("meeting_number"),
        location=meeting_dict.get("location"),
    )
    meeting_id = meeting_row["id"]
    logger.info("Meeting upserted: id=%s  NPC %s", meeting_id, meeting_date)

    if overwrite:
        db.clear_agenda_for_meeting(meeting_id)
        logger.info("  Cleared existing agenda for re-ingest")

    # ── 2. Upsert all original documents ────────────────────────────────
    doc_db_rows: list[dict] = []
    for doc in doc_list:
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        ceii = doc.get("ceii", False)
        row = db.upsert_document(
            meeting_id=meeting_id,
            filename=filename,
            file_type=ext,
            source_url=doc.get("url"),
            ceii_skipped=ceii,
        )
        doc_db_rows.append({"db_row": row, "filename": filename, "url": doc.get("url")})
    logger.info("  %d original document(s) upserted", len(doc_db_rows))

    # ── 3. Download and parse the best combined PDF ─────────────────────
    combined_doc = find_combined_pdf(doc_list)
    sections: list[CombinedSection] = []
    pdf_bytes: bytes | None = None

    if combined_doc:
        logger.info("  Downloading combined PDF: %s", combined_doc["filename"])
        pdf_bytes = _download_bytes(combined_doc.get("url", ""), session)
        if pdf_bytes:
            sections = parse_combined_pdf(pdf_bytes)
            logger.info("  Parsed %d section(s) from combined PDF", len(sections))
        else:
            logger.warning("  Could not download combined PDF")
    else:
        logger.warning("  No combined PDF found in doc list")

    # ── 4. Create virtual document rows for combined PDF sections ───────
    # Maps item_number → list of document DB ids for section-to-item mapping
    section_doc_map: dict[str, list[int]] = {}

    for s in sections:
        if s.is_tba or not s.text.strip():
            continue

        slug = _slugify(s.clean_title)
        synthetic_filename = f"_combined_{s.item_number or 'x'}_{slug}.pdf"
        ignored = s.section_type in _SKIP_SECTION_TYPES

        row = db.upsert_document(
            meeting_id=meeting_id,
            filename=synthetic_filename,
            file_type=".pdf",
            source_url=combined_doc.get("url") if combined_doc else None,
        )
        doc_id = row["id"]

        # Pre-populate raw_content so summarizer skips download
        db.set_document_raw_content(doc_id, s.text)

        if ignored:
            db.set_document_ignored(doc_id, True)
            logger.debug("  Section %s (%s) marked ignored", s.item_number, s.section_type)

        # Track for section-to-item mapping
        if s.item_number and not ignored:
            # For sub_documents, map to parent's item number
            map_key = s.parent_number if s.section_type == "sub_document" and s.parent_number else s.item_number
            section_doc_map.setdefault(map_key, []).append(doc_id)

    virtual_count = sum(1 for s in sections if not s.is_tba and s.text.strip())
    logger.info("  %d virtual section document(s) created", virtual_count)

    # ── 5. Parse agenda ─────────────────────────────────────────────────
    parsed_items: list[dict] = []
    agenda_sections = [s for s in sections if s.section_type == "agenda"]

    if agenda_sections and pdf_bytes:
        a = agenda_sections[0]
        parsed_items = parse_agenda_section(pdf_bytes, a.start_page, a.end_page)
        logger.info("  Parsed %d agenda item(s) from agenda section", len(parsed_items))

    if len(parsed_items) < 3:
        logger.info("  Agenda section yielded %d item(s), using bookmark fallback", len(parsed_items))
        fallback = build_agenda_from_sections(sections)
        if len(fallback) > len(parsed_items):
            parsed_items = fallback

    # ── 6. Insert agenda items ──────────────────────────────────────────
    # Items kept by clear_agenda_for_meeting (approved/manual summaries)
    # must not be re-inserted; reuse their row ids.
    item_id_map: dict[str, int] = {}  # item_id → DB row id
    _existing = {
        r["item_id"]: r["id"]
        for r in db.get_agenda_items(meeting_id)
        if r.get("item_id")
    }

    for seq, item in enumerate(parsed_items):
        raw_item_id = item["item_id"]
        if raw_item_id in _existing:
            item_id_map[raw_item_id] = _existing[raw_item_id]
            continue
        depth = _item_depth(raw_item_id)
        parent_raw = _parent_item_id(raw_item_id)
        parent_db_id = item_id_map.get(parent_raw) if parent_raw else None

        row = db.insert_agenda_item(
            meeting_id=meeting_id,
            title=item["title"],
            seq=seq,
            depth=depth,
            parent_id=parent_db_id,
            item_id=raw_item_id,
            prefix=item.get("prefix"),
            auto_sub=item.get("auto_sub", False),
            presenter=item.get("presenter"),
            org=item.get("org"),
            vote_status=item.get("vote_status"),
            wmpp_id=item.get("wmpp_id"),
            time_slot=item.get("time_slot"),
            notes=item.get("notes"),
        )
        item_id_map[raw_item_id] = row["id"]
        logger.debug("  inserted agenda item %s: %s", raw_item_id, item["title"])

    db.ensure_agenda_hierarchy(meeting_id)
    logger.info("  %d agenda item(s) inserted", len(item_id_map))

    # ── 7. Map combined PDF sections to agenda items ────────────────────
    section_assigned = 0
    for item_num_key, doc_ids in section_doc_map.items():
        # Find matching agenda item — try exact match, then numeric prefix
        item_db_id = item_id_map.get(item_num_key)
        if not item_db_id:
            # Try matching just the numeric part (e.g., "2A" section → item "2")
            num_match = re.match(r"^(\d+)", item_num_key)
            if num_match:
                item_db_id = item_id_map.get(num_match.group(1))
        if item_db_id:
            for doc_db_id in doc_ids:
                db.assign_document_to_item(item_db_id, doc_db_id)
                section_assigned += 1

    logger.info("  %d section-to-item assignment(s)", section_assigned)

    # ── 8. Map standalone documents to agenda items ─────────────────────
    if parsed_items and doc_db_rows:
        # Filter to non-combined-PDF docs
        standalone_docs = [
            d for d in doc_db_rows
            if not d["filename"].startswith("_combined_")
        ]
        if standalone_docs:
            simple_doc_rows = [{"filename": d["filename"]} for d in standalone_docs]
            doc_db_by_filename = {d["filename"]: d["db_row"]["id"] for d in standalone_docs}
            prefix_to_item_db_id = {
                item["prefix"]: item_id_map[item["item_id"]]
                for item in parsed_items
                if item.get("prefix") and item["item_id"] in item_id_map
            }

            buckets = map_docs_to_agenda_items(simple_doc_rows, parsed_items)
            standalone_assigned = 0
            for prefix, docs_in_bucket in buckets.items():
                if prefix == "other":
                    continue
                item_db_id = prefix_to_item_db_id.get(prefix)
                if not item_db_id:
                    continue
                for doc_row in docs_in_bucket:
                    doc_db_id = doc_db_by_filename.get(doc_row["filename"])
                    if doc_db_id:
                        db.assign_document_to_item(item_db_id, doc_db_id)
                        standalone_assigned += 1
            logger.info("  %d standalone document-item assignment(s)", standalone_assigned)

    # ── 9. Create stub summaries ────────────────────────────────────────
    for item_id_str, db_id in item_id_map.items():
        try:
            summarize_agenda_item(db_id, created_by="ingest")
        except Exception as exc:
            logger.warning("  stub failed for item %s: %s", item_id_str, exc)

    try:
        summarize_meeting(meeting_id, created_by="ingest")
    except Exception as exc:
        logger.warning("  meeting stub failed: %s", exc)

    db.set_meeting_status(meeting_id, "complete")
    logger.info("  Ingest complete: meeting_id=%s", meeting_id)
    return meeting_id
