"""CRUD endpoints for agenda items.

Wraps pipeline/db_new.py:insert_agenda_item / update_agenda_item /
delete_agenda_item so analysts can:
  - Add a new item to a meeting (e.g., backfill a missing parent)
  - Rename / renumber / re-presenter an existing item
  - Delete an item (with cascade through item_documents)
"""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Body, HTTPException

from pipeline import db_new as db
from .. import lifecycle

router = APIRouter(tags=["agenda-items"])


def _item_id_sort_key(iid: str) -> tuple:
    """Natural-sort key for agenda item_ids like '4', '4.a', '4.10', '7.1.b'.

    Splits on '.' and casts each part to (int, ...) when numeric so '4.10'
    sorts after '4.2' rather than alphabetically.
    """
    parts = (iid or "").split(".")
    out: list[tuple] = []
    for p in parts:
        if p.isdigit():
            out.append((0, int(p), p))
        else:
            # Letters / mixed (e.g. "1A", "a") → preserve as string after numbers
            out.append((1, 0, p.lower()))
    return tuple(out)


def _compute_seq_for_new_item(meeting_id: int, new_item_id: str | None) -> int:
    """Pick a seq for a new item that places it correctly in agenda order.

    Strategy: position relative to siblings by natural item_id sort, then
    use the average of neighbouring seqs to create a fresh value. If
    neighbours are adjacent (no integer gap), renumber items after the
    insertion point to make space.
    """
    rows = db.get_agenda_items(meeting_id)
    if not rows:
        return 10
    if not new_item_id:
        max_seq = max((r.get("seq") or 0) for r in rows)
        return max_seq + 10

    new_key = _item_id_sort_key(new_item_id)
    sorted_rows = sorted(rows, key=lambda x: _item_id_sort_key(x.get("item_id") or ""))

    # Find insertion index by natural sort order
    idx = 0
    for i, r in enumerate(sorted_rows):
        if _item_id_sort_key(r.get("item_id") or "") < new_key:
            idx = i + 1
        else:
            break

    prev_seq = sorted_rows[idx - 1]["seq"] if idx > 0 else 0
    next_seq = sorted_rows[idx]["seq"] if idx < len(sorted_rows) else None

    if next_seq is None:
        return prev_seq + 10  # append

    if next_seq - prev_seq >= 2:
        return prev_seq + 1  # integer gap available

    # No gap — bump every item from idx onward by 10 to make room.
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            ids_to_bump = [r["id"] for r in sorted_rows[idx:]]
            if ids_to_bump:
                cur.execute(
                    "UPDATE agenda_items SET seq = seq + 10 WHERE id = ANY(%s)",
                    (ids_to_bump,),
                )
    return prev_seq + 1


@router.post("/api/meetings/{meeting_id}/agenda-items")
def create_item(
    meeting_id: int,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Create a new agenda item on a meeting.

    Required body: `title`.
    Optional: `item_id`, `presenter`, `org`, `time_slot`, `vote_status`,
    `wmpp_id`, `notes`, `depth` (computed from item_id dots if omitted),
    `seq` (defaults to end of meeting's agenda).
    """
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    title = (body.get("title") or "").strip() or "(untitled)"
    item_id_str = (body.get("item_id") or "").strip() or None

    # Reject duplicates of item_id within the meeting.
    if item_id_str:
        for r in db.get_agenda_items(meeting_id):
            if (r.get("item_id") or "") == item_id_str:
                raise HTTPException(
                    status_code=409,
                    detail=f"Item id '{item_id_str}' already exists for this meeting",
                )

    depth = body.get("depth")
    if depth is None and item_id_str:
        depth = item_id_str.count(".")
    depth = int(depth or 0)

    seq = body.get("seq")
    if seq is None:
        seq = _compute_seq_for_new_item(meeting_id, item_id_str)

    row = db.insert_agenda_item(
        meeting_id=meeting_id,
        title=title,
        seq=int(seq),
        depth=depth,
        item_id=item_id_str,
        prefix=body.get("prefix"),
        presenter=body.get("presenter"),
        org=body.get("org"),
        vote_status=body.get("vote_status"),
        wmpp_id=body.get("wmpp_id"),
        time_slot=body.get("time_slot"),
        notes=body.get("notes"),
    )
    lifecycle.bump_lifecycle(meeting_id)
    return {"id": row["id"], "item_id": row.get("item_id"), "title": row.get("title")}


@router.patch("/api/agenda-items/{row_id}")
def update_item(
    row_id: int,
    body: dict[str, Any] = Body(...),
) -> dict[str, str]:
    """Update editable metadata on an agenda item.

    Recognised fields: title, item_id, presenter, org, vote_status,
    wmpp_id, time_slot, notes.
    """
    existing = db.get_agenda_item(row_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Agenda item not found")

    # If renumbering, check for conflicts within the same meeting.
    new_iid = body.get("item_id")
    if new_iid and new_iid != existing.get("item_id"):
        for r in db.get_agenda_items(existing["meeting_id"]):
            if r["id"] != row_id and (r.get("item_id") or "") == new_iid:
                raise HTTPException(
                    status_code=409,
                    detail=f"Item id '{new_iid}' already exists for this meeting",
                )

    db.update_agenda_item(row_id, **body)
    return {"status": "ok"}


@router.post("/api/agenda-items/{row_id}/resummarize")
def resummarize_item(row_id: int) -> dict[str, Any]:
    """Re-run Level-2 rollup for this single agenda item — uses existing
    doc summaries + child-item summaries; writes a new draft `summary_version`.
    """
    from .. import resummarize

    if db.get_agenda_item(row_id) is None:
        raise HTTPException(status_code=404, detail="Agenda item not found")
    try:
        return resummarize.resummarize_agenda_item(row_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/agenda-items/{row_id}")
def delete_item(row_id: int) -> dict[str, str]:
    """Delete an agenda item. item_documents cascade automatically;
    documents themselves are NOT deleted (they fall back to unassigned).
    """
    existing = db.get_agenda_item(row_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Agenda item not found")
    meeting_id = existing["meeting_id"]
    db.delete_agenda_item(row_id)
    lifecycle.bump_lifecycle(meeting_id)
    return {"status": "ok"}
