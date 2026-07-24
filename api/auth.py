"""FastAPI-native session auth + role gates.

Issues an HTTP-only signed cookie after a successful local password check.
Mirrors the cookie shape used by pipeline/auth.py so the same DB users work
in both apps. OAuth providers can hydrate the same cookie later.

Every router except the explicit public surface (auth, health, /api/public/*)
is gated with router-level dependencies in api/main.py — `current_user` for
session auth plus one of the role gates below (viewer / editor / admin
tiers). The mount groups in main.py ARE the permission policy; per-endpoint
gates exist only on the mixed public routers (share, user_tokens) and inside
admin.py. The signing secret must be provided via POOLSIDE_SESSION_SECRET;
for local development without one, set POOLSIDE_INSECURE_DEV=1 explicitly.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import Cookie, Depends, HTTPException, Request, Response, status

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


def current_user(
    request: Request,
    poolside_session: str | None = Cookie(default=None),
) -> dict:
    """FastAPI dependency: returns the authenticated user dict or 401s.

    Reads the user fresh from the DB every request, so role changes and
    deactivation take effect immediately — a stale cookie stays valid but
    powerless. The user is stashed on request.state for the audit
    middleware (state lives in the shared ASGI scope, so values set during
    dependency resolution are visible to middleware after call_next).
    """
    email = verify_session_cookie(poolside_session or "")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user = get_user_by_email(email)
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    request.state.user = user
    return user


# ── Role gates ──────────────────────────────────────────────────────────
# Three tiers: admin > editor > viewer. Viewers are read-only plus the
# self-service surface (prefs, mark-read, watches, Ask) — enforced by which
# gate each router is mounted with in api/main.py, not by path matching.
# 403 details are deliberately distinct from the 401 "not authenticated".

VALID_ROLES = ("admin", "editor", "viewer")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Requires admin access")
    return user


def require_editor(user: dict = Depends(current_user)) -> dict:
    """Any-method editor gate — for individual write endpoints on routers
    that aren't mounted with a role dependency (share management)."""
    if user.get("role") not in ("admin", "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Requires editor access")
    return user


def require_editor_for_writes(
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    """Router-level gate: GETs stay open to every role; writes need editor+."""
    if (request.method not in _SAFE_METHODS
            and user.get("role") not in ("admin", "editor")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Requires editor access")
    return user
