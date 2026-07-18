"""
appconfig.py — runtime-editable configuration: DB overrides over repo files.

The UI edits prompts, the model config, and parts of config.yaml. Those
edits used to be written back to the container filesystem, which Railway
discards on every deploy — prod silently reverted to the repo copy, and the
repo never learned what prod was actually running.

Model: the repo files stay the DEFAULTS; the database holds OVERRIDES.
    app_config(key, value JSONB)      config.yaml top-level keys + 'model_config'
    prompt_overrides(slug, content)   prompts/<slug>.md bodies

Readers merge DB over file (per top-level key / per slug). Writers touch
only the DB — deploys can't clobber edits, and the nightly pg_dump covers
them. Every reader degrades to file-only when the DB is unreachable
(CI, offline tooling), so nothing here adds a hard DB dependency.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"
_PROMPTS_DIR = _REPO_ROOT / "prompts"

MODEL_CONFIG_KEY = "model_config"


# ── app_config (config.yaml keys) ────────────────────────────────────────


def _db_get(key: str) -> Any | None:
    try:
        import pipeline.db_new as db
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
                row = cur.fetchone()
                return row["value"] if row else None
    except Exception:
        logger.debug("app_config read failed for %r — file value wins", key,
                     exc_info=True)
        return None


def _db_get_all() -> dict[str, Any] | None:
    try:
        import pipeline.db_new as db
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute("SELECT key, value FROM app_config")
                return {r["key"]: r["value"] for r in cur.fetchall()}
    except Exception:
        logger.debug("app_config bulk read failed — file config wins",
                     exc_info=True)
        return None


def set_config_key(key: str, value: Any, updated_by: str = "system") -> None:
    import pipeline.db_new as db
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO app_config (key, value, updated_by)
                   VALUES (%s, %s::jsonb, %s)
                   ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value,
                           updated_by = EXCLUDED.updated_by,
                           updated_at = NOW()""",
                (key, json.dumps(value), updated_by),
            )


def _file_config() -> dict:
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def get_config() -> dict:
    """config.yaml with DB overrides applied per top-level key."""
    cfg = _file_config()
    overrides = _db_get_all()
    if overrides:
        overrides.pop(MODEL_CONFIG_KEY, None)  # model config has its own reader
        cfg.update(overrides)
    return cfg


# ── prompt_overrides (prompts/<slug>.md) ─────────────────────────────────


def _db_get_prompt(slug: str) -> str | None:
    try:
        import pipeline.db_new as db
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute("SELECT content FROM prompt_overrides WHERE slug = %s",
                            (slug,))
                row = cur.fetchone()
                return row["content"] if row is not None else None
    except Exception:
        logger.debug("prompt_overrides read failed for %r — file wins", slug,
                     exc_info=True)
        return None


def get_prompt(slug: str) -> str:
    """Prompt body: DB override first, else prompts/<slug>.md, else ''."""
    override = _db_get_prompt(slug)
    if override is not None:
        return override
    path = _PROMPTS_DIR / f"{slug}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def get_prompt_overrides() -> dict[str, dict[str, Any]]:
    """{slug: {content, updated_at}} for every override (empty when DB down)."""
    try:
        import pipeline.db_new as db
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute("SELECT slug, content, updated_at FROM prompt_overrides")
                return {r["slug"]: {"content": r["content"],
                                    "updated_at": r["updated_at"]}
                        for r in cur.fetchall()}
    except Exception:
        return {}


def set_prompt(slug: str, content: str, updated_by: str = "system") -> None:
    import pipeline.db_new as db
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """INSERT INTO prompt_overrides (slug, content, updated_by)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (slug) DO UPDATE
                       SET content = EXCLUDED.content,
                           updated_by = EXCLUDED.updated_by,
                           updated_at = NOW()""",
                (slug, content, updated_by),
            )


def delete_prompt_override(slug: str) -> bool:
    """Drop the override so the repo default shows through. True if one existed."""
    import pipeline.db_new as db
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("DELETE FROM prompt_overrides WHERE slug = %s", (slug,))
            return cur.rowcount > 0


# ── model config (prompts/model_config.json) ─────────────────────────────


def _file_model_config() -> dict:
    path = _PROMPTS_DIR / "model_config.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    return {}


def get_model_config() -> dict:
    """model_config.json with the DB override merged over it, key-level."""
    cfg = _file_model_config()
    db_val = _db_get(MODEL_CONFIG_KEY)
    if isinstance(db_val, dict):
        cfg.update(db_val)
    return cfg


def set_model_config(cfg: dict, updated_by: str = "system") -> None:
    set_config_key(MODEL_CONFIG_KEY, cfg, updated_by=updated_by)
