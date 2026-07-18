"""
pipeline/ingest.py — Ingest a scraped meeting into the new database schema.

Workflow:
  1. Resolve meeting_type from committee short name + venue
  2. Upsert meeting row
  3. Upsert all document rows
  4. Download + parse the agenda docx → insert agenda_items (with depth/parent)
  5. Map documents to agenda items → write item_documents
  6. Create stub summary_version rows for the meeting and each agenda item

Entry point:  ingest_meeting(meeting_dict, doc_list, config)

meeting_dict  — as returned by scraper.scrape_calendar (one element)
doc_list      — list of {filename, url} dicts from scraper.fetch_event_docs
config        — loaded config.yaml dict
"""
import logging
from pathlib import Path

import requests

import pipeline.db as db
from pipeline.agenda_parser import parse_agenda_from_docx, map_docs_to_agenda_items
from pipeline.llm_agenda_parser import parse_agenda_hybrid, llm_match_docs
from pipeline.summarizer import summarize_agenda_item, summarize_meeting

logger = logging.getLogger(__name__)

# Filenames containing any of these substrings are treated as the agenda docx.
_AGENDA_HINTS = ["agenda", "a00_", "a000"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_agenda_doc(doc_list: list[dict]) -> dict | None:
    """Return the first doc whose filename looks like the meeting agenda.

    Prefers .docx over .pdf when both exist.
    """
    best: dict | None = None
    for doc in doc_list:
        fn = doc["filename"].lower()
        if fn.startswith("_combined_"):
            continue  # NPC virtual section docs — never the agenda source
        if not any(h in fn for h in _AGENDA_HINTS):
            continue
        if fn.endswith(".docx"):
            return doc  # docx is preferred — return immediately
        if fn.endswith(".pdf") and best is None:
            best = doc
    return best


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
        resp = sess.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Failed to download %s: %s", url, exc)
        return None


def _item_depth(item_id: str) -> int:
    """Depth from item_id dot notation: '7'→0, '7.1'→1, '7.1.b'→2."""
    return len(item_id.split(".")) - 1


def _parent_item_id(item_id: str) -> str | None:
    """Return parent item_id, or None for top-level."""
    parts = item_id.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def _resolve_meeting_type(committee_short: str,
                           venue_short: str = "ISO-NE") -> int | None:
    """Look up meeting_type.id for a given committee + venue."""
    types = db.get_meeting_types(venue_short_name=venue_short)
    for mt in types:
        if mt["short_name"].upper() == committee_short.upper():
            return mt["id"]
    logger.error("No meeting_type found for %s / %s", committee_short, venue_short)
    return None


# ---------------------------------------------------------------------------
# Agenda items → DB
# ---------------------------------------------------------------------------

def _inherit_wmpp(parsed_items: list[dict]) -> list[dict]:
    """
    Propagate wmpp_id from parent items to children that lack one.
    Items are assumed to be in parse order (parents before children).
    Returns a new list; originals are not mutated.
    """
    wmpp_map: dict[str, str] = {}
    result = []
    for item in parsed_items:
        wmpp = item.get("wmpp_id")
        if not wmpp:
            parent = _parent_item_id(item["item_id"])
            wmpp = wmpp_map.get(parent)
        if wmpp:
            wmpp_map[item["item_id"]] = wmpp
        result.append({**item, "wmpp_id": wmpp})
    return result


def _insert_agenda_items(
    meeting_id: int,
    parsed_items: list[dict],
) -> dict[str, int]:
    """
    Insert parsed agenda items into the DB, computing depth and parent_id
    from the item_id dot-notation hierarchy.

    Returns a mapping {item_id_str → db_row_id} for use in doc assignment.

    Items that already exist for this meeting (same item_id) are NOT
    re-inserted — clear_agenda_for_meeting keeps rows with approved/manual
    summaries, and those must survive a re-ingest without duplicating.
    Existing rows still land in the returned map so doc assignment and
    parent linking keep working.
    """
    id_map: dict[str, int] = {}  # item_id → DB id
    existing: dict[str, int] = {
        r["item_id"]: r["id"]
        for r in db.get_agenda_items(meeting_id)
        if r.get("item_id")
    }

    parsed_items = _inherit_wmpp(parsed_items)

    for seq, item in enumerate(parsed_items):
        raw_item_id = item["item_id"]
        if raw_item_id in existing:
            id_map[raw_item_id] = existing[raw_item_id]
            logger.debug("  kept existing agenda item %s (human-edited)", raw_item_id)
            continue
        depth = _item_depth(raw_item_id)
        parent_raw = _parent_item_id(raw_item_id)
        parent_db_id = id_map.get(parent_raw) if parent_raw else None

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
        id_map[raw_item_id] = row["id"]
        logger.debug("  inserted agenda item %s: %s", raw_item_id, item["title"])

    # Guarantee every dotted item has a correctly-placed, linked parent —
    # even when the agenda never listed the top-line item explicitly.
    stubs = db.ensure_agenda_hierarchy(meeting_id)
    if stubs:
        logger.info("  created %d parent stub(s) for orphaned sub-items", stubs)
        for r in db.get_agenda_items(meeting_id):
            iid = r.get("item_id")
            if iid and iid not in id_map:
                id_map[iid] = r["id"]

    return id_map


# ---------------------------------------------------------------------------
# Tag initiative codes
# ---------------------------------------------------------------------------

def _tag_initiative_codes(item_db_id: int, initiative_codes: list[str]) -> None:
    for code in initiative_codes:
        tag_row = db.upsert_tag(name=code, tag_type="initiative")
        db.tag_entity(tag_row["id"], "agenda_item", item_db_id)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_meeting(
    meeting_dict: dict,
    doc_list: list[dict],
    config: dict,
    venue_short: str = "ISO-NE",
    overwrite: bool = True,
    session: requests.Session | None = None,
) -> int | None:
    """
    Ingest one scraped meeting into the database.

    Returns the meetings.id of the upserted row, or None on failure.

    meeting_dict keys used:
        primary_event_id, committee_short, dates, location, title, meeting_number (optional)

    doc_list: [{filename, url}, ...]
    """
    committee_short = meeting_dict.get("committee_short", "")

    # Check for NPC combined PDF (initial/supplemental/composite) and delegate
    if committee_short.upper() == "NPC":
        from pipeline.npc_ingest import find_combined_pdf
        if find_combined_pdf(doc_list):
            from pipeline.npc_ingest import ingest_npc_meeting
            logger.info("NPC combined PDF detected — delegating to npc_ingest")
            return ingest_npc_meeting(
                meeting_dict, doc_list, config,
                venue_short=venue_short, overwrite=overwrite, session=session,
            )

    event_id = meeting_dict.get("primary_event_id")
    dates = meeting_dict.get("dates", [])
    meeting_date = str(dates[0]) if dates else None

    if not meeting_date:
        logger.error("ingest_meeting: no dates in meeting_dict")
        return None

    meeting_type_id = _resolve_meeting_type(committee_short, venue_short)
    if meeting_type_id is None:
        return None

    # ── 1. Upsert meeting ────────────────────────────────────────────────────
    dates = meeting_dict.get("dates", [])
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
    logger.info("Meeting upserted: id=%s  %s %s", meeting_id, committee_short, meeting_date)

    if overwrite:
        db.clear_agenda_for_meeting(meeting_id)
        logger.info("  Cleared existing agenda for re-ingest")

    # ── 2. Upsert documents ──────────────────────────────────────────────────
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
    logger.info("  %d document(s) upserted", len(doc_db_rows))

    # Note: zip files are not expanded here. The summarizer opens .zip docs
    # transparently at process time (see pipeline/summarizer.extract_text_zip).

    # ── 3. Parse agenda docx ─────────────────────────────────────────────────
    agenda_doc = _find_agenda_doc(doc_list)
    parsed_items: list[dict] = []
    item_id_map: dict[str, int] = {}  # item_id_str → DB row id

    parse_audit: dict | None = None

    if agenda_doc:
        logger.info("  Downloading agenda: %s", agenda_doc["filename"])
        agenda_bytes = _download_bytes(agenda_doc["url"], session)
        if agenda_bytes:
            try:
                parse_mode = config.get("agenda_parsing", {}).get("mode", "regex_only")
                parsed_items, parse_audit = parse_agenda_hybrid(
                    agenda_bytes, venue_short, committee_short,
                    mode=parse_mode, config=config,
                )
                logger.info("  Parsed %d agenda item(s) [mode=%s]", len(parsed_items), parse_mode)
                if parse_audit:
                    logger.info("  Parse audit: %s", {
                        k: v for k, v in parse_audit.items()
                        if k not in ("matched", "escalated_matched")
                    })
            except Exception as exc:
                logger.error("  Hybrid agenda parse failed: %s — falling back to regex", exc)
                try:
                    parsed_items = parse_agenda_from_docx(agenda_bytes)
                    logger.info("  Regex fallback: %d agenda item(s)", len(parsed_items))
                except Exception as exc2:
                    logger.error("  Regex fallback also failed: %s", exc2)
        else:
            logger.warning("  Could not download agenda docx")
    else:
        logger.warning("  No agenda docx found in doc list")

    # ── 4. Insert agenda items → DB ──────────────────────────────────────────
    if parsed_items:
        item_id_map = _insert_agenda_items(meeting_id, parsed_items)

        # Tag initiative codes
        for item in parsed_items:
            db_id = item_id_map.get(item["item_id"])
            if db_id and item.get("initiative_codes"):
                _tag_initiative_codes(db_id, item["initiative_codes"])

    # ── 5. Map documents → agenda items → item_documents ────────────────────
    if parsed_items and doc_db_rows:
        simple_doc_rows = [{"filename": d["filename"]} for d in doc_db_rows]
        doc_db_by_filename = {d["filename"]: d["db_row"]["id"] for d in doc_db_rows}
        prefix_to_item_db_id = {
            item["prefix"]: item_id_map[item["item_id"]]
            for item in parsed_items
            if item.get("prefix") and item["item_id"] in item_id_map
        }
        # Step 5a: regex prefix matching
        buckets = map_docs_to_agenda_items(simple_doc_rows, parsed_items)
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
        logger.info("  %d document-item assignment(s) via regex prefix", assigned)

        # Step 5b: LLM matching for unassigned files
        unassigned = [r["filename"] for r in buckets.get("other", [])]
        parse_mode = config.get("agenda_parsing", {}).get("mode", "regex_only")
        if unassigned and parse_mode != "regex_only":
            logger.info("  %d unassigned file(s) — running LLM matching", len(unassigned))
            try:
                match_model = config.get("agenda_parsing", {}).get(
                    "match_model", "claude-haiku-4-5-20251001",
                )
                llm_buckets = llm_match_docs(
                    parsed_items, unassigned, venue_short, model=match_model,
                )
                llm_assigned = 0
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
                            llm_assigned += 1
                logger.info("  %d additional assignment(s) via LLM matching", llm_assigned)
                assigned += llm_assigned
            except Exception as exc:
                logger.warning("  LLM doc matching failed: %s", exc)

        logger.info("  %d total document-item assignment(s)", assigned)

    # ── 6. Create stub summaries ─────────────────────────────────────────────
    for item in parsed_items:
        db_id = item_id_map.get(item["item_id"])
        if db_id:
            try:
                summarize_agenda_item(db_id, created_by="ingest")
            except Exception as exc:
                logger.warning("  stub failed for item %s: %s", item["item_id"], exc)

    try:
        summarize_meeting(meeting_id, created_by="ingest")
    except Exception as exc:
        logger.warning("  meeting stub failed: %s", exc)

    db.set_meeting_status(meeting_id, "complete")
    logger.info("  Ingest complete: meeting_id=%s", meeting_id)
    return meeting_id


# ---------------------------------------------------------------------------
# Clean-up: undo a prior pre-expansion of zip archives
# ---------------------------------------------------------------------------

def cleanup_zip_expansion(meeting_id: int) -> dict[str, int]:
    """Reverse an earlier `expand_zip_documents` run.

    Deletes any document row whose source_url contains the synthetic `#zip:`
    fragment, and un-ignores any `.zip` document rows. Used to migrate
    meetings that were pre-expanded back to the on-demand model where the
    summarizer opens zips at process time.

    Returns counts of what was changed.
    """
    deleted_children = 0
    un_ignored = 0

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """DELETE FROM documents
                    WHERE meeting_id = %s
                      AND source_url LIKE %s""",
                (meeting_id, "%#zip:%"),
            )
            deleted_children = cur.rowcount or 0

            cur.execute(
                """UPDATE documents
                       SET ignored = false
                     WHERE meeting_id = %s
                       AND file_type = '.zip'
                       AND ignored = true""",
                (meeting_id,),
            )
            un_ignored = cur.rowcount or 0

    return {"deleted_children": deleted_children, "un_ignored_zips": un_ignored}


# ---------------------------------------------------------------------------
# Public API — used by api/orchestrator.py. The underscore originals stay for
# internal callers; these names are the supported surface.
# ---------------------------------------------------------------------------
download_bytes = _download_bytes
find_agenda_doc = _find_agenda_doc
insert_agenda_items = _insert_agenda_items
inherit_wmpp = _inherit_wmpp
tag_initiative_codes = _tag_initiative_codes
