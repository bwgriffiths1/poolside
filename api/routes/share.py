"""Public share links for meeting briefings.

An admin user generates a token for a meeting; the resulting URL
(`/share/<token>` on the frontend, hitting `/api/public/share/<token>`)
renders the Briefing reader read-only, without requiring login. Tokens
can be revoked or have an expiry.

Auth: token management is auth-protected; the public render endpoint is
intentionally NOT protected — that's the whole point.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pipeline import db_new as db
from .. import adapters, briefing_parser
from ..auth import current_user

router = APIRouter(prefix="/api", tags=["share"])


def _generate_token() -> str:
    # ~32 chars of url-safe randomness; URL-friendly + plenty of entropy.
    return secrets.token_urlsafe(24)


def _serialize_token(row: dict) -> dict[str, Any]:
    out = dict(row)
    for k in ("created_at", "expires_at", "revoked_at"):
        v = out.get(k)
        if v is not None and hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


@router.post("/meetings/{meeting_id}/share")
def create_share_link(
    meeting_id: int,
    body: dict[str, Any] | None = None,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Mint a new share token for this meeting's briefing.

    Body (optional): { "expires_days": 30 }  — null/missing = no expiry.
    """
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    expires_at = None
    if body and body.get("expires_days") is not None:
        try:
            days = int(body["expires_days"])
            if days > 0:
                expires_at = datetime.now(timezone.utc) + timedelta(days=days)
        except (TypeError, ValueError):
            expires_at = None

    token = _generate_token()
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO share_tokens
                       (token, meeting_id, created_by, expires_at)
                   VALUES (%s, %s, %s, %s)
                   RETURNING *""",
                (token, meeting_id, user["id"], expires_at),
            )
            row = dict(cur.fetchone())
    return _serialize_token(row)


@router.get("/meetings/{meeting_id}/share")
def list_share_links(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT * FROM share_tokens
                    WHERE meeting_id = %s
                 ORDER BY created_at DESC""",
                (meeting_id,),
            )
            return [_serialize_token(dict(r)) for r in cur.fetchall()]


@router.delete("/share-tokens/{token_id}")
def revoke_share(
    token_id: int,
    _: dict = Depends(current_user),
) -> dict[str, bool]:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """UPDATE share_tokens
                       SET revoked_at = NOW()
                     WHERE id = %s
                       AND revoked_at IS NULL""",
                (token_id,),
            )
            ok = bool(cur.rowcount)
    return {"revoked": ok}


# ── Public, no-auth render endpoint ────────────────────────────────────


@router.get("/public/share/{token}")
def public_share_render(token: str) -> dict[str, Any]:
    """Public briefing payload — same shape as /api/meetings/:id/briefing,
    but reachable without a session cookie. Returns 404 for missing,
    revoked, or expired tokens."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                SELECT st.*, m.id AS meeting_id, m.title AS meeting_title,
                       m.meeting_date, m.location,
                       mt.name AS type_name, mt.short_name AS type_short,
                       v.short_name AS venue_short, v.name AS venue_name
                  FROM share_tokens st
                  JOIN meetings m       ON m.id  = st.meeting_id
                  JOIN meeting_types mt ON mt.id = m.meeting_type_id
                  JOIN venues v         ON v.id  = mt.venue_id
                 WHERE st.token = %s
                """,
                (token,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Share link not found")
    row = dict(row)
    if row.get("revoked_at"):
        raise HTTPException(status_code=410, detail="Share link revoked")
    expires_at = row.get("expires_at")
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Share link expired")

    summary = db.get_current_summary("meeting", row["meeting_id"])
    if summary is None:
        raise HTTPException(status_code=404, detail="No briefing for this meeting")

    md = adapters.resolve_image_refs(
        summary.get("detailed") or summary.get("one_line") or ""
    )
    meta = {
        "title": f"{row.get('type_name') or ''} — {row.get('meeting_date') or ''}",
        "subtitle": f"{row.get('venue_name') or ''} · {row.get('location') or ''}",
        "headline": summary.get("one_line") or "",
        "generated_at": str(summary.get("created_at", "")),
        "model": summary.get("model") or summary.get("created_by") or "",
    }
    briefing = briefing_parser.parse_briefing_markdown(md, meta)
    return {
        "venue": row.get("venue_short"),
        "type_short": row.get("type_short"),
        "type_name": row.get("type_name"),
        "meeting_date": str(row.get("meeting_date") or ""),
        "briefing": briefing.model_dump(),
    }
