"""
pipeline/nyiso_ingest.py — Ingest a NYISO meeting into the database.

Mirrors pipeline/ingest.py but uses NYISO data sources:
  - nyiso_scraper.fetch_meeting_files()  for file listing
  - nyiso_agenda_parser.parse_agenda_pdf() for agenda parsing (PDF, not docx)
  - nyiso_agenda_parser.map_files_to_agenda_items() for doc-to-item mapping

Entry point:  ingest_nyiso_meeting(committee, meeting_id, meeting_date, ...)
"""
import logging
import time
from datetime import date
from pathlib import Path

import requests

import pipeline.db_new as db
from pipeline.downloader import download_file_temp
from pipeline.nyiso_agenda_parser import map_files_to_agenda_items, parse_agenda_pdf
from pipeline.nyiso_scraper import fetch_meeting_files
from pipeline.summarizer import summarize_agenda_item, summarize_meeting

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_meeting_type(committee_short: str,
                           venue_short: str = "NYISO") -> int | None:
    """Look up meeting_type.id for a given committee + venue."""
    types = db.get_meeting_types(venue_short_name=venue_short)
    for mt in types:
        if mt["short_name"].upper() == committee_short.upper():
            return mt["id"]
    logger.error("No meeting_type found for %s / %s", committee_short, venue_short)
    return None


def _find_agenda_file(files: list[dict]) -> dict | None:
    """Return the first file that looks like the meeting agenda."""
    for f in files:
        if f.get("file_type") != "pdf":
            continue
        # Agenda prefix "1" or "0" or display name contains "agenda"
        prefix = f.get("agenda_prefix")
        if prefix in ("1", "0", "00") or "agenda" in f.get("name", "").lower():
            return f
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_nyiso_meeting(
    committee: dict,
    meeting_id: str,
    meeting_date: date,
    venue_short: str = "NYISO",
    overwrite: bool = True,
    session: requests.Session | None = None,
) -> int | None:
    """
    Full ingest of a single NYISO meeting into Postgres.

    1. Resolve meeting_type_id from committee short + venue
    2. Upsert meeting (external_id = NYISO folder ID)
    3. Fetch file list via nyiso_scraper.fetch_meeting_files()
    4. Upsert documents
    5. Download agenda PDF to temp file
    6. Parse agenda via nyiso_agenda_parser.parse_agenda_pdf()
    7. Insert agenda items (flat -- NYISO agendas are not hierarchical)
    8. Map documents to items via prefix matching
    9. Create stub summaries
    10. Set meeting status = 'complete'

    Returns the meetings.id of the upserted row, or None on failure.
    """
    committee_short = committee["short"]
    sess = session or requests.Session()

    meeting_type_id = _resolve_meeting_type(committee_short, venue_short)
    if meeting_type_id is None:
        return None

    # -- 1. Upsert meeting ------------------------------------------------
    meeting_row = db.upsert_meeting(
        meeting_type_id=meeting_type_id,
        meeting_date=meeting_date.isoformat(),
        external_id=meeting_id,
        title=committee["name"],
    )
    db_meeting_id = meeting_row["id"]
    logger.info("Meeting upserted: id=%s  %s %s", db_meeting_id,
                committee_short, meeting_date.isoformat())

    if overwrite:
        db.clear_agenda_for_meeting(db_meeting_id)
        logger.info("  Cleared existing agenda for re-ingest")

    # -- 2. Fetch file list from NYISO API --------------------------------
    files = fetch_meeting_files(committee, meeting_id, session=sess)
    if not files:
        logger.warning("No files returned from NYISO API for meeting %s", meeting_id)

    # -- 3. Upsert documents ----------------------------------------------
    doc_db_rows: list[dict] = []
    for f in files:
        filename = f["filename"]
        ext = Path(filename).suffix.lower()
        row = db.upsert_document(
            meeting_id=db_meeting_id,
            filename=filename,
            file_type=ext,
            source_url=f.get("url"),
        )
        doc_db_rows.append({
            "db_row": row,
            "filename": filename,
            "url": f.get("url"),
            "agenda_prefix": f.get("agenda_prefix"),
            "name": f.get("name"),
        })
    logger.info("  %d document(s) upserted", len(doc_db_rows))

    # -- 4. Download and parse agenda PDF ---------------------------------
    agenda_file = _find_agenda_file(files)
    parsed_items: list[dict] = []

    if agenda_file:
        logger.info("  Downloading agenda: %s", agenda_file["filename"])
        try:
            with download_file_temp(
                url=agenda_file["url"],
                filename=agenda_file["filename"],
                referer_url=agenda_file["url"],
                session=sess,
            ) as tmp_path:
                if tmp_path:
                    parsed_items = parse_agenda_pdf(tmp_path)
                    logger.info("  Parsed %d agenda item(s)", len(parsed_items))
                else:
                    logger.warning("  Agenda download returned None")
        except Exception as exc:
            logger.error("  Agenda parse failed: %s", exc)
    else:
        logger.warning("  No agenda PDF found in file list")

    # -- 5. Insert agenda items (flat, depth=0) ---------------------------
    # Items kept by clear_agenda_for_meeting (approved/manual summaries)
    # must not be re-inserted; reuse their row ids.
    item_id_map: dict[str, int] = {}  # item_id_str -> DB row id
    _existing = {
        r["item_id"]: r["id"]
        for r in db.get_agenda_items(db_meeting_id)
        if r.get("item_id")
    }
    for seq, item in enumerate(parsed_items):
        if item["item_id"] in _existing:
            item_id_map[item["item_id"]] = _existing[item["item_id"]]
            continue
        row = db.insert_agenda_item(
            meeting_id=db_meeting_id,
            title=item["title"],
            seq=seq,
            depth=0,
            parent_id=None,
            item_id=item["item_id"],
            prefix=item["item_id"],
            presenter=item.get("presenter"),
            time_slot=item.get("time_slot"),
        )
        item_id_map[item["item_id"]] = row["id"]
        logger.debug("  inserted agenda item %s: %s", item["item_id"], item["title"])

    # -- 6. Map documents to agenda items ---------------------------------
    if parsed_items and doc_db_rows:
        file_mapping = map_files_to_agenda_items(files, parsed_items)

        # Build lookup: filename -> doc DB id
        doc_db_by_filename = {d["filename"]: d["db_row"]["id"] for d in doc_db_rows}

        assigned = 0
        for item_id_str, matched_files in file_mapping.items():
            if item_id_str == "unmatched":
                continue
            db_item_id = item_id_map.get(item_id_str)
            if not db_item_id:
                continue
            for f in matched_files:
                fn = f["filename"] if isinstance(f, dict) else f
                doc_db_id = doc_db_by_filename.get(fn)
                if doc_db_id:
                    db.assign_document_to_item(db_item_id, doc_db_id)
                    assigned += 1
        logger.info("  %d document-item assignment(s) written", assigned)

    # -- 7. Create stub summaries -----------------------------------------
    for item_id_str, db_id in item_id_map.items():
        try:
            summarize_agenda_item(db_id, created_by="ingest")
        except Exception as exc:
            logger.warning("  stub failed for item %s: %s", item_id_str, exc)

    try:
        summarize_meeting(db_meeting_id, created_by="ingest")
    except Exception as exc:
        logger.warning("  meeting stub failed: %s", exc)

    db.set_meeting_status(db_meeting_id, "complete")
    logger.info("  Ingest complete: meeting_id=%s", db_meeting_id)
    return db_meeting_id
