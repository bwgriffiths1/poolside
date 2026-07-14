"""Auth routes — local email/password login + logout."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from pipeline.auth import authenticate_user
from .. import schemas
from ..auth import clear_session_cookie, set_session_cookie

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


# ── Login rate limiting ─────────────────────────────────────────────────
# In-memory sliding window, keyed by client IP and by target email. Only
# FAILED attempts count; a success clears both keys. Per-process state is
# fine — we run a single uvicorn worker.
_FAILED: dict[str, deque[float]] = defaultdict(deque)
_WINDOW_SECONDS = 300.0
_MAX_FAILURES = 10


def _client_ip(request: Request) -> str:
    # Railway terminates TLS at its proxy; the real client is the first
    # entry of X-Forwarded-For.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_limited(key: str) -> bool:
    now = time.monotonic()
    q = _FAILED[key]
    while q and now - q[0] > _WINDOW_SECONDS:
        q.popleft()
    return len(q) >= _MAX_FAILURES


def _record_failure(key: str) -> None:
    _FAILED[key].append(time.monotonic())


def _to_current_user(user: dict) -> schemas.CurrentUser:
    name = (user.get("name") or user.get("email") or "User").strip()
    parts = [p for p in name.split() if p]
    initials = (
        (parts[0][0] + parts[-1][0]).upper()
        if len(parts) >= 2
        else (parts[0][:2].upper() if parts else "U")
    )
    return schemas.CurrentUser(
        name=name,
        email=user.get("email", ""),
        initials=initials,
    )


@router.post("/login", response_model=schemas.CurrentUser)
def login(body: LoginRequest, request: Request, response: Response) -> schemas.CurrentUser:
    ip_key = f"ip:{_client_ip(request)}"
    email_key = f"email:{body.email.strip().lower()}"
    if _is_limited(ip_key) or _is_limited(email_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again in a few minutes.",
        )
    user = authenticate_user(body.email, body.password)
    if user is None:
        _record_failure(ip_key)
        _record_failure(email_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    _FAILED.pop(ip_key, None)
    _FAILED.pop(email_key, None)
    set_session_cookie(response, user["email"])
    return _to_current_user(user)


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    clear_session_cookie(response)
    return {"ok": True}
