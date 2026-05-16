"""Prompt library — read/write the markdown prompts under prompts/.

Mirrors the Streamlit page at v2_pages/prompt_library.py. All prompts are
flat files; no DB rows. Slug pattern:
  general_context_prompt           shared context
  doc_summary_prompt               shared document summariser
  agenda_item_prompt               default per-item prompt
  agenda_parse_prompt              pipeline: agenda parser
  doc_match_prompt                 pipeline: doc → item matcher
  deep_dive_prompt                 feature: deep dive reports
  keyword_extraction_prompt        feature: keyword extraction
  {venue}_{committee}_briefing_prompt   per venue + committee
  {venue}_{committee}_agenda_item_prompt
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import db_new as db

router = APIRouter(prefix="/api/prompts", tags=["prompts"])
config_router = APIRouter(prefix="/api/model-config", tags=["prompts"])

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
_CONFIG_PATH = _PROMPTS_DIR / "model_config.json"

_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_VENUE_SLUG_MAP = {"ISO-NE": "isone", "NYISO": "nyiso"}


def _venue_to_slug(short_name: str) -> str:
    return _VENUE_SLUG_MAP.get(short_name, short_name.lower().replace("-", "").replace(" ", ""))


def _safe_slug(slug: str) -> str:
    """Reject anything that isn't a plain `[a-z0-9_]` slug to keep us inside
    the prompts/ directory."""
    if not slug or not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail=f"Invalid slug: {slug!r}")
    return slug


def _prompt_path(slug: str) -> Path:
    return _PROMPTS_DIR / f"{_safe_slug(slug)}.md"


# ── Index ───────────────────────────────────────────────────────────────────


@router.get("")
def list_prompts() -> dict[str, Any]:
    """Return every prompt grouped by category, with venue/committee context
    drawn from the DB so the frontend can render the right tab hierarchy.
    """
    if not _PROMPTS_DIR.exists():
        return {"shared": [], "pipeline": [], "venues": [], "extras": []}

    files = {p.stem: p for p in _PROMPTS_DIR.glob("*.md")}

    def meta(slug: str) -> dict[str, Any] | None:
        p = files.get(slug)
        if p is None:
            return {"slug": slug, "exists": False, "size": 0, "modified": None}
        stat = p.stat()
        return {
            "slug": slug,
            "exists": True,
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

    # Anything in prompts/ we haven't surfaced yet
    known_slugs = {
        "general_context_prompt", "doc_summary_prompt", "agenda_item_prompt",
        "agenda_parse_prompt", "doc_match_prompt", "deep_dive_prompt",
        "keyword_extraction_prompt",
    }
    for v in venues_out:
        for c in v["committees"]:
            for k in ("briefing", "agenda_item"):
                if c[k] and c[k].get("exists"):
                    known_slugs.add(c[k]["slug"])
    extras = []
    for slug in sorted(files.keys()):
        if slug in known_slugs:
            continue
        extras.append({"slug": slug, **(meta(slug) or {})})

    return {
        "shared": shared,
        "pipeline": pipeline,
        "venues": venues_out,
        "extras": extras,
    }


# ── Read / write a single prompt ────────────────────────────────────────────


@router.get("/{slug}")
def get_prompt(slug: str) -> dict[str, Any]:
    path = _prompt_path(slug)
    if not path.exists():
        return {"slug": slug, "exists": False, "content": ""}
    return {
        "slug": slug,
        "exists": True,
        "content": path.read_text(encoding="utf-8"),
        "size": path.stat().st_size,
        "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
    }


@router.put("/{slug}")
def save_prompt(slug: str, body: dict[str, str] = Body(...)) -> dict[str, Any]:
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="`content` is required")
    path = _prompt_path(slug)
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    stat = path.stat()
    return {
        "slug": slug,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


@router.delete("/{slug}")
def delete_prompt(slug: str) -> dict[str, str]:
    path = _prompt_path(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Prompt not found")
    path.unlink()
    return {"status": "ok"}


# ── Model config ────────────────────────────────────────────────────────────

_DEFAULT_MODELS = {
    "document_model": "claude-haiku-4-5-20251001",
    "item_model": "claude-haiku-4-5-20251001",
    "meeting_model": "claude-haiku-4-5-20251001",
    "document_max_tokens": 32768,
    "item_max_tokens": 32768,
    "meeting_max_tokens": 32768,
}


@config_router.get("")
def get_model_config() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        try:
            return {**_DEFAULT_MODELS, **json.loads(_CONFIG_PATH.read_text())}
        except Exception:
            pass
    return _DEFAULT_MODELS.copy()


@config_router.put("")
def save_model_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    # Whitelist keys to avoid writing arbitrary fields.
    allowed = set(_DEFAULT_MODELS.keys())
    cfg = {k: v for k, v in body.items() if k in allowed}
    if not cfg:
        raise HTTPException(status_code=400, detail="No recognised fields in body")
    # Merge with whatever's already on disk
    existing: dict[str, Any] = {}
    if _CONFIG_PATH.exists():
        try:
            existing = json.loads(_CONFIG_PATH.read_text())
        except Exception:
            pass
    merged = {**existing, **cfg}
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged
