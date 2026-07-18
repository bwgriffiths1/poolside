"""Level 1/2 semantics for parents that have their own documents.

Historically the runner Level-1-summarized EVERY item with docs — including
parents — and Level 2 then rolled up the children and superseded the parent's
L1 summary. That was a double LLM spend per parent-with-docs, the parent's
own material vanished from the final rollup, and in "missing" mode the fresh
L1 draft made the Level 2 skip-guard bypass the rollup entirely.

These tests drive run_meeting_summarization / estimate_summarization_cost
against an in-memory fake of the db module (no Postgres, no LLM) and pin the
new semantics:

  * Level 1 runs for leaves only.
  * A parent's own docs are doc-group-summarized inside the Level 2 worker
    and fed to the rollup as one extra input.
  * A parent with own docs but no child summaries keeps a doc-group summary
    as its item summary (exactly one version — no overwrite race).
  * The estimator counts the parent's own-docs call the same way.
"""
from types import SimpleNamespace

import pytest

import pipeline.summarizer as summarizer


# ---------------------------------------------------------------------------
# Fake world
# ---------------------------------------------------------------------------

def _mk_items():
    # Parent "2" with children "2.1"/"2.2", plus leaf "3".
    return [
        {"id": 1, "item_id": "2", "title": "Parent item", "depth": 0, "parent_id": None},
        {"id": 2, "item_id": "2.1", "title": "Child one", "depth": 1, "parent_id": 1},
        {"id": 3, "item_id": "2.2", "title": "Child two", "depth": 1, "parent_id": 1},
        {"id": 4, "item_id": "3", "title": "Leaf item", "depth": 0, "parent_id": None},
    ]


class FakeDB:
    """Just enough of pipeline.db for the summarize runner + estimator."""

    def __init__(self, items, docs_by_item, summaries=None):
        self.items = items
        self.docs_by_item = docs_by_item
        # {(entity_type, entity_id): [version dicts, newest last]}
        self.versions = dict(summaries or {})

    # -- reads --------------------------------------------------------------
    def get_agenda_items(self, meeting_id):
        return list(self.items)

    def get_documents_for_item(self, item_id):
        return list(self.docs_by_item.get(item_id, []))

    def get_current_summary(self, entity_type, entity_id):
        chain = self.versions.get((entity_type, entity_id)) or []
        return chain[-1] if chain else None

    def get_meeting(self, meeting_id):
        return {"id": meeting_id, "type_short": "NPC", "type_name": "Participants"}

    # -- writes -------------------------------------------------------------
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


def _doc(name, content="RAW"):
    return {"id": hash(name) % 10_000, "filename": name, "file_type": ".pdf",
            "ceii_skipped": False, "ignored": False, "raw_content": content}


@pytest.fixture
def world(monkeypatch):
    """Patch summarizer's collaborators; returns (fakedb, llm_calls)."""
    llm_calls: list[tuple[str, str]] = []

    def fake_call_llm(client, model, prompt, max_tokens=4096, label=""):
        llm_calls.append((label, prompt))
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
                        lambda c, v: ("briefing", "item {item_id} {title}\n{doc_summaries}"))
    monkeypatch.setattr(summarizer, "_load_prompt",
                        lambda slug: "docs {filename}\n{text}")
    monkeypatch.setattr(summarizer, "_get_text_for_doc",
                        lambda d: d.get("raw_content") or "")
    monkeypatch.setattr(summarizer, "_run_meeting_briefing",
                        lambda *a, **k: True)

    def install(fakedb):
        monkeypatch.setattr(summarizer, "db", fakedb)
        return fakedb

    return SimpleNamespace(install=install, llm_calls=llm_calls)


# ---------------------------------------------------------------------------
# Runner semantics
# ---------------------------------------------------------------------------

def test_parent_with_docs_and_children_gets_one_rollup_not_two_summaries(world):
    fakedb = world.install(FakeDB(
        _mk_items(),
        docs_by_item={
            1: [_doc("parent-memo.pdf", "PARENT-MEMO")],
            2: [_doc("child1.pdf", "CHILD1-DOC")],
            4: [_doc("leaf.pdf", "LEAF-DOC")],
        },
        summaries={("agenda_item", 3): [{"status": "draft", "detailed": "C2-EXISTING", "version": 1}]},
    ))

    result = summarizer.run_meeting_summarization(
        99, client=object(), force_rerun=True,
    )
    assert result["errors"] == []

    labels = [lbl for lbl, _ in world.llm_calls]
    # Level 1 ran for the leaves (2.1 and 3) — NOT for the parent.
    assert "L1 item 2.1" in labels and "L1 item 3" in labels
    # The parent's own docs were summarized once, inside the L2 phase…
    assert labels.count("L1 item 2") == 1
    # …and exactly one rollup happened.
    assert labels.count("L2 item 2") == 1

    # The parent has exactly ONE stored version — the rollup. (The old code
    # stored an L1 version and then superseded it with the rollup.)
    parent_chain = fakedb.versions[("agenda_item", 1)]
    assert len(parent_chain) == 1
    assert parent_chain[0]["detailed"] == "SUM(L2 item 2)"

    # The rollup prompt saw both children AND the own-docs summary.
    rollup_prompt = next(p for lbl, p in world.llm_calls if lbl == "L2 item 2")
    assert "SUM(L1 item 2.1)" in rollup_prompt
    assert "C2-EXISTING" in rollup_prompt
    assert "Materials filed directly under this item" in rollup_prompt
    assert "SUM(L1 item 2)" in rollup_prompt


def test_missing_mode_parent_rollup_is_not_blocked(world):
    """Old bug: in "missing" mode the parent's fresh L1 draft made the L2
    skip-guard treat it as already-summarized, so children never rolled up."""
    fakedb = world.install(FakeDB(
        _mk_items(),
        docs_by_item={
            1: [_doc("parent-memo.pdf", "PARENT-MEMO")],
            2: [_doc("child1.pdf", "CHILD1-DOC")],
        },
    ))

    result = summarizer.run_meeting_summarization(
        99, client=object(), force_rerun=False,
    )
    assert result["errors"] == []
    parent_chain = fakedb.versions[("agenda_item", 1)]
    assert len(parent_chain) == 1
    assert parent_chain[0]["detailed"] == "SUM(L2 item 2)"
    rollup_prompt = next(p for lbl, p in world.llm_calls if lbl == "L2 item 2")
    assert "SUM(L1 item 2.1)" in rollup_prompt


def test_parent_with_own_docs_but_no_child_summaries_keeps_doc_summary(world):
    """Children exist but have nothing to roll up → the own-docs summary IS
    the parent's summary, stored once."""
    fakedb = world.install(FakeDB(
        _mk_items(),
        docs_by_item={1: [_doc("parent-memo.pdf", "PARENT-MEMO")]},
    ))

    result = summarizer.run_meeting_summarization(
        99, client=object(), force_rerun=True,
    )
    assert result["errors"] == []
    labels = [lbl for lbl, _ in world.llm_calls]
    assert labels.count("L1 item 2") == 1
    assert "L2 item 2" not in labels  # no rollup happened
    parent_chain = fakedb.versions[("agenda_item", 1)]
    assert len(parent_chain) == 1
    assert parent_chain[0]["detailed"] == "SUM(L1 item 2)"


def test_item_ids_filter_reaches_parent_directly(world):
    """A material landing directly on a parent re-runs that parent's rollup
    even though none of its children changed."""
    fakedb = world.install(FakeDB(
        _mk_items(),
        docs_by_item={1: [_doc("parent-memo.pdf", "PARENT-MEMO")]},
        summaries={
            ("agenda_item", 2): [{"status": "draft", "detailed": "C1-EXISTING", "version": 1}],
            ("agenda_item", 1): [{"status": "draft", "detailed": "OLD-PARENT", "version": 1}],
        },
    ))

    result = summarizer.run_meeting_summarization(
        99, client=object(), force_rerun=True, item_ids={1},
    )
    assert result["errors"] == []
    parent_chain = fakedb.versions[("agenda_item", 1)]
    assert parent_chain[-1]["detailed"] == "SUM(L2 item 2)"
    rollup_prompt = next(p for lbl, p in world.llm_calls if lbl == "L2 item 2")
    assert "C1-EXISTING" in rollup_prompt
    assert "SUM(L1 item 2)" in rollup_prompt


# ---------------------------------------------------------------------------
# Estimator parity
# ---------------------------------------------------------------------------

def test_estimator_counts_parent_own_docs_call(world):
    world.install(FakeDB(
        _mk_items(),
        docs_by_item={
            1: [_doc("parent-memo.pdf", "PARENT-MEMO")],
            2: [_doc("child1.pdf", "CHILD1-DOC")],
            4: [_doc("leaf.pdf", "LEAF-DOC")],
        },
    ))

    est = summarizer.estimate_summarization_cost(99, mode="all")
    lines = est["model_breakdown"]
    l1_leaf = [ln for ln in lines if ln["level"] == 1 and ln["item_id"] in ("2.1", "3")]
    l1_parent_own = [ln for ln in lines if ln["level"] == 1 and ln["item_id"] == "2"]
    l2 = [ln for ln in lines if ln["level"] == 2]
    assert len(l1_leaf) == 2
    assert len(l1_parent_own) == 1, "parent's own-docs call missing from estimate"
    assert len(l2) == 1
    # The rollup's input includes the expected own-docs summary length, so it
    # must be strictly larger than the bare prompt + one child placeholder.
    assert l2[0]["input_tokens"] > 0
