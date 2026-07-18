"""Prompt library — read/write prompts as DB overrides over the repo files.

The repo's prompts/*.md are the defaults; edits land in the prompt_overrides
table (see pipeline/appconfig.py) so they survive redeploys and ride the
nightly backup. DELETE reverts a slug to its repo default. Slug pattern:
  general_context_prompt           shared context
  doc_summary_prompt               shared document summariser
  agenda_item_prompt               default per-item prompt
  agenda_parse_prompt              pipeline: agenda parser
  doc_match_prompt                 pipeline: doc → item matcher
  deep_dive_prompt                 feature: deep dive reports
  monthly_roundup_prompt           feature: monthly roundups
  keyword_extraction_prompt        feature: keyword extraction
  {venue}_{committee}_briefing_prompt   per venue + committee
  {venue}_{committee}_agenda_item_prompt
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import appconfig
from pipeline import db_new as db

router = APIRouter(prefix="/api/prompts", tags=["prompts"])
config_router = APIRouter(prefix="/api/model-config", tags=["prompts"])

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_VENUE_SLUG_MAP = {"ISO-NE": "isone"}


def _venue_to_slug(short_name: str) -> str:
    return _VENUE_SLUG_MAP.get(short_name, short_name.lower().replace("-", "").replace(" ", ""))


def _safe_slug(slug: str) -> str:
    """Reject anything that isn't a plain `[a-z0-9_]` slug."""
    if not slug or not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail=f"Invalid slug: {slug!r}")
    return slug


# ── Index ───────────────────────────────────────────────────────────────────


@router.get("")
def list_prompts() -> dict[str, Any]:
    """Every prompt grouped by category, with venue/committee context from
    the DB. A prompt "exists" when it has a repo file OR a DB override;
    `overridden` tells the UI which ones have live edits."""
    files = {p.stem: p for p in _PROMPTS_DIR.glob("*.md")} if _PROMPTS_DIR.exists() else {}
    overrides = appconfig.get_prompt_overrides()

    def meta(slug: str) -> dict[str, Any]:
        ov = overrides.get(slug)
        if ov is not None:
            modified = ov["updated_at"]
            return {
                "slug": slug,
                "exists": True,
                "overridden": True,
                "size": len(ov["content"].encode("utf-8")),
                "modified": modified.isoformat() if hasattr(modified, "isoformat") else str(modified),
            }
        p = files.get(slug)
        if p is None:
            return {"slug": slug, "exists": False, "overridden": False,
                    "size": 0, "modified": None}
        stat = p.stat()
        return {
            "slug": slug,
            "exists": True,
            "overridden": False,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }

    shared = [
        {"label": "General context", **meta("general_context_prompt"),
         "hint": "Prepended to every briefing + agenda-item prompt"},
        {"label": "Document summary", **meta("doc_summary_prompt"),
         "hint": "Applied to every downloaded document"},
        {"label": "Default agenda item", **meta("agenda_item_prompt"),
         "hint": "Fallback when no per-committee item prompt exists"},
    ]

    pipeline = [
        {"label": "Agenda parser", **meta("agenda_parse_prompt"),
         "hint": "LLM-assisted parse of the agenda doc into items"},
        {"label": "Document matcher", **meta("doc_match_prompt"),
         "hint": "LLM fallback that assigns docs to agenda items"},
        {"label": "Deep dive", **meta("deep_dive_prompt"),
         "hint": "Cross-meeting analysis reports"},
        {"label": "Monthly roundup", **meta("monthly_roundup_prompt"),
         "hint": "Cross-committee monthly state of play"},
        {"label": "Keyword extraction", **meta("keyword_extraction_prompt"),
         "hint": "Tag generation"},
    ]

    venues_out: list[dict[str, Any]] = []
    for v in db.get_venues():
        venue_slug = _venue_to_slug(v["short_name"])
        committees: list[dict[str, Any]] = []
        for c in db.get_meeting_types(v["short_name"]):
            comm_slug = c["short_name"].lower()
            committees.append({
                "short_name": c["short_name"],
                "name": c.get("name") or c["short_name"],
                "briefing": meta(f"{venue_slug}_{comm_slug}_briefing_prompt"),
                "agenda_item": meta(f"{venue_slug}_{comm_slug}_agenda_item_prompt"),
            })
        venues_out.append({
            "venue_short": v["short_name"],
            "venue_name": v.get("name") or v["short_name"],
            "venue_slug": venue_slug,
            "committees": committees,
        })

    # Anything on disk or overridden that we haven't surfaced yet
    known_slugs = {
        "general_context_prompt", "doc_summary_prompt", "agenda_item_prompt",
        "agenda_parse_prompt", "doc_match_prompt", "deep_dive_prompt",
        "keyword_extraction_prompt", "monthly_roundup_prompt",
    }
    for v in venues_out:
        for c in v["committees"]:
            for k in ("briefing", "agenda_item"):
                if c[k] and c[k].get("exists"):
                    known_slugs.add(c[k]["slug"])
    extras = []
    for slug in sorted(set(files.keys()) | set(overrides.keys())):
        if slug in known_slugs:
            continue
        extras.append({"slug": slug, **meta(slug)})

    return {
        "shared": shared,
        "pipeline": pipeline,
        "venues": venues_out,
        "extras": extras,
    }


# ── Read / write a single prompt ────────────────────────────────────────────


@router.get("/{slug}")
def get_prompt(slug: str) -> dict[str, Any]:
    slug = _safe_slug(slug)
    overrides = appconfig.get_prompt_overrides()
    ov = overrides.get(slug)
    path = _PROMPTS_DIR / f"{slug}.md"
    if ov is None and not path.exists():
        return {"slug": slug, "exists": False, "overridden": False, "content": ""}
    if ov is not None:
        content = ov["content"]
        ts = ov["updated_at"]
        modified = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    else:
        content = path.read_text(encoding="utf-8")
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return {
        "slug": slug,
        "exists": True,
        "overridden": ov is not None,
        "content": content,
        "size": len(content.encode("utf-8")),
        "modified": modified,
    }


@router.put("/{slug}")
def save_prompt(slug: str, body: dict[str, str] = Body(...)) -> dict[str, Any]:
    slug = _safe_slug(slug)
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="`content` is required")
    appconfig.set_prompt(slug, content, updated_by="ui")
    return {
        "slug": slug,
        "overridden": True,
        "size": len(content.encode("utf-8")),
        "modified": datetime.now(timezone.utc).isoformat(),
    }


@router.delete("/{slug}")
def delete_prompt(slug: str) -> dict[str, Any]:
    """Remove the DB override so the repo default shows through again.
    Repo defaults themselves can't be deleted from the UI (the container
    filesystem is ephemeral — deleting there never stuck anyway)."""
    slug = _safe_slug(slug)
    if appconfig.delete_prompt_override(slug):
        return {"status": "reverted",
                "exists": (_PROMPTS_DIR / f"{slug}.md").exists()}
    if (_PROMPTS_DIR / f"{slug}.md").exists():
        raise HTTPException(
            status_code=400,
            detail="This prompt has no override to remove — it is the repo "
                   "default. Edit it instead, or change the file in git.",
        )
    raise HTTPException(status_code=404, detail="Prompt not found")


# ── Model config ────────────────────────────────────────────────────────────

_DEFAULT_MODELS = {
    "document_model": "claude-haiku-4-5-20251001",
    "item_model": "claude-haiku-4-5-20251001",
    "meeting_model": "claude-haiku-4-5-20251001",
    "document_max_tokens": 32768,
    "item_max_tokens": 32768,
    "meeting_max_tokens": 32768,
}

# Optional keys the roundup feature reads (falls back to meeting_* when unset).
_OPTIONAL_MODEL_KEYS = {"roundup_model", "roundup_max_tokens"}


@config_router.get("")
def get_model_config() -> dict[str, Any]:
    return {**_DEFAULT_MODELS, **appconfig.get_model_config()}


@config_router.put("")
def save_model_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    # Whitelist keys to avoid writing arbitrary fields.
    allowed = set(_DEFAULT_MODELS.keys()) | _OPTIONAL_MODEL_KEYS
    cfg = {k: v for k, v in body.items() if k in allowed}
    if not cfg:
        raise HTTPException(status_code=400, detail="No recognised fields in body")
    # Merge over what's already effective (file defaults + prior override).
    merged = {**appconfig.get_model_config(), **cfg}
    appconfig.set_model_config(merged, updated_by="ui")
    return {**_DEFAULT_MODELS, **merged}
