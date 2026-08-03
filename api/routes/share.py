"""Public share links for meeting briefings and FERC dockets.

An editor generates a token for a meeting or a docket; the resulting URL
(`/share/<token>` on the frontend, hitting `/api/public/share/<token>`)
renders the target read-only, without requiring login. One token table
covers both kinds (share_tokens.meeting_id XOR .docket_id); the public
payload carries a "kind" discriminator so the frontend picks the reader.
Tokens can be revoked or have an expiry.

Auth: token management is auth-protected; the public render endpoints are
intentionally NOT protected — that's the whole point.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from pipeline import db, storage
from .. import adapters, briefing_parser
from ..auth import current_user, require_editor
from . import dockets as dockets_routes

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


def _parse_expiry(body: dict[str, Any] | None) -> datetime | None:
    """Body (optional): { "expires_days": 30 } — null/missing = no expiry."""
    if body and body.get("expires_days") is not None:
        try:
            days = int(body["expires_days"])
            if days > 0:
                return datetime.now(timezone.utc) + timedelta(days=days)
        except (TypeError, ValueError):
            return None
    return None


def _mint_token(target_col: str, target_id: int, user_id: int,
                expires_at: datetime | None) -> dict[str, Any]:
    # target_col is a code-supplied literal ("meeting_id" | "docket_id"),
    # never user input — same for _list_tokens.
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                f"""INSERT INTO share_tokens
                        (token, {target_col}, created_by, expires_at)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *""",
                (_generate_token(), target_id, user_id, expires_at),
            )
            row = dict(cur.fetchone())
    return _serialize_token(row)


def _list_tokens(target_col: str, target_id: int) -> list[dict[str, Any]]:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                f"""SELECT * FROM share_tokens
                     WHERE {target_col} = %s
                  ORDER BY created_at DESC""",
                (target_id,),
            )
            return [_serialize_token(dict(r)) for r in cur.fetchall()]


@router.post("/meetings/{meeting_id}/share")
def create_share_link(
    meeting_id: int,
    body: dict[str, Any] | None = None,
    user: dict = Depends(require_editor),
) -> dict[str, Any]:
    """Mint a new share token for this meeting's briefing."""
    if db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return _mint_token("meeting_id", meeting_id, user["id"], _parse_expiry(body))


@router.get("/meetings/{meeting_id}/share")
def list_share_links(
    meeting_id: int,
    _: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    return _list_tokens("meeting_id", meeting_id)


@router.post("/dockets/{docket_id}/share")
def create_docket_share_link(
    docket_id: int,
    body: dict[str, Any] | None = None,
    user: dict = Depends(require_editor),
) -> dict[str, Any]:
    """Mint a new share token for this docket."""
    if db.get_docket(docket_id) is None:
        raise HTTPException(status_code=404, detail="Docket not found")
    return _mint_token("docket_id", docket_id, user["id"], _parse_expiry(body))


@router.get("/dockets/{docket_id}/share")
def list_docket_share_links(
    docket_id: int,
    _: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    return _list_tokens("docket_id", docket_id)


@router.delete("/share-tokens/{token_id}")
def revoke_share(
    token_id: int,
    _: dict = Depends(require_editor),
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


# ── Public, no-auth render endpoints ───────────────────────────────────


def _fetch_valid_token(token: str) -> dict[str, Any]:
    """The share_tokens row for a live token; raise 404 for missing,
    410 for revoked or expired."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM share_tokens WHERE token = %s", (token,)
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
    return row


def _join_meeting(tok: dict[str, Any]) -> dict[str, Any]:
    """Token row + its meeting's display metadata."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                SELECT m.id AS meeting_id, m.title AS meeting_title,
                       m.meeting_date, m.location, m.external_id,
                       mt.name AS type_name, mt.short_name AS type_short,
                       v.short_name AS venue_short, v.name AS venue_name
                  FROM meetings m
                  JOIN meeting_types mt ON mt.id = m.meeting_type_id
                  JOIN venues v         ON v.id  = mt.venue_id
                 WHERE m.id = %s
                """,
                (tok["meeting_id"],),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Share link not found")
    return {**tok, **dict(row)}


def _load_valid_share(token: str) -> dict[str, Any]:
    """Meeting-targeted token joined to meeting metadata. 404s for docket
    tokens — the meeting-scoped image endpoints below call this, and a
    docket token must not reach meeting images."""
    tok = _fetch_valid_token(token)
    if not tok.get("meeting_id"):
        raise HTTPException(status_code=404, detail="Share link not found")
    return _join_meeting(tok)


@router.get("/public/share/{token}")
def public_share_render(token: str) -> dict[str, Any]:
    """Public payload for a share link, discriminated by "kind": meeting
    tokens return the briefing shape (kind="meeting"), docket tokens the
    docket-detail shape (kind="docket"). Reachable without a session
    cookie; 404/410 for missing, revoked, or expired tokens."""
    tok = _fetch_valid_token(token)

    if tok.get("docket_id"):
        payload = dockets_routes.docket_detail_payload(tok["docket_id"])
        if payload is None:
            raise HTTPException(status_code=404, detail="Docket not found")
        return {"kind": "docket", **payload}

    row = _join_meeting(tok)
    summary = db.get_current_summary("meeting", row["meeting_id"])
    if summary is None:
        raise HTTPException(status_code=404, detail="No briefing for this meeting")

    md = adapters.resolve_image_refs(
        summary.get("detailed") or summary.get("one_line") or ""
    )
    # The session-gated image routes 401 for anonymous share viewers, so
    # rewrite image URLs to the token-scoped public equivalents below.
    md = md.replace("/api/images/", f"/api/public/share/{token}/images/")
    md = md.replace("/api/editor-images/", f"/api/public/share/{token}/editor-images/")
    meta = {
        "title": f"{row.get('type_name') or ''} — {row.get('meeting_date') or ''}",
        "subtitle": f"{row.get('venue_name') or ''} · {row.get('location') or ''}",
        "headline": summary.get("one_line") or "",
        "generated_at": str(summary.get("created_at", "")),
        "model": summary.get("model") or summary.get("created_by") or "",
    }
    briefing = briefing_parser.parse_briefing_markdown(md, meta)
    adapters.attach_briefing_docs(briefing, row["meeting_id"])
    return {
        "kind": "meeting",
        "venue": row.get("venue_short"),
        "type_short": row.get("type_short"),
        "type_name": row.get("type_name"),
        "meeting_date": str(row.get("meeting_date") or ""),
        "external_id": row.get("external_id") or "",
        "briefing": briefing.model_dump(),
    }


@router.get("/public/share/{token}/images/{image_id}")
def public_share_document_image(token: str, image_id: int) -> Response:
    """Document-extracted image for a shared briefing. Only serves images
    whose source document belongs to the shared meeting."""
    row = _load_valid_share(token)
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT di.id, di.image_b64, di.storage_key
                     FROM document_images di
                     JOIN documents d ON d.id = di.document_id
                    WHERE di.id = %s AND d.meeting_id = %s""",
                (image_id, row["meeting_id"]),
            )
            img = cur.fetchone()
    raw = storage.get_image_bytes(dict(img)) if img else None
    if raw is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(
        content=raw,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/public/share/{token}/files/{file_row_id}/download")
def public_share_filing_file(token: str, file_row_id: int) -> Response:
    """FERC passthrough for a shared docket's filing file — scoped so a
    token only reaches files inside its own docket."""
    tok = _fetch_valid_token(token)
    row = db.get_docket_filing_file(file_row_id)
    if (
        not tok.get("docket_id")
        or not row
        or row["docket_id"] != tok["docket_id"]
    ):
        raise HTTPException(status_code=404, detail="File not found")
    return dockets_routes.serve_filing_file(row)


@router.get("/public/share/{token}/editor-images/{image_id}")
def public_share_editor_image(token: str, image_id: int) -> Response:
    """Editor-pasted image for a shared briefing, scoped to the shared
    meeting."""
    row = _load_valid_share(token)
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """SELECT mime_type, data FROM editor_images
                    WHERE id = %s AND meeting_id = %s""",
                (image_id, row["meeting_id"]),
            )
            img = cur.fetchone()
    if img is None:
        raise HTTPException(status_code=404, detail="Image not found")
    raw = bytes(img["data"]) if isinstance(img["data"], memoryview) else img["data"]
    return Response(
        content=raw,
        media_type=img["mime_type"] or "image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
