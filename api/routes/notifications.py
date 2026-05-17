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

from fastapi import APIRouter, Depends, HTTPException

from pipeline import db_new as db
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


# ── Helpers used by other routes/jobs (not endpoints) ───────────────────


def create_notification(
    kind: str,
    user_id: int | None,
    meeting_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Insert a notification. user_id=None makes it a broadcast that
    everyone sees in their inbox. Returns the new id."""
    import json
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO notifications (user_id, kind, payload, meeting_id)
                   VALUES (%s, %s, %s::jsonb, %s)
                   RETURNING id""",
                (user_id, kind, json.dumps(payload or {}), meeting_id),
            )
            return int(cur.fetchone()["id"])


def fan_out_to_watchers(
    meeting_id: int,
    kind: str,
    payload: dict[str, Any] | None = None,
    exclude_user_id: int | None = None,
) -> int:
    """Insert one notification per watcher of a meeting. Returns count."""
    import json
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT user_id FROM meeting_watches WHERE meeting_id = %s""",
                (meeting_id,),
            )
            watcher_ids = [r["user_id"] for r in cur.fetchall()]
            if exclude_user_id is not None:
                watcher_ids = [u for u in watcher_ids if u != exclude_user_id]
            if not watcher_ids:
                return 0
            # Single multi-row insert.
            from psycopg2.extras import execute_values
            execute_values(
                cur,
                """INSERT INTO notifications (user_id, kind, payload, meeting_id)
                   VALUES %s""",
                [(uid, kind, json.dumps(payload or {}), meeting_id) for uid in watcher_ids],
                template="(%s, %s, %s::jsonb, %s)",
            )
    return len(watcher_ids)
