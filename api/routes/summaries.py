"""Summary read/write endpoints — the heart of the rich-text editor.

Wraps pipeline/db_new.py:save_manual_summary and get_current_summary so the
new full-page editor (web/src/routes/Editor.tsx) can:
  - load the current draft for a meeting or agenda item
  - save user-edited markdown as a new approved version (superseding the prior)
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException

from pipeline import db_new as db
from .. import lifecycle

router = APIRouter(tags=["summaries"])

EntityType = Literal["meeting", "agenda_item"]


def _validate_entity_type(t: str) -> EntityType:
    if t not in ("meeting", "agenda_item"):
        raise HTTPException(status_code=400, detail=f"Unknown entity_type: {t}")
    return t  # type: ignore[return-value]


@router.get("/api/summaries/{entity_type}/{entity_id}")
def get_summary(entity_type: str, entity_id: int) -> dict[str, Any]:
    """Return the current summary for an entity (meeting or agenda_item)."""
    et = _validate_entity_type(entity_type)

    # Look up the parent (meeting or agenda item) so we can echo a friendly title.
    parent_label = ""
    meeting_id: int | None = None
    if et == "meeting":
        m = db.get_meeting(entity_id)
        if m is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        parent_label = f"{m.get('type_short','')} — {m.get('meeting_date','')}"
        meeting_id = entity_id
    else:
        item = db.get_agenda_item(entity_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Agenda item not found")
        parent_label = f"Item {item.get('item_id','')}: {item.get('title','')}"
        meeting_id = item["meeting_id"]

    s = db.get_current_summary(et, entity_id) or {}
    return {
        "entity_type": et,
        "entity_id": entity_id,
        "meeting_id": meeting_id,
        "parent_label": parent_label,
        "one_line": s.get("one_line") or "",
        "detailed": s.get("detailed") or "",
        "version": s.get("version"),
        "status": s.get("status"),
        "is_manual": bool(s.get("is_manual")),
        "created_at": str(s.get("created_at") or "") or None,
        "created_by": s.get("created_by"),
    }


@router.put("/api/summaries/{entity_type}/{entity_id}")
def save_summary(
    entity_type: str,
    entity_id: int,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Save a manual edit as a new approved version. Supersedes the prior.

    Body: { one_line?: str, detailed: str }
    """
    et = _validate_entity_type(entity_type)
    detailed = body.get("detailed")
    one_line = body.get("one_line")
    if not isinstance(detailed, str):
        raise HTTPException(status_code=400, detail="`detailed` (string) is required")

    # Resolve meeting_id for the lifecycle bump
    if et == "meeting":
        m = db.get_meeting(entity_id)
        if m is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        meeting_id = entity_id
    else:
        item = db.get_agenda_item(entity_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Agenda item not found")
        meeting_id = item["meeting_id"]

    row = db.save_manual_summary(
        entity_type=et,
        entity_id=entity_id,
        one_line=(one_line or None),
        detailed=detailed,
        created_by="user",
    )
    lifecycle.bump_lifecycle(meeting_id)
    return {
        "id": row["id"],
        "version": row["version"],
        "status": row["status"],
        "is_manual": True,
    }


# ─── Version history ────────────────────────────────────────────────────────


def _version_meta(row: dict) -> dict[str, Any]:
    """Compact metadata for the version list (no body text)."""
    detailed = row.get("detailed") or ""
    one_line = row.get("one_line") or ""
    return {
        "id": row["id"],
        "version": row["version"],
        "status": row["status"],
        "is_manual": bool(row.get("is_manual")),
        "model_id": row.get("model_id"),
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "created_by": row.get("created_by"),
        "size": len(detailed),
        "preview": one_line or (detailed[:160] + "…" if len(detailed) > 160 else detailed),
    }


@router.get("/api/summaries/{entity_type}/{entity_id}/versions")
def list_versions(entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    et = _validate_entity_type(entity_type)
    rows = db.list_summary_versions(et, entity_id)
    return [_version_meta(r) for r in rows]


@router.get("/api/summaries/{entity_type}/{entity_id}/versions/{version_id}")
def get_version(entity_type: str, entity_id: int, version_id: int) -> dict[str, Any]:
    et = _validate_entity_type(entity_type)
    # Look up the specific version, scoped to the entity for safety.
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                SELECT * FROM summary_versions
                WHERE id = %s AND entity_type = %s AND entity_id = %s
                """,
                (version_id, et, entity_id),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")
    return {
        **_version_meta(dict(row)),
        "detailed": row.get("detailed") or "",
        "one_line": row.get("one_line") or "",
    }


@router.post("/api/summaries/{entity_type}/{entity_id}/versions/{version_id}/restore")
def restore_version(
    entity_type: str, entity_id: int, version_id: int
) -> dict[str, Any]:
    """Make this version the current approved one. Marks all other non-stub
    versions for the same entity as superseded.

    Resolve the meeting_id for the lifecycle bump.
    """
    et = _validate_entity_type(entity_type)
    if et == "meeting":
        if db.get_meeting(entity_id) is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        meeting_id = entity_id
    else:
        item = db.get_agenda_item(entity_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Agenda item not found")
        meeting_id = item["meeting_id"]

    # Verify the version belongs to this entity
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "SELECT id FROM summary_versions WHERE id = %s AND entity_type = %s AND entity_id = %s",
                (version_id, et, entity_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Version not found for this entity")

    db.approve_summary_version(version_id)
    lifecycle.bump_lifecycle(meeting_id)
    return {"status": "ok", "restored_version_id": version_id}
