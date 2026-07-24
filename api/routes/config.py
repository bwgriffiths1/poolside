"""Config routes — the ISO-NE bits of the runtime config.

Reads return the merged view (repo config.yaml defaults + app_config DB
overrides, see pipeline/appconfig.py). Writes touch ONLY the two managed
keys in the DB, so redeploys can't clobber edits and unmanaged keys are
untouched by construction.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import pipeline.db as db
from pipeline import appconfig
from ..auth import current_user, require_admin

router = APIRouter(prefix="/api/admin", tags=["config"])


class Committee(BaseModel):
    name: str
    short: str
    url: str
    active: bool = True


class ConfigPayload(BaseModel):
    lookahead_days: int = Field(ge=7, le=365)
    committees: list[Committee]


@router.get("/config", response_model=ConfigPayload)
def get_config(_: dict = Depends(current_user)) -> ConfigPayload:
    cfg = appconfig.get_config()
    committees = [
        Committee(
            name=c.get("name", ""),
            short=c.get("short", ""),
            url=c.get("url", ""),
            active=bool(c.get("active", True)),
        )
        for c in (cfg.get("committees") or [])
    ]
    return ConfigPayload(
        lookahead_days=int(cfg.get("lookahead_days", 60)),
        committees=committees,
    )


@router.put("/config", response_model=ConfigPayload)
def put_config(
    body: ConfigPayload,
    user: dict = Depends(require_admin),
) -> ConfigPayload:
    clean = [
        {"name": c.name, "short": c.short, "url": c.url, "active": c.active}
        for c in body.committees
        if c.name.strip() or c.url.strip()
    ]
    updated_by = (user.get("email") if isinstance(user, dict) else None) or "ui"
    appconfig.set_config_key("lookahead_days", int(body.lookahead_days),
                             updated_by=updated_by)
    appconfig.set_config_key("committees", clean, updated_by=updated_by)

    # Ensure each committee has a matching meeting_type row in the DB.
    venue = db.get_venue("ISO-NE")
    if venue:
        for row in clean:
            if row["short"] and row["name"]:
                try:
                    db.create_meeting_type(venue["id"], row["name"], row["short"])
                except Exception:
                    # Don't fail the whole save over a duplicate row, etc.
                    pass

    return get_config(user)
