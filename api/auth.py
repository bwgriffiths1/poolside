"""FastAPI-native session auth.

Issues an HTTP-only signed cookie after a successful local password check.
Mirrors the cookie shape used by pipeline/auth.py so the same DB users work
in both apps. OAuth providers can hydrate the same cookie later.

Every router except the explicit public surface (auth, health, /api/public/*)
is gated with a router-level `Depends(current_user)` in api/main.py. The
signing secret must be provided via POOLSIDE_SESSION_SECRET; for local
development without one, set POOLSIDE_INSECURE_DEV=1 explicitly.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import Cookie, HTTPException, Response, status

from pipeline.auth import get_user_by_email

SESSION_COOKIE = "poolside_session"
_MAX_AGE = 7 * 24 * 3600  # 1 week


def _secret() -> bytes:
    s = os.environ.get("POOLSIDE_SESSION_SECRET")
    if s:
        return s.encode()
    if os.environ.get("POOLSIDE_INSECURE_DEV") == "1":
        return b"dev-secret-change-me"
    raise RuntimeError(
        "POOLSIDE_SESSION_SECRET is not set. Generate one with "
        "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` and set it "
        "in the environment, or set POOLSIDE_INSECURE_DEV=1 for local development."
    )


def require_secret() -> None:
    """Startup guard: crash loudly if no usable signing secret is configured.

    Called from the app lifespan so a misconfigured deploy fails its
    healthcheck instead of silently signing cookies with a known default.
    """
    _secret()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_cookie(email: str) -> str:
    expiry = int(time.time()) + _MAX_AGE
    payload = f"{email}|{expiry}"
    return f"{payload}|{_sign(payload)}"


def verify_session_cookie(raw: str) -> str | None:
    """Return email if the cookie is valid and unexpired, else None."""
    if not raw:
        return None
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    email, expiry_str, sig = parts
    payload = f"{email}|{expiry_str}"
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        if int(expiry_str) < int(time.time()):
            return None
    except ValueError:
        return None
    return email


def set_session_cookie(response: Response, email: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=make_session_cookie(email),
        max_age=_MAX_AGE,
        httponly=True,
        samesite="lax",
        # Secure by default — opt OUT for plain-http local dev (Safari won't
        # set Secure cookies on http://localhost).
        secure=os.environ.get("POOLSIDE_COOKIE_SECURE", "1") == "1",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def current_user(poolside_session: str | None = Cookie(default=None)) -> dict:
    """FastAPI dependency: returns the authenticated user dict or 401s."""
    email = verify_session_cookie(poolside_session or "")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user = get_user_by_email(email)
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return user
