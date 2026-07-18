"""Notification writers — used by routes, the scheduler, and the orchestrator."""
from __future__ import annotations

from typing import Any

from pipeline import db

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
