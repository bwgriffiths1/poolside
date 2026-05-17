"""Per-user meeting watches.

When a user "watches" a meeting, they get an in-app notification when
something noteworthy happens to it (today: briefing approved). The model
is intentionally minimal — one row per (user, meeting). Per-committee or
per-tag watching can layer on later if needed.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pipeline import db_new as db
from ..auth import current_user

router = APIRouter(prefix="/api/watches", tags=["watches"])


@router.get("")
def list_my_watches(user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """Return every meeting this user watches, with light meeting metadata."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                SELECT w.id        AS watch_id,
                       w.meeting_id,
                       w.created_at AS watched_at,
                       m.title,
                       m.meeting_date,
                       v.short_name AS venue,
                       mt.short_name AS type_short
                  FROM meeting_watches w
                  JOIN meetings m       ON m.id  = w.meeting_id
                  JOIN meeting_types mt ON mt.id = m.meeting_type_id
                  JOIN venues v         ON v.id  = mt.venue_id
                 WHERE w.user_id = %s
              ORDER BY m.meeting_date DESC
                """,
                (user["id"],),
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        for k in ("watched_at", "meeting_date"):
            v = r.get(k)
            if v is not None and hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return rows


@router.get("/by-meeting/{meeting_id}")
def is_watching(
    meeting_id: int,
    user: dict = Depends(current_user),
) -> dict[str, bool]:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "SELECT 1 FROM meeting_watches WHERE user_id = %s AND meeting_id = %s",
                (user["id"], meeting_id),
            )
            return {"watching": cur.fetchone() is not None}


@router.post("/by-meeting/{meeting_id}")
def watch(
    meeting_id: int,
    user: dict = Depends(current_user),
) -> dict[str, bool]:
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO meeting_watches (user_id, meeting_id)
                   VALUES (%s, %s)
                   ON CONFLICT (user_id, meeting_id) DO NOTHING""",
                (user["id"], meeting_id),
            )
    return {"watching": True}


@router.delete("/by-meeting/{meeting_id}")
def unwatch(
    meeting_id: int,
    user: dict = Depends(current_user),
) -> dict[str, bool]:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "DELETE FROM meeting_watches WHERE user_id = %s AND meeting_id = %s",
                (user["id"], meeting_id),
            )
    return {"watching": False}
