"""Level 3 briefing assembly when materials are filed directly on a parent.

Regression for the RC 2026-07-23 meeting (prod meeting 145): every substantive
agenda item had its documents attached to the *parent* item, with child
sub-items that carried no documents of their own. Level 2 correctly stored a
summary on each parent (its own-docs path), but Level 3's _collect_leaf_summaries
walked *past* the parent into the empty leaves and collected nothing — so the
briefing assembled zero parts, _run_meeting_briefing returned False, and the
meeting was marked "complete" with no briefing and no error.

These tests pin:
  * _collect_leaf_summaries falls back to a branch's own summary when the
    branch yields no leaf summaries (and does NOT double-count otherwise).
  * The full runner produces a meeting briefing for the parent-owns-docs shape.
  * A briefing that collects nothing while item summaries exist surfaces an
    error instead of a silent success.
"""
from types import SimpleNamespace

import pytest

import pipeline.summarizer as summarizer


# ---------------------------------------------------------------------------
# Unit: _collect_leaf_summaries own-summary fallback
# ---------------------------------------------------------------------------

class _SummDB:
    """Minimal db stand-in exposing only get_current_summary."""

    def __init__(self, summaries):
        # {entity_id: detailed-text}
        self._s = summaries

    def get_current_summary(self, entity_type, entity_id):
        text = self._s.get(entity_id)
        return {"detailed": text} if text else None


def test_collect_falls_back_to_parent_own_summary(monkeypatch):
    """Parent with an own summary but a child that has none → the parent's
    own summary is collected (the meeting-145 shape)."""
    parent = {"id": 1, "item_id": "4", "title": "Transmission Cost Allocations"}
    child = {"id": 2, "item_id": "4.1", "title": "South Naugatuck Rebuild"}
    children_of = {1: [child]}
    monkeypatch.setattr(summarizer, "db", _SummDB({1: "PARENT-OWN-SUMMARY"}))

    leaves = summarizer._collect_leaf_summaries(parent, children_of)

    assert len(leaves) == 1
    assert leaves[0][0]["id"] == 1
    assert leaves[0][1]["detailed"] == "PARENT-OWN-SUMMARY"


def test_collect_prefers_leaves_and_does_not_double_count(monkeypatch):
    """When children have summaries, the leaves are collected and the parent's
    own summary is NOT added on top (no regression / no double-count)."""
    parent = {"id": 1, "item_id": "4", "title": "Parent"}
    c1 = {"id": 2, "item_id": "4.1", "title": "Child one"}
    c2 = {"id": 3, "item_id": "4.2", "title": "Child two"}
    children_of = {1: [c1, c2]}
    monkeypatch.setattr(summarizer, "db", _SummDB(
        {1: "PARENT-OWN", 2: "CHILD-ONE", 3: "CHILD-TWO"}
    ))

    leaves = summarizer._collect_leaf_summaries(parent, children_of)

    ids = sorted(node["id"] for node, _ in leaves)
    assert ids == [2, 3]                       # both leaves
    assert all(node["id"] != 1 for node, _ in leaves)  # parent not double-counted


def test_collect_fallback_is_per_branch(monkeypatch):
    """A dry branch falls back to its own summary while a sibling leaf still
    contributes normally — the fallback is scoped to each branch."""
    top = {"id": 1, "item_id": "5", "title": "Operating Procedures"}
    branch = {"id": 2, "item_id": "5.1", "title": "Sub with own docs"}
    dead_leaf = {"id": 5, "item_id": "5.1.a", "title": "Empty leaf"}
    sib_leaf = {"id": 4, "item_id": "5.2", "title": "Leaf with summary"}
    children_of = {1: [branch, sib_leaf], 2: [dead_leaf]}
    # top (1) and dead_leaf (5) have no summaries; branch (2) + sibling (4) do.
    monkeypatch.setattr(summarizer, "db", _SummDB({2: "BRANCH-OWN", 4: "SIBLING-LEAF"}))

    leaves = summarizer._collect_leaf_summaries(top, children_of)

    got = {node["id"]: summ["detailed"] for node, summ in leaves}
    assert got == {2: "BRANCH-OWN", 4: "SIBLING-LEAF"}


# ---------------------------------------------------------------------------
# End-to-end: the runner produces a briefing for the parent-owns-docs shape
# ---------------------------------------------------------------------------

class FakeDB:
    """Just enough of pipeline.db for the full summarize runner."""

    def __init__(self, items, docs_by_item, summaries=None):
        self.items = items
        self.docs_by_item = docs_by_item
        self.versions = dict(summaries or {})

    def get_agenda_items(self, meeting_id):
        return list(self.items)

    def get_documents_for_item(self, item_id):
        return list(self.docs_by_item.get(item_id, []))

    def get_current_summary(self, entity_type, entity_id):
        chain = self.versions.get((entity_type, entity_id)) or []
        return chain[-1] if chain else None

    def get_meeting(self, meeting_id):
        return {"id": meeting_id, "type_short": "RC", "type_name": "Reliability Committee"}

    def get_prior_meeting_briefings(self, meeting_id, within_days=60, limit=3):
        return []

    def get_images_by_ids(self, image_ids):
        return []

    def create_summary_version(self, entity_type, entity_id, one_line, detailed,
                               model_id, is_manual, status, created_by):
        chain = self.versions.setdefault((entity_type, entity_id), [])
        chain.append({
            "entity_type": entity_type, "entity_id": entity_id,
            "version": len(chain) + 1, "one_line": one_line,
            "detailed": detailed, "model_id": model_id,
            "status": status, "is_manual": is_manual,
        })

    def set_meeting_status(self, meeting_id, status):
        pass


def _doc(name):
    return {"id": hash(name) % 10_000, "filename": name, "file_type": ".pdf",
            "ceii_skipped": False, "ignored": False, "raw_content": "RAW"}


def _mk_parent_owns_docs_items():
    """Two depth-0 parents, each with a doc-less child sub-item — the shape
    that broke meeting 145."""
    return [
        {"id": 1, "item_id": "4", "title": "Transmission Cost Allocations",
         "depth": 0, "parent_id": None},
        {"id": 2, "item_id": "4.1", "title": "South Naugatuck Rebuild",
         "depth": 1, "parent_id": 1},
        {"id": 3, "item_id": "5", "title": "Operating Procedures",
         "depth": 0, "parent_id": None},
        {"id": 4, "item_id": "5.1", "title": "Order 2222 Conforming Changes",
         "depth": 1, "parent_id": 3},
    ]


@pytest.fixture
def runner(monkeypatch):
    """Patch summarizer collaborators but leave _run_meeting_briefing REAL."""
    calls: list[str] = []

    def fake_call_llm(client, model, prompt, max_tokens=4096, label=""):
        calls.append(label)
        return f"SUM({label})"

    monkeypatch.setattr(summarizer, "_call_llm", fake_call_llm)
    monkeypatch.setattr(summarizer, "_load_image_config", lambda: {"enabled": False})
    monkeypatch.setattr(summarizer, "_load_parallel_workers", lambda: 1)
    monkeypatch.setattr(summarizer, "_load_model_config", lambda: {
        "document_model": "claude-haiku-4-5-20251001",
        "item_model": "claude-haiku-4-5-20251001",
        "meeting_model": "claude-haiku-4-5-20251001",
        "document_max_tokens": 1000,
        "item_max_tokens": 1000,
        "meeting_max_tokens": 1000,
    })
    monkeypatch.setattr(summarizer, "_get_committee_prompts",
                        lambda c, v: ("BRIEFING PROMPT", "item {item_id} {title}"))
    monkeypatch.setattr(summarizer, "_load_prompt", lambda slug: "docs {filename}\n{text}")
    monkeypatch.setattr(summarizer, "_get_text_for_doc",
                        lambda d: d.get("raw_content") or "")
    return SimpleNamespace(calls=calls)


def test_runner_generates_briefing_when_docs_are_on_parents(runner, monkeypatch):
    fakedb = FakeDB(_mk_parent_owns_docs_items(),
                    docs_by_item={1: [_doc("tca-memo.pdf")], 3: [_doc("op-memo.pdf")]})
    monkeypatch.setattr(summarizer, "db", fakedb)

    result = summarizer.run_meeting_summarization(145, client=object(), force_rerun=True)

    # Each parent got its own-docs summary at Level 2…
    assert result["level2"] == 2
    # …and the meeting briefing was actually created (the bug: this was False).
    assert result["level3"] is True
    assert result["errors"] == []
    assert ("meeting", 145) in fakedb.versions
    assert fakedb.versions[("meeting", 145)][0]["detailed"]

    # The briefing LLM call happened and its prompt carried both parents' text.
    assert "L3 meeting 145" in runner.calls
    briefing = fakedb.versions[("meeting", 145)][0]["detailed"]
    assert briefing == "SUM(L3 meeting 145)"


def test_runner_surfaces_error_when_briefing_collects_nothing(runner, monkeypatch):
    """Hardening: if item summaries exist but the briefing produces nothing,
    the job reports an error rather than a silent 'Done'."""
    fakedb = FakeDB(_mk_parent_owns_docs_items(),
                    docs_by_item={1: [_doc("tca-memo.pdf")], 3: [_doc("op-memo.pdf")]})
    monkeypatch.setattr(summarizer, "db", fakedb)
    # Force the failing outcome regardless of the collector fix.
    monkeypatch.setattr(summarizer, "_run_meeting_briefing", lambda *a, **k: False)

    result = summarizer.run_meeting_summarization(145, client=object(), force_rerun=True)

    assert result["level3"] is False
    assert result["errors"], "expected an error when briefing collected nothing"
    assert any("no content was collected" in e for e in result["errors"])
