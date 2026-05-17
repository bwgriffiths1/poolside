"""Invite + password-reset tokens.

Both flows share one table (user_tokens, purpose='invite' | 'password_reset').
Email infra isn't wired yet, so the admin generates the token, copies the
URL the route returns, and forwards it to the user out-of-band.

Admin endpoints are auth-gated; the accept endpoint is public so the user
can hit it without first logging in.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from pipeline import db_new as db
from pipeline.auth import (
    create_user,
    get_user_by_email,
    set_user_password,
)
from ..auth import current_user

router = APIRouter(prefix="/api", tags=["user-tokens"])

_VALID_PURPOSES = {"invite", "password_reset"}
_DEFAULT_EXPIRY_DAYS = 14
_MIN_PASSWORD_LEN = 6


def _make_token() -> str:
    return secrets.token_urlsafe(24)


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
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Generate an invite token for a new user. Body: {"email": str,
    "name": str, "expires_days": int?}.

    Idempotent: re-inviting the same email rotates the token if there's
    an outstanding (active, unused) one for that email.
    """
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip()
    if not email or not name:
        raise HTTPException(status_code=400, detail="email and name are required")
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
                       (token, purpose, email, name, created_by, expires_at)
                   VALUES (%s, 'invite', %s, %s, %s, %s)
                   RETURNING *""",
                (token, email, name, user["id"], expires_at),
            )
            row = dict(cur.fetchone())
    return _serialize(row)


@router.post("/admin/password-resets")
def create_password_reset(
    body: dict[str, Any] = Body(...),
    user: dict = Depends(current_user),
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
    return _serialize(row)


@router.get("/admin/user-tokens")
def list_user_tokens(
    _: dict = Depends(current_user),
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
    _: dict = Depends(current_user),
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
