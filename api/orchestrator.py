"""Pipeline orchestration — chains discover → parse_agenda → refresh_materials.

The standalone pipeline functions in pipeline/refresh.py and pipeline/ingest.py
each do one thing well, but they don't know about each other. When materials
are refreshed for a meeting that has no agenda parsed yet, the refresh dumps
every doc into "unassigned" (correctly — nothing to assign to). This module
adds the missing glue: parse the agenda first, then assign docs.
"""
from __future__ import annotations

import logging
import hashlib
from pathlib import Path
from typing import Any

import requests

from pipeline import db_new as db
from pipeline import refresh as pl_refresh
from pipeline.ingest import (
    _download_bytes,
    _find_agenda_doc,
    _insert_agenda_items,
    _inherit_wmpp,
    _tag_initiative_codes,
)
from pipeline.llm_agenda_parser import parse_agenda_hybrid
from pipeline.agenda_parser import parse_agenda_from_docx

from . import lifecycle

log = logging.getLogger("poolside.orchestrator")


def try_parse_agenda(meeting_id: int, config: dict) -> dict[str, Any]:
    """Look for an agenda doc among the meeting's stored documents and parse it.

    Returns:
      {
        "parsed": bool,            # True if items were inserted
        "n_items": int,
        "agenda_filename": str | None,
        "reason": str | None,      # populated when parsed=False
      }
    """
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        return {"parsed": False, "n_items": 0, "agenda_filename": None,
                "reason": "meeting not found"}

    existing_items = db.get_agenda_items(meeting_id)
    if existing_items:
        return {"parsed": False, "n_items": len(existing_items),
                "agenda_filename": None,
                "reason": f"agenda already parsed ({len(existing_items)} items)"}

    # Find the agenda doc among existing documents
    docs = db.get_documents_for_meeting(meeting_id)
    if not docs:
        return {"parsed": False, "n_items": 0, "agenda_filename": None,
                "reason": "no documents on this meeting"}

    # _find_agenda_doc expects {filename, url} shape
    candidates = [
        {"filename": d["filename"], "url": d.get("source_url"), "_db": d}
        for d in docs
    ]
    agenda = _find_agenda_doc(candidates)
    if agenda is None:
        return {"parsed": False, "n_items": 0, "agenda_filename": None,
                "reason": "no document name matches agenda heuristic"}

    url = agenda.get("url")
    if not url:
        return {"parsed": False, "n_items": 0,
                "agenda_filename": agenda["filename"],
                "reason": "agenda doc has no source_url"}

    log.info("Downloading agenda doc for meeting %s: %s", meeting_id, agenda["filename"])
    session = requests.Session()
    agenda_bytes = _download_bytes(url, session)
    if not agenda_bytes:
        return {"parsed": False, "n_items": 0,
                "agenda_filename": agenda["filename"],
                "reason": "download failed"}

    # Parse via hybrid LLM+regex
    venue_short = meeting.get("venue_short") or "ISO-NE"
    type_short = meeting.get("type_short") or ""
    parse_mode = config.get("agenda_parsing", {}).get("mode", "llm_verify")

    try:
        parsed_items, audit = parse_agenda_hybrid(
            agenda_bytes, venue_short, type_short,
            mode=parse_mode, config=config,
        )
        log.info("parse_agenda_hybrid → %d items (mode=%s)", len(parsed_items), parse_mode)
    except Exception as e:
        log.warning("parse_agenda_hybrid failed: %s — falling back to regex", e)
        try:
            parsed_items = parse_agenda_from_docx(agenda_bytes)
            log.info("regex fallback → %d items", len(parsed_items))
        except Exception as e2:
            log.exception("regex fallback also failed: %s", e2)
            return {"parsed": False, "n_items": 0,
                    "agenda_filename": agenda["filename"],
                    "reason": f"parse failed: {e2}"}

    if not parsed_items:
        return {"parsed": False, "n_items": 0,
                "agenda_filename": agenda["filename"],
                "reason": "parser returned 0 items"}

    parsed_items = _inherit_wmpp(parsed_items)
    item_id_map = _insert_agenda_items(meeting_id, parsed_items)
    for item in parsed_items:
        dbid = item_id_map.get(item["item_id"])
        if dbid and item.get("initiative_codes"):
            _tag_initiative_codes(dbid, item["initiative_codes"])

    # Record the hash + timestamp so we can detect future re-parses
    agenda_hash = hashlib.sha256(agenda_bytes).hexdigest()
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "UPDATE meetings SET agenda_doc_hash=%s, agenda_parsed_at=NOW() WHERE id=%s",
                (agenda_hash, meeting_id),
            )

    return {
        "parsed": True,
        "n_items": len(parsed_items),
        "agenda_filename": agenda["filename"],
        "reason": None,
    }


def refresh_with_agenda(meeting_id: int, config: dict) -> dict[str, Any]:
    """End-to-end refresh: pull docs, parse agenda if needed, then auto-assign.

    Steps:
      1. refresh_meeting_documents — pulls new docs from the source.
         If the agenda was already parsed, this also runs the regex+LLM
         assignment for any new docs.
      2. If agenda_items is still empty, try_parse_agenda — finds the agenda
         doc among the just-downloaded set and parses it into agenda_items.
      3. If we just parsed an agenda, call refresh_meeting_documents AGAIN —
         this time the auto-assignment has items to point at, so the existing
         "unassigned" docs will get placed.
      4. bump_lifecycle.

    Returns a structured summary for the caller.
    """
    out: dict[str, Any] = {"meeting_id": meeting_id, "steps": []}

    # Step 1
    try:
        r = pl_refresh.refresh_meeting_documents(meeting_id, config=config)
        out["steps"].append({
            "step": "refresh_materials",
            "new_docs": len(getattr(r, "new_docs", []) or []),
            "errors": getattr(r, "errors", []) or [],
        })
    except Exception as e:
        log.exception("refresh_meeting_documents failed: %s", e)
        out["steps"].append({"step": "refresh_materials", "error": str(e)})

    # Step 2: try parsing agenda if we still don't have one
    items = db.get_agenda_items(meeting_id)
    parse_result: dict[str, Any] | None = None
    if not items:
        parse_result = try_parse_agenda(meeting_id, config)
        out["steps"].append({"step": "parse_agenda", **parse_result})

    # Step 3: if we just parsed an agenda, run assignment again to backfill
    # the docs that were sitting in "unassigned".
    if parse_result and parse_result.get("parsed"):
        try:
            # The refresh helper only assigns NEW docs — we need to manually
            # run the assignment pass over the already-existing docs.
            _assign_existing_docs(meeting_id, config)
            out["steps"].append({"step": "assign_existing_docs", "ok": True})
        except Exception as e:
            log.exception("assign_existing_docs failed: %s", e)
            out["steps"].append({"step": "assign_existing_docs", "error": str(e)})

    new_status = lifecycle.bump_lifecycle(meeting_id)
    out["lifecycle_status"] = new_status
    return out


def _assign_existing_docs(meeting_id: int, config: dict) -> None:
    """Run regex + LLM doc-to-item assignment over ALL existing unassigned
    documents for a meeting. The standard refresh only assigns NEW docs;
    this is for the case where docs were ingested before the agenda was parsed.
    """
    items = db.get_agenda_items(meeting_id)
    if not items:
        return

    unassigned = db.get_unassigned_documents(meeting_id)
    if not unassigned:
        return

    # ── regex pass ──────────────────────────────────────────────────────────
    doc_rows_simple = [{"filename": d["filename"]} for d in unassigned]
    buckets = pl_refresh.map_docs_to_agenda_items(doc_rows_simple, items)

    prefix_to_item_db_id = {
        item["prefix"]: item["id"] for item in items if item.get("prefix")
    }
    doc_db_by_filename = {d["filename"]: d["id"] for d in unassigned}

    assigned = 0
    for prefix, docs_in_bucket in buckets.items():
        if prefix == "other":
            continue
        item_db_id = prefix_to_item_db_id.get(prefix)
        if not item_db_id:
            continue
        for d in docs_in_bucket:
            doc_db_id = doc_db_by_filename.get(d["filename"])
            if doc_db_id is not None:
                db.assign_document_to_item(item_db_id, doc_db_id)
                assigned += 1
    log.info("regex assign: %d docs assigned for meeting %s", assigned, meeting_id)

    # ── LLM pass over what's still unassigned ───────────────────────────────
    still_unassigned = db.get_unassigned_documents(meeting_id)
    if not still_unassigned:
        return

    parse_mode = config.get("agenda_parsing", {}).get("mode", "regex_only")
    if parse_mode == "regex_only":
        return

    try:
        from pipeline.llm_agenda_parser import llm_match_docs

        match_model = config.get("agenda_parsing", {}).get(
            "match_model", "claude-haiku-4-5-20251001"
        )
        meeting = db.get_meeting(meeting_id) or {}
        venue_short = meeting.get("venue_short") or "ISO-NE"
        filenames = [d["filename"] for d in still_unassigned]
        # Same bucket-dict contract as map_docs_to_agenda_items; mirrors the
        # working call in pipeline/refresh.py.
        llm_buckets = llm_match_docs(items, filenames, venue_short, model=match_model)
        n = 0
        for prefix, docs_in_bucket in llm_buckets.items():
            if prefix == "other":
                continue
            item_db_id = prefix_to_item_db_id.get(prefix)
            if not item_db_id:
                continue
            for d in docs_in_bucket:
                doc_db_id = doc_db_by_filename.get(d["filename"])
                if doc_db_id is not None:
                    db.assign_document_to_item(item_db_id, doc_db_id)
                    n += 1
        log.info("LLM assign: %d docs assigned for meeting %s", n, meeting_id)
    except Exception as e:
        log.warning("LLM assignment pass failed: %s", e)
