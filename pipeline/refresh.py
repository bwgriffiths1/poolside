"""
pipeline/refresh.py — Incremental document refresh for existing meetings.

Meeting materials are added over time (agenda first, then presentations,
reports, etc.). This module detects new documents, maps them to existing
agenda items, and identifies which items need re-summarization.

Entry points:
  - refresh_meeting_documents(meeting_id, config) → RefreshResult
  - refresh_all_upcoming(config, days_ahead, days_back) → list[RefreshResult]
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

import pipeline.db_new as db
from pipeline.agenda_parser import map_docs_to_agenda_items
from pipeline.scraper import fetch_event_docs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RefreshResult:
    meeting_id: int
    meeting_label: str = ""         # e.g. "NPC Apr 9, 2026"
    new_docs: list[dict] = field(default_factory=list)
    affected_item_ids: set[int] = field(default_factory=set)
    unassigned_docs: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_new(self) -> bool:
        return len(self.new_docs) > 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _load_config() -> dict:
    try:
        with open("config.yaml") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


# ---------------------------------------------------------------------------
# Core: refresh documents for a single meeting
# ---------------------------------------------------------------------------

def refresh_meeting_documents(
    meeting_id: int,
    config: dict | None = None,
    session: requests.Session | None = None,
) -> RefreshResult:
    """
    Check for new documents on a meeting's source and map them to agenda items.

    1. Re-scrapes the document list from the source (ISO-NE event API)
    2. Compares against existing documents in the DB
    3. Upserts genuinely new documents
    4. Maps new docs to existing agenda items (prefix matching + LLM)
    5. For NPC: handles combined PDF upgrades (initial → supplemental → composite)

    Returns a RefreshResult with new docs, affected items, and any errors.
    """
    config = config or _load_config()
    result = RefreshResult(meeting_id=meeting_id)

    # Load meeting metadata
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        result.errors.append(f"Meeting {meeting_id} not found in database")
        return result

    venue_short = meeting.get("venue_short", "ISO-NE")
    type_short = meeting.get("type_short", "")
    external_id = meeting.get("external_id")
    meeting_date = meeting.get("meeting_date", "")
    result.meeting_label = f"{type_short} {meeting_date}"

    if not external_id:
        result.errors.append(f"Meeting {meeting_id} has no external_id — cannot scrape")
        return result

    # Get existing filenames
    existing_filenames = db.get_existing_filenames(meeting_id)

    # Scrape current doc list from source. Only ISO-NE has a live scraper —
    # NYISO support was removed 2026-07 after sitting unreachable since the
    # Streamlit UI was retired. Historical non-ISO-NE rows stay readable;
    # they just can't be refreshed.
    scraped_docs: list[dict] = []
    if venue_short != "ISO-NE":
        result.errors.append(
            f"Venue {venue_short} has no active scraper — refresh skipped"
        )
        return result
    try:
        scraped_docs = fetch_event_docs(str(external_id), session=session)
    except Exception as exc:
        result.errors.append(f"Scrape failed: {exc}")
        return result

    if not scraped_docs:
        logger.info("  No documents returned from scrape for meeting %s", meeting_id)
        return result

    # Identify genuinely new documents
    new_docs = [d for d in scraped_docs if d["filename"] not in existing_filenames]

    if not new_docs:
        logger.info("  No new documents for %s %s", type_short, meeting_date)
        return result

    logger.info("  Found %d new document(s) for %s %s", len(new_docs), type_short, meeting_date)

    # Upsert new documents
    new_doc_rows: list[dict] = []
    for doc in new_docs:
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        row = db.upsert_document(
            meeting_id=meeting_id,
            filename=filename,
            file_type=ext,
            source_url=doc.get("url"),
            ceii_skipped=doc.get("ceii", False),
        )
        new_doc_rows.append({
            "db_row": row,
            "filename": filename,
            "url": doc.get("url"),
        })

    # For NPC: check if a combined PDF upgrade is needed
    if type_short.upper() == "NPC":
        npc_new = _handle_npc_combined_refresh(
            meeting_id, new_docs, existing_filenames, config, session,
        )
        new_doc_rows.extend(npc_new.get("virtual_docs", []))
        result.errors.extend(npc_new.get("errors", []))

    # Map new documents to existing agenda items
    agenda_items = db.get_agenda_items(meeting_id)
    if not agenda_items:
        # No agenda items yet — docs stay unassigned
        for d in new_doc_rows:
            result.new_docs.append({
                "filename": d["filename"],
                "doc_db_id": d["db_row"]["id"],
                "assigned_to_item": None,
            })
            result.unassigned_docs.append(d)
        return result

    # Build mapping structures
    simple_doc_rows = [{"filename": d["filename"]} for d in new_doc_rows]
    doc_db_by_filename = {d["filename"]: d["db_row"]["id"] for d in new_doc_rows}
    prefix_to_item_db_id = {
        item["prefix"]: item["id"]
        for item in agenda_items
        if item.get("prefix")
    }
    item_id_to_db_id = {
        item["item_id"]: item["id"]
        for item in agenda_items
        if item.get("item_id")
    }

    # Step 1: regex prefix matching
    buckets = map_docs_to_agenda_items(simple_doc_rows, agenda_items)
    assigned = 0

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
                assigned += 1
                result.affected_item_ids.add(item_db_id)
                result.new_docs.append({
                    "filename": doc_row["filename"],
                    "doc_db_id": doc_db_id,
                    "assigned_to_item": item_db_id,
                })

    # Step 2: LLM matching for unassigned
    unassigned = [r["filename"] for r in buckets.get("other", [])]
    parse_mode = config.get("agenda_parsing", {}).get("mode", "regex_only")
    if unassigned and parse_mode != "regex_only":
        try:
            from pipeline.llm_agenda_parser import llm_match_docs
            match_model = config.get("agenda_parsing", {}).get(
                "match_model", "claude-haiku-4-5-20251001",
            )
            llm_buckets = llm_match_docs(
                agenda_items, unassigned, venue_short, model=match_model,
            )
            for prefix, docs_in_bucket in llm_buckets.items():
                if prefix == "other":
                    continue
                item_db_id = prefix_to_item_db_id.get(prefix)
                if not item_db_id:
                    continue
                for doc_row in docs_in_bucket:
                    doc_db_id = doc_db_by_filename.get(doc_row["filename"])
                    if doc_db_id:
                        db.assign_document_to_item(item_db_id, doc_db_id)
                        assigned += 1
                        result.affected_item_ids.add(item_db_id)
                        result.new_docs.append({
                            "filename": doc_row["filename"],
                            "doc_db_id": doc_db_id,
                            "assigned_to_item": item_db_id,
                        })
                        # Remove from unassigned
                        if doc_row["filename"] in unassigned:
                            unassigned.remove(doc_row["filename"])
        except Exception as exc:
            logger.warning("LLM doc matching failed: %s", exc)
            result.errors.append(f"LLM doc matching failed: {exc}")

    # Record remaining unassigned docs
    for fn in unassigned:
        doc_db_id = doc_db_by_filename.get(fn)
        result.new_docs.append({
            "filename": fn,
            "doc_db_id": doc_db_id,
            "assigned_to_item": None,
        })
        result.unassigned_docs.append({"filename": fn, "db_row": {"id": doc_db_id}})

    logger.info(
        "  Refresh: %d new doc(s), %d assigned, %d unassigned, %d item(s) affected",
        len(new_docs), assigned, len(unassigned), len(result.affected_item_ids),
    )
    return result


# ---------------------------------------------------------------------------
# NPC combined PDF refresh
# ---------------------------------------------------------------------------

def _handle_npc_combined_refresh(
    meeting_id: int,
    new_docs: list[dict],
    existing_filenames: set[str],
    config: dict,
    session: requests.Session | None = None,
) -> dict:
    """
    Handle NPC combined PDF upgrades (initial → supplemental → composite).

    If a higher-priority combined PDF appeared among new_docs, download and
    re-parse it to create virtual document rows for new sections.

    Returns {"virtual_docs": [...], "errors": [...]}.
    """
    from pipeline.npc_ingest import find_combined_pdf, _slugify
    from pipeline.npc_combined_parser import parse_combined_pdf
    from pipeline.npc_ingest import _SKIP_SECTION_TYPES

    result = {"virtual_docs": [], "errors": []}

    # Check if any new doc is a combined PDF
    combined = find_combined_pdf(new_docs)
    if not combined:
        return result

    logger.info("  NPC combined PDF upgrade detected: %s", combined["filename"])
    pdf_bytes = _download_bytes(combined.get("url", ""), session)
    if not pdf_bytes:
        result["errors"].append(f"Failed to download NPC combined PDF: {combined['filename']}")
        return result

    sections = parse_combined_pdf(pdf_bytes)

    for s in sections:
        if s.is_tba or not s.text.strip():
            continue

        slug = re.sub(r"[^a-z0-9]+", "_", s.clean_title.lower()).strip("_")[:60]
        synthetic_filename = f"_combined_{s.item_number or 'x'}_{slug}.pdf"

        # Skip if this virtual doc already exists
        if synthetic_filename in existing_filenames:
            continue

        ignored = s.section_type in _SKIP_SECTION_TYPES
        row = db.upsert_document(
            meeting_id=meeting_id,
            filename=synthetic_filename,
            file_type=".pdf",
            source_url=combined.get("url"),
        )
        db.set_document_raw_content(row["id"], s.text)
        if ignored:
            db.set_document_ignored(row["id"], True)

        if not ignored:
            result["virtual_docs"].append({
                "db_row": row,
                "filename": synthetic_filename,
                "url": combined.get("url"),
                "item_number": s.item_number,
                "parent_number": s.parent_number,
                "section_type": s.section_type,
            })

    logger.info("  Created %d new virtual section doc(s) from combined PDF",
                len(result["virtual_docs"]))
    return result


# ---------------------------------------------------------------------------
# Bulk refresh: all upcoming meetings
# ---------------------------------------------------------------------------

def refresh_all_upcoming(
    config: dict | None = None,
    days_ahead: int = 14,
    days_back: int = 7,
    session: requests.Session | None = None,
) -> list[RefreshResult]:
    """
    Refresh documents for all meetings in the window
    [today - days_back, today + days_ahead].

    Returns a list of RefreshResult (only those with new docs).
    """
    config = config or _load_config()
    today = date.today()
    start = today - timedelta(days=days_back)
    end = today + timedelta(days=days_ahead)

    # Get all meetings in the window
    all_meetings = db.list_meetings(limit=500)
    target_meetings = [
        m for m in all_meetings
        if m.get("meeting_date") and start <= m["meeting_date"] <= end
    ]

    logger.info(
        "Refreshing %d meeting(s) in window %s to %s",
        len(target_meetings), start, end,
    )

    results: list[RefreshResult] = []
    for m in target_meetings:
        try:
            r = refresh_meeting_documents(m["id"], config, session)
            if r.has_new:
                results.append(r)
        except Exception as exc:
            logger.error("Refresh failed for meeting %s: %s", m["id"], exc)
            results.append(RefreshResult(
                meeting_id=m["id"],
                meeting_label=f"{m.get('type_short', '?')} {m.get('meeting_date', '?')}",
                errors=[str(exc)],
            ))

    total_new = sum(len(r.new_docs) for r in results)
    logger.info("Refresh complete: %d meeting(s) with %d total new doc(s)",
                len(results), total_new)
    return results
