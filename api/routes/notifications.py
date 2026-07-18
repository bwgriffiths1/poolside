"""In-app notifications.

Notifications are created by:
  - api/routes/briefings.py when a briefing transitions to 'approved'
  - api/scheduler.py drift alarm when no discoveries land for 48h
  - any future system event that wants to ping users

The sidebar bell polls /unread-count; the dropdown lists recent rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from pipeline import db
from ..auth import current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _serialize(row: dict) -> dict[str, Any]:
    out = dict(row)
    for k in ("created_at", "read_at"):
        v = out.get(k)
        if v is not None and hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    # payload comes back from psycopg2 as a dict already if column is jsonb
    return out


@router.get("")
def list_notifications(
    user: dict = Depends(current_user),
    limit: int = 30,
    include_read: bool = False,
) -> list[dict[str, Any]]:
    """Most-recent notifications for the current user. Broadcasts
    (user_id IS NULL, e.g. drift alarms) are folded in for everyone."""
    user_id = user["id"]
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT *
                FROM notifications
                WHERE (user_id = %s OR user_id IS NULL)
                  {"" if include_read else "AND read_at IS NULL"}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            rows = [_serialize(dict(r)) for r in cur.fetchall()]
    return rows


@router.get("/unread-count")
def unread_count(user: dict = Depends(current_user)) -> dict[str, int]:
    user_id = user["id"]
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT COUNT(*) AS n
                     FROM notifications
                    WHERE (user_id = %s OR user_id IS NULL)
                      AND read_at IS NULL""",
                (user_id,),
            )
            row = cur.fetchone()
    return {"count": int(row["n"]) if row else 0}


@router.post("/mark-read")
def mark_read(
    body: dict[str, Any] | None = None,
    user: dict = Depends(current_user),
) -> dict[str, int]:
    """Mark a set of notification ids as read, or all of them when no ids
    are supplied. Body: { "ids": [int, int, ...] } or {}.
    """
    ids = (body or {}).get("ids")
    user_id = user["id"]
    now = datetime.now(timezone.utc)
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            if ids and isinstance(ids, list):
                cur.execute(
                    """UPDATE notifications
                          SET read_at = %s
                        WHERE read_at IS NULL
                          AND id = ANY(%s)
                          AND (user_id = %s OR user_id IS NULL)""",
                    (now, ids, user_id),
                )
            else:
                cur.execute(
                    """UPDATE notifications
                          SET read_at = %s
                        WHERE read_at IS NULL
                          AND (user_id = %s OR user_id IS NULL)""",
                    (now, user_id),
                )
            count = cur.rowcount or 0
    return {"marked_read": int(count)}
