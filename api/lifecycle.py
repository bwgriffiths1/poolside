"""Meeting lifecycle helpers.

Single source of truth for which lifecycle_status a meeting should be in.
Called after any operation that could move a meeting between phases
(ingest, agenda parse, materials refresh, summarize, approve).
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pipeline import db_new as db

LifecycleStatus = Literal[
    "discovered",
    "agenda_posted",
    "materials_posted",
    "summarized",
    "approved",
]

_ORDER = ["discovered", "agenda_posted", "materials_posted", "summarized", "approved"]


def compute_lifecycle(meeting_id: int) -> LifecycleStatus:
    """Derive the right lifecycle_status from the DB state right now.

    Same rules as api/adapters.py:derive_status but with explicit phase names.
    """
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("""
                SELECT
                    EXISTS(
                        SELECT 1 FROM summary_versions sv
                        WHERE sv.entity_type='meeting' AND sv.entity_id=%s
                          AND sv.is_manual=true AND sv.status IN ('approved','draft')
                    ) AS has_manual,
                    EXISTS(
                        SELECT 1 FROM summary_versions sv
                        WHERE sv.entity_type='meeting' AND sv.entity_id=%s
                          AND sv.status IN ('approved','draft')
                          AND sv.created_by != 'autosave'
                    ) AS has_summary,
                    (SELECT COUNT(*) FROM documents WHERE meeting_id=%s) AS doc_count,
                    (SELECT COUNT(*) FROM agenda_items
                       WHERE meeting_id=%s AND COALESCE(inactive,false)=false) AS item_count
            """, (meeting_id, meeting_id, meeting_id, meeting_id))
            row = cur.fetchone()
    if not row:
        return "discovered"
    if row["has_manual"]:
        return "approved"
    if row["has_summary"]:
        return "summarized"
    if (row["doc_count"] or 0) > 0:
        return "materials_posted"
    if (row["item_count"] or 0) > 0:
        return "agenda_posted"
    return "discovered"


def bump_lifecycle(meeting_id: int) -> LifecycleStatus:
    """Recompute and persist the meeting's lifecycle_status. Returns the new value."""
    status = compute_lifecycle(meeting_id)
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "UPDATE meetings SET lifecycle_status=%s WHERE id=%s",
                (status, meeting_id),
            )
    return status


def is_at_least(current: LifecycleStatus, target: LifecycleStatus) -> bool:
    """True if `current` is >= `target` in the lifecycle ordering."""
    return _ORDER.index(current) >= _ORDER.index(target)


def hash_agenda_doc(content: bytes) -> str:
    """SHA-256 of raw agenda doc bytes — used to detect when the venue
    has posted an updated agenda PDF and we should re-parse."""
    return hashlib.sha256(content).hexdigest()
