"""TLDR → summary_versions.one_line across the ISO L1/L2/L3 persist sites.

The FERC docket side has required a first-line `TLDR:` since it shipped
(split into one_line by what used to be docket_ingest._split_tldr); the ISO
sites always wrote one_line=None, leaving the briefing headline dead and
agenda rows on a regex-derived first-sentence fallback. These tests pin:

  * split_tldr (now in summarizer, aliased back into docket_ingest) —
    plain / bold / absent / empty contracts;
  * all four persist sites store the TLDR as one_line with a TLDR-free body;
  * rollup *input* never contains a TLDR line (own-docs and child bodies are
    embedded body-only);
  * non-compliant output degrades to one_line=None with the body unchanged
    (also pinned by test_l2_own_docs, whose fake emits no TLDR).
"""
from types import SimpleNamespace

import pytest

import pipeline.summarizer as summarizer
from pipeline.summarizer import split_tldr


# ---------------------------------------------------------------------------
# Unit contract (ported from the docket-side tests)
# ---------------------------------------------------------------------------

def test_split_tldr_plain():
    one, body = split_tldr("TLDR: The gist.\n\nFull body here.")
    assert one == "The gist."
    assert body == "Full body here."


def test_split_tldr_bold_variant():
    one, body = split_tldr("**TLDR:** The gist.\n\nFull body here.")
    assert one == "The gist."
    assert body == "Full body here."


def test_split_tldr_absent():
    one, body = split_tldr("Just a body with no marker.")
    assert one is None
    assert body == "Just a body with no marker."


def test_split_tldr_empty_inputs():
    assert split_tldr("") == (None, "")
    assert split_tldr(None) == (None, "")


def test_docket_alias_still_importable():
    from pipeline.docket_ingest import _split_tldr
    assert _split_tldr is split_tldr


# ---------------------------------------------------------------------------
# Fake world (TLDR-emitting LLM)
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
        return {"id": meeting_id, "type_short": "MC", "type_name": "Markets Committee"}

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
    """Like test_l2_own_docs' world, but the fake LLM complies with the TLDR
    contract and _run_meeting_briefing runs for real (L3 split coverage)."""
    llm_calls: list[tuple[str, str]] = []

    def fake_call_llm(client, model, prompt, max_tokens=4096, label=""):
        llm_calls.append((label, prompt))
        return f"TLDR: one-liner for {label}\n\nSUM({label})"

    monkeypatch.setattr(summarizer, "_call_llm", fake_call_llm)
    monkeypatch.setattr(summarizer, "_load_image_config", lambda: {"enabled": False})
    monkeypatch.setattr(summarizer, "_load_parallel_workers", lambda: 1)
    monkeypatch.setattr(summarizer, "_load_char_caps", lambda: (150_000, 600_000))
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

    def install(fakedb):
        monkeypatch.setattr(summarizer, "db", fakedb)
        return fakedb

    return SimpleNamespace(install=install, llm_calls=llm_calls)


# ---------------------------------------------------------------------------
# Persist sites
# ---------------------------------------------------------------------------

def test_all_levels_split_tldr_into_one_line(world):
    fakedb = world.install(FakeDB(
        _mk_items(),
        docs_by_item={
            1: [_doc("parent-memo.pdf", "PARENT-MEMO")],
            2: [_doc("child1.pdf", "CHILD1-DOC")],
            4: [_doc("leaf.pdf", "LEAF-DOC")],
        },
    ))

    result = summarizer.run_meeting_summarization(99, client=object(), force_rerun=True)
    assert result["errors"] == []
    assert result["level3"] is True

    # L1 leaf
    leaf = fakedb.versions[("agenda_item", 4)][-1]
    assert leaf["one_line"] == "one-liner for L1 item 3"
    assert leaf["detailed"] == "SUM(L1 item 3)"

    # L2 rollup parent
    parent = fakedb.versions[("agenda_item", 1)][-1]
    assert parent["one_line"] == "one-liner for L2 item 2"
    assert parent["detailed"] == "SUM(L2 item 2)"

    # L3 meeting briefing
    mtg = fakedb.versions[("meeting", 99)][-1]
    assert mtg["one_line"] == "one-liner for L3 meeting 99"
    assert mtg["detailed"] == "SUM(L3 meeting 99)"


def test_rollup_and_briefing_inputs_are_tldr_free(world):
    world.install(FakeDB(
        _mk_items(),
        docs_by_item={
            1: [_doc("parent-memo.pdf", "PARENT-MEMO")],
            2: [_doc("child1.pdf", "CHILD1-DOC")],
            4: [_doc("leaf.pdf", "LEAF-DOC")],
        },
    ))
    summarizer.run_meeting_summarization(99, client=object(), force_rerun=True)

    rollup_prompt = next(p for lbl, p in world.llm_calls if lbl == "L2 item 2")
    assert "SUM(L1 item 2.1)" in rollup_prompt  # child body embedded
    assert "SUM(L1 item 2)" in rollup_prompt    # own-docs body embedded
    assert "TLDR:" not in rollup_prompt

    briefing_prompt = next(p for lbl, p in world.llm_calls if lbl == "L3 meeting 99")
    assert "SUM(" in briefing_prompt
    assert "TLDR:" not in briefing_prompt


def test_own_docs_only_parent_keeps_tldr(world):
    """Children exist but contribute nothing → the own-docs summary IS the
    parent's summary, TLDR split included."""
    fakedb = world.install(FakeDB(
        _mk_items(),
        docs_by_item={1: [_doc("parent-memo.pdf", "PARENT-MEMO")]},
    ))
    result = summarizer.run_meeting_summarization(99, client=object(), force_rerun=True)
    assert result["errors"] == []
    parent = fakedb.versions[("agenda_item", 1)][-1]
    assert parent["one_line"] == "one-liner for L1 item 2"
    assert parent["detailed"] == "SUM(L1 item 2)"
