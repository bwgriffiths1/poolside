"""Current user — sidebar chip + personal preferences."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from pipeline import db

from .. import schemas
from ..auth import current_user

router = APIRouter(prefix="/api", tags=["me"])

# The only email prefs that exist; PATCH bodies are whitelisted to these.
_EMAIL_PREF_KEYS = {"briefing_ready", "weekly_digest"}


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    if parts:
        return parts[0][:2].upper()
    return "U"


@router.get("/me", response_model=schemas.CurrentUser)
def me(user: dict = Depends(current_user)) -> schemas.CurrentUser:
    name = (user.get("name") or user.get("email") or "User").strip()
    return schemas.CurrentUser(
        name=name,
        email=user.get("email", ""),
        initials=_initials(name),
    )


def _pref_shape(prefs: dict) -> dict[str, bool]:
    return {k: bool(prefs.get(k)) for k in sorted(_EMAIL_PREF_KEYS)}


@router.get("/me/prefs")
def my_prefs(user: dict = Depends(current_user)) -> dict[str, Any]:
    from ..services.mailer import mail_enabled

    return {
        "email_prefs": _pref_shape(db.get_user_email_prefs(user["id"])),
        # Lets Settings explain why toggles are inert on an unconfigured deploy.
        "mail_configured": mail_enabled(),
    }


@router.patch("/me/prefs")
def update_my_prefs(
    body: dict = Body(...),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    email_prefs = body.get("email_prefs") or {}
    updates = {k: bool(v) for k, v in email_prefs.items() if k in _EMAIL_PREF_KEYS}
    if not updates:
        raise HTTPException(status_code=400, detail="No recognised prefs in body")
    merged = db.set_user_email_prefs(user["id"], updates)
    from ..services.mailer import mail_enabled

    return {
        "email_prefs": _pref_shape(merged),
        "mail_configured": mail_enabled(),
    }
