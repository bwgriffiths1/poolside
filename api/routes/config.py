"""Config routes — read/write the ISO-NE bits of config.yaml.

Keys outside the managed set (summarization etc.) are preserved on PUT.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import pipeline.db_new as db
from ..auth import current_user

router = APIRouter(prefix="/api/admin", tags=["config"])

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


class Committee(BaseModel):
    name: str
    short: str
    url: str
    active: bool = True


class ConfigPayload(BaseModel):
    lookahead_days: int = Field(ge=7, le=365)
    committees: list[Committee]


def _load() -> dict[str, Any]:
    with CONFIG_PATH.open() as fh:
        return yaml.safe_load(fh) or {}


def _save(data: dict[str, Any]) -> None:
    with CONFIG_PATH.open("w") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


@router.get("/config", response_model=ConfigPayload)
def get_config(_: dict = Depends(current_user)) -> ConfigPayload:
    cfg = _load()
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
    _: dict = Depends(current_user),
) -> ConfigPayload:
    # Preserve any keys we don't manage (summarization etc.).
    on_disk = _load()
    new_cfg = copy.deepcopy(on_disk)
    new_cfg["lookahead_days"] = int(body.lookahead_days)

    clean = [
        {"name": c.name, "short": c.short, "url": c.url, "active": c.active}
        for c in body.committees
        if c.name.strip() or c.url.strip()
    ]
    new_cfg["committees"] = clean

    try:
        _save(new_cfg)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write config.yaml: {e}")

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

    return get_config(_)
