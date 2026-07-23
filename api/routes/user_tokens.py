"""Invite + password-reset tokens.

Both flows share one table (user_tokens, purpose='invite' | 'password_reset').
Invites carry the role the admin chose; the accepting user is created with
it. When the mailer is configured the token URL is emailed to the target
(best-effort, off-thread); either way the response includes accept_url so
the admin can always copy it and forward it out-of-band.

Admin endpoints require the admin role; the accept endpoint is public so
the user can hit it without first logging in.
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from pipeline import db
from pipeline.auth import (
    create_user,
    get_user_by_email,
    set_user_password,
)
from ..auth import VALID_ROLES, require_admin
from ..services import mailer

log = logging.getLogger("poolside.user_tokens")

router = APIRouter(prefix="/api", tags=["user-tokens"])

_VALID_PURPOSES = {"invite", "password_reset"}
_DEFAULT_EXPIRY_DAYS = 14
_MIN_PASSWORD_LEN = 6


def _make_token() -> str:
    return secrets.token_urlsafe(24)


def _accept_url(token: str) -> str:
    return f"{mailer._base_url()}/#/accept/{token}"


def _send_token_email(kind: str, to: str, payload: dict[str, Any]) -> None:
    """Best-effort send, off the request thread (mirrors the approve-flow
    fan-out in briefings.py) — a slow mail API can't drag out the response,
    and a failure only logs; the copy-URL in the response is the fallback."""
    def _send() -> None:
        try:
            if kind == "invite":
                subject, html_body = mailer.invite_email(payload)
            else:
                subject, html_body = mailer.password_reset_email(payload)
            mailer.send_email(to, subject, html_body)
        except Exception:  # never let email break token creation
            log.exception("%s email to %s failed", kind, to)

    threading.Thread(target=_send, daemon=True,
                     name=f"user-token-mail-{kind}").start()


def _serialize(row: dict) -> dict[str, Any]:
    out = dict(row)
    for k in ("created_at", "expires_at", "used_at"):
        v = out.get(k)
        if v is not None and hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


def _status(row: dict) -> str:
    if row.get("used_at"):
        return "used"
    exp = row.get("expires_at")
    if exp and exp < datetime.now(timezone.utc):
        return "expired"
    return "active"


# ── Admin endpoints ────────────────────────────────────────────────────


@router.post("/admin/invites")
def create_invite(
    body: dict[str, Any] = Body(...),
    user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Generate an invite token for a new user. Body: {"email": str,
    "name": str, "role": str?, "expires_days": int?}.

    Idempotent: re-inviting the same email rotates the token if there's
    an outstanding (active, unused) one for that email.
    """
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip()
    if not email or not name:
        raise HTTPException(status_code=400, detail="email and name are required")
    role = (body.get("role") or "viewer").strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of: {', '.join(VALID_ROLES)}",
        )
    if get_user_by_email(email):
        raise HTTPException(
            status_code=409,
            detail=f"{email} is already a user — use a password reset instead.",
        )
    days = body.get("expires_days")
    try:
        days = int(days) if days is not None else _DEFAULT_EXPIRY_DAYS
    except (TypeError, ValueError):
        days = _DEFAULT_EXPIRY_DAYS
    expires_at = datetime.now(timezone.utc) + timedelta(days=days) if days > 0 else None

    token = _make_token()
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO user_tokens
                       (token, purpose, email, name, role, created_by, expires_at)
                   VALUES (%s, 'invite', %s, %s, %s, %s, %s)
                   RETURNING *""",
                (token, email, name, role, user["id"], expires_at),
            )
            row = dict(cur.fetchone())

    emailed = mailer.mail_enabled()
    if emailed:
        _send_token_email("invite", email, {
            "name": name,
            "email": email,
            "role": role,
            "accept_url": _accept_url(token),
            "expires_days": days if days > 0 else None,
            "invited_by": user.get("name") or user.get("email") or "",
        })
    # emailed means "queued" — the send is off-thread and best-effort; the
    # accept_url is always present so the admin can copy it either way.
    return {**_serialize(row), "emailed": emailed, "accept_url": _accept_url(token)}


@router.post("/admin/password-resets")
def create_password_reset(
    body: dict[str, Any] = Body(...),
    user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Generate a password-reset token for an existing user. Body:
    {"email": str, "expires_days": int?}."""
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    target = get_user_by_email(email)
    if not target:
        raise HTTPException(status_code=404, detail=f"No user with email {email}")
    days = body.get("expires_days")
    try:
        days = int(days) if days is not None else 7
    except (TypeError, ValueError):
        days = 7
    expires_at = datetime.now(timezone.utc) + timedelta(days=days) if days > 0 else None

    token = _make_token()
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO user_tokens
                       (token, purpose, email, name, created_by, expires_at)
                   VALUES (%s, 'password_reset', %s, %s, %s, %s)
                   RETURNING *""",
                (token, email, target.get("name"), user["id"], expires_at),
            )
            row = dict(cur.fetchone())

    emailed = mailer.mail_enabled()
    if emailed:
        _send_token_email("password_reset", email, {
            "name": target.get("name") or email,
            "email": email,
            "accept_url": _accept_url(token),
            "expires_days": days if days > 0 else None,
        })
    return {**_serialize(row), "emailed": emailed, "accept_url": _accept_url(token)}


@router.get("/admin/user-tokens")
def list_user_tokens(
    _: dict = Depends(require_admin),
    purpose: str | None = None,
) -> list[dict[str, Any]]:
    """List recent invite + reset tokens. Filter with ?purpose=invite or
    ?purpose=password_reset."""
    where = ""
    params: list[Any] = []
    if purpose:
        if purpose not in _VALID_PURPOSES:
            raise HTTPException(status_code=400, detail="bad purpose")
        where = "WHERE purpose = %s"
        params.append(purpose)
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                f"""SELECT * FROM user_tokens
                    {where}
                    ORDER BY created_at DESC
                    LIMIT 50""",
                params,
            )
            rows = [_serialize(dict(r)) for r in cur.fetchall()]
    for r in rows:
        r["status"] = _status({
            "used_at": (
                datetime.fromisoformat(r["used_at"]) if r.get("used_at") else None
            ),
            "expires_at": (
                datetime.fromisoformat(r["expires_at"]) if r.get("expires_at") else None
            ),
        })
    return rows


@router.delete("/admin/user-tokens/{token_id}")
def revoke_token(
    token_id: int,
    _: dict = Depends(require_admin),
) -> dict[str, bool]:
    """Hard-delete a token (revoke). The token's URL stops working
    immediately."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("DELETE FROM user_tokens WHERE id = %s", (token_id,))
            ok = bool(cur.rowcount)
    return {"revoked": ok}


# ── Public endpoints (no auth) ─────────────────────────────────────────


@router.get("/public/user-tokens/{token}")
def public_token_preview(token: str) -> dict[str, Any]:
    """Return purpose + email + name for the accept page to render. 404 if
    missing; 410 if revoked / used / expired."""
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT * FROM user_tokens WHERE token = %s", (token,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")
    row = dict(row)
    if row.get("used_at"):
        raise HTTPException(status_code=410, detail="Token already used")
    exp = row.get("expires_at")
    if exp and exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Token expired")
    return {
        "purpose": row["purpose"],
        "email": row["email"],
        "name": row.get("name"),
        "role": row.get("role") if row["purpose"] == "invite" else None,
        "expires_at": row["expires_at"].isoformat()
            if row.get("expires_at") is not None else None,
    }


@router.post("/public/user-tokens/{token}/accept")
def public_token_accept(
    token: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Set a password using a valid invite or reset token. Body:
    {"password": str}. On success the token is marked used."""
    password = (body.get("password") or "").strip()
    if len(password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters.",
        )

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("SELECT * FROM user_tokens WHERE token = %s", (token,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Token not found")
            row = dict(row)
            if row.get("used_at"):
                raise HTTPException(status_code=410, detail="Token already used")
            exp = row.get("expires_at")
            if exp and exp < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Token expired")

            email = row["email"]
            purpose = row["purpose"]

            if purpose == "invite":
                if get_user_by_email(email):
                    # Race: someone already onboarded this email another way.
                    raise HTTPException(
                        status_code=409,
                        detail="A user with this email already exists.",
                    )
                create_user(
                    email=email,
                    name=row.get("name") or email,
                    password=password,
                    # Invites minted before migration 016 carry 'viewer'.
                    role=row.get("role") or "viewer",
                )
            elif purpose == "password_reset":
                target = get_user_by_email(email)
                if not target:
                    raise HTTPException(status_code=404, detail="User not found")
                set_user_password(target["id"], password)
            else:
                raise HTTPException(status_code=400, detail="Unknown token purpose")

            cur.execute(
                "UPDATE user_tokens SET used_at = NOW() WHERE id = %s",
                (row["id"],),
            )

    return {"ok": True, "purpose": purpose, "email": email}
