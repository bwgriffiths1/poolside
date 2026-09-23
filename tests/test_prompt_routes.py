"""Prompt-route slug handling — hyphenated committee slugs must be reachable.

PJM's CIFP-RBP committee yields prompt slugs containing hyphens
(pjm_cifp-rbp_briefing_prompt). The validation regex once admitted only
[a-z0-9_], so every GET/PUT/DELETE on those slugs 400'd — the repo files
were stuck read-only in the Admin UI and could never grow DB overrides.
Pins:

  * _safe_slug admits hyphens but still rejects every path-traversal
    shape (dots, slashes, backslashes, uppercase, empty);
  * GET serves the hyphenated repo file, PUT stores an override,
    DELETE reverts to it;
  * the index groups the CIFP-RBP prompts under the PJM venue entry,
    not the dead-end extras bucket.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

import api.routes.prompts as pr

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
BRIEFING = "pjm_cifp-rbp_briefing_prompt"
ITEM = "pjm_cifp-rbp_agenda_item_prompt"


def _no_overrides(monkeypatch):
    monkeypatch.setattr(pr.appconfig, "get_prompt_overrides", lambda: {})


# ── Slug validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", [BRIEFING, ITEM, "doc_summary_prompt"])
def test_safe_slug_admits_hyphens_and_plain(slug):
    assert pr._safe_slug(slug) == slug


@pytest.mark.parametrize("slug", [
    "", "..", "../etc/passwd", "a/b", "a\\b", "a.b",
    "PJM_CIFP", "pjm cifp", "pjm_cifp-rbp_briefing_prompt.md",
])
def test_safe_slug_rejects_traversal_shapes(slug):
    with pytest.raises(HTTPException) as exc:
        pr._safe_slug(slug)
    assert exc.value.status_code == 400


# ── Read / write / revert on the hyphenated slugs ───────────────────────────

def test_get_serves_hyphenated_repo_file(monkeypatch):
    _no_overrides(monkeypatch)
    out = pr.get_prompt(BRIEFING)
    assert out["exists"] and not out["overridden"]
    assert out["content"] == (PROMPTS / f"{BRIEFING}.md").read_text(encoding="utf-8")


def test_put_stores_override_for_hyphenated_slug(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        pr.appconfig, "set_prompt",
        lambda slug, content, updated_by: saved.update(slug=slug, content=content))
    out = pr.save_prompt(ITEM, {"content": "override body"})
    assert saved == {"slug": ITEM, "content": "override body"}
    assert out["overridden"]


def test_delete_reverts_hyphenated_slug_to_repo_file(monkeypatch):
    monkeypatch.setattr(pr.appconfig, "delete_prompt_override", lambda slug: True)
    out = pr.delete_prompt(BRIEFING)
    assert out == {"status": "reverted", "exists": True}


# ── Index grouping ──────────────────────────────────────────────────────────

def test_index_groups_cifp_rbp_under_pjm_not_extras(monkeypatch):
    _no_overrides(monkeypatch)
    monkeypatch.setattr(pr.db, "get_venues", lambda: [
        {"short_name": "PJM", "name": "PJM Interconnection"}])
    monkeypatch.setattr(pr.db, "get_meeting_types", lambda venue: [
        {"short_name": "CIFP-RBP",
         "name": "Critical Issue Fast Path - Reliability Backstop Procurement"}])
    index = pr.list_prompts()

    (venue,) = index["venues"]
    assert venue["venue_slug"] == "pjm"
    (comm,) = venue["committees"]
    assert comm["briefing"]["slug"] == BRIEFING and comm["briefing"]["exists"]
    assert comm["agenda_item"]["slug"] == ITEM and comm["agenda_item"]["exists"]
    extras = {e["slug"] for e in index["extras"]}
    assert BRIEFING not in extras and ITEM not in extras
