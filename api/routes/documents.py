"""Document → agenda-item assignment endpoints.

Wraps the existing pipeline/db_new.py primitives so the web UI can:
  - see unassigned / assigned / ignored documents for a meeting
  - assign an unassigned doc to an item
  - reassign an already-assigned doc to a different item
  - unassign a doc
  - mark a doc as ignored / restore it
"""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Body, HTTPException

from pipeline import db_new as db

router = APIRouter(tags=["documents"])


def _doc_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Compact doc shape for the assignment UI (includes id + ignored flag)."""
    return {
        "id": d["id"],
        "filename": d.get("filename") or "",
        "type": (d.get("filename") or "").rsplit(".", 1)[-1].lower(),
        "ignored": bool(d.get("ignored")),
    }


@router.get("/api/meetings/{meeting_id}/documents")
def list_meeting_documents(meeting_id: int) -> dict[str, Any]:
    """Return docs grouped: unassigned, by_item (item_id → list), ignored."""
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    unassigned = [_doc_dict(d) for d in db.get_unassigned_documents(meeting_id)]
    ignored = [_doc_dict(d) for d in db.get_ignored_documents(meeting_id)]

    by_item: dict[int, list[dict[str, Any]]] = {}
    for ar in db.get_agenda_items(meeting_id):
        rows = db.get_documents_for_item(ar["id"])
        if rows:
            by_item[ar["id"]] = [_doc_dict(d) for d in rows]

    return {
        "unassigned": unassigned,
        "by_item": by_item,
        "ignored": ignored,
    }


@router.post("/api/agenda-items/{item_id}/documents/{doc_id}")
def assign(item_id: int, doc_id: int) -> dict[str, str]:
    """Assign a previously-unassigned document to an agenda item."""
    db.assign_document_to_item(item_id, doc_id)
    return {"status": "ok"}


@router.patch("/api/documents/{doc_id}/item")
def reassign(
    doc_id: int,
    body: dict[str, int] = Body(..., description="{item_id, meeting_id}"),
) -> dict[str, str]:
    """Move a document from one agenda item to another within the same meeting."""
    item_id = body.get("item_id")
    meeting_id = body.get("meeting_id")
    if item_id is None or meeting_id is None:
        raise HTTPException(status_code=400, detail="item_id and meeting_id required")
    db.reassign_document(doc_id, item_id, meeting_id)
    return {"status": "ok"}


@router.delete("/api/agenda-items/{item_id}/documents/{doc_id}")
def unassign(
    item_id: int,
    doc_id: int,
    meeting_id: int,
) -> dict[str, str]:
    """Remove a document's assignment to an agenda item.

    item_id is part of the URL for symmetry with POST; meeting_id is the
    actual scope key for the DB call.
    """
    db.unassign_document(doc_id, meeting_id)
    return {"status": "ok"}


@router.patch("/api/documents/{doc_id}")
def set_ignored(
    doc_id: int,
    body: dict[str, Any] = Body(..., description="{ignored: bool}"),
) -> dict[str, str]:
    ignored = body.get("ignored")
    if not isinstance(ignored, bool):
        raise HTTPException(status_code=400, detail="ignored (bool) required")
    db.set_document_ignored(doc_id, ignored)
    return {"status": "ok"}
