"""Per-item resummarization — wires the existing pipeline/summarizer.py
helpers into a single-item entrypoint that the meeting page's Re-run button
calls. Avoids re-running the Level-3 meeting briefing (which is expensive
and not what the user asked for when they clicked Re-run on one item).

The pipeline's data model (per pipeline/summarizer.py):
  - Level 1 (`_run_item_doc_summary`): for a leaf item with documents,
    reads all doc text and writes a single agenda_item summary_version.
  - Level 2 (`_run_item_rollup`):       for a parent item with children,
    rolls up the children's summaries into the parent.
  - Level 3 (`_run_meeting_briefing`):  for the whole meeting briefing.

Re-run on a single item:
  * has children → Level 2 rollup
  * leaf with docs → Level 1 from doc text
  * neither → nothing to do
"""
from __future__ import annotations

import logging
from typing import Any

from pipeline import db_new as db
from pipeline.summarizer import (
    _get_committee_prompts,
    _load_model_config,
    _load_prompt,
    _run_item_doc_summary,
    _run_item_rollup,
    make_client,
)

from . import lifecycle

log = logging.getLogger("poolside.resummarize")


def _children_of(item: dict, all_items: list[dict]) -> list[dict]:
    """Direct children of `item` via parent_id FK OR item_id prefix."""
    children: list[dict] = []
    parent_iid = item.get("item_id") or ""
    for ai in all_items:
        if ai["id"] == item["id"]:
            continue
        if ai.get("parent_id") == item["id"]:
            children.append(ai)
            continue
        if parent_iid and ai.get("item_id", "").startswith(f"{parent_iid}."):
            rest = ai["item_id"][len(parent_iid) + 1 :]
            if "." not in rest:  # direct child, not grandchild
                children.append(ai)
    return children


def resummarize_agenda_item(item_id: int) -> dict[str, Any]:
    """Re-run the summary for a single agenda item.

    Writes a new draft `summary_version` (the prior is automatically
    superseded by `create_summary_version`'s next-version logic).
    Does NOT touch the Level-3 meeting briefing.
    """
    item = db.get_agenda_item(item_id)
    if item is None:
        raise ValueError(f"agenda item {item_id} not found")

    meeting = db.get_meeting(item["meeting_id"])
    if meeting is None:
        raise ValueError(f"meeting {item['meeting_id']} not found")

    venue_short = meeting.get("venue_short") or "ISO-NE"
    type_short = meeting.get("type_short") or "MC"

    cfg = _load_model_config()
    client = make_client()

    all_items = db.get_agenda_items(item["meeting_id"])
    children = _children_of(item, all_items)
    docs = db.get_documents_for_item(item_id)

    # Path A: has children → Level 2 rollup from child summaries
    if children:
        children_with_summaries: list[tuple[dict, dict]] = []
        for child in children:
            s = db.get_current_summary("agenda_item", child["id"])
            if s and (s.get("detailed") or s.get("one_line")):
                children_with_summaries.append((child, s))
        if not children_with_summaries:
            return {
                "ok": False,
                "level": 2,
                "reason": (
                    f"Item has {len(children)} children but none have "
                    "summaries yet. Re-run those child items first."
                ),
            }
        _, agenda_item_prompt = _get_committee_prompts(type_short, venue_short)
        if not agenda_item_prompt:
            return {"ok": False, "reason": f"No agenda_item prompt for {venue_short}/{type_short}"}
        log.info(
            "L2 rollup for item %s (%s/%s) — %d children, model=%s",
            item.get("item_id"), venue_short, type_short,
            len(children_with_summaries), cfg["item_model"],
        )
        ok = _run_item_rollup(
            item=item,
            child_summaries=children_with_summaries,
            client=client,
            model=cfg["item_model"],
            agenda_item_prompt=agenda_item_prompt,
            max_tokens=cfg.get("item_max_tokens", 4096),
        )
        if ok:
            lifecycle.bump_lifecycle(item["meeting_id"])
        return {
            "ok": bool(ok),
            "level": 2,
            "model": cfg["item_model"],
            "n_children": len(children_with_summaries),
            "item_id": item.get("item_id"),
            "reason": None if ok else "rollup returned no summary",
        }

    # Path B: leaf with documents → Level 1 from doc text
    if docs:
        doc_summary_prompt = _load_prompt("doc_summary_prompt") or (
            "Summarise the following document(s) for an energy market analyst.\n\n"
            "Document(s): {filename}\n\n{text}"
        )
        log.info(
            "L1 doc summary for item %s — %d docs, model=%s",
            item.get("item_id"), len(docs), cfg["document_model"],
        )
        ok = _run_item_doc_summary(
            item=item,
            client=client,
            model=cfg["document_model"],
            doc_summary_prompt=doc_summary_prompt,
            max_tokens=cfg.get("document_max_tokens", 4096),
            extract_images=False,
            meeting_folder=None,
        )
        if ok:
            lifecycle.bump_lifecycle(item["meeting_id"])
        return {
            "ok": bool(ok),
            "level": 1,
            "model": cfg["document_model"],
            "n_docs": len(docs),
            "item_id": item.get("item_id"),
            "reason": None if ok else "no usable documents (all skipped/ignored/unsupported)",
        }

    # Path C: no inputs
    return {
        "ok": False,
        "level": None,
        "reason": "Nothing to summarize — item has no documents and no children with summaries.",
    }
