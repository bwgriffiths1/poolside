"""Level 1 prompt assembly: general-context injection + meeting metadata.

The FERC docket side always injects prompts/general_context_prompt.md and a
rich metadata block; the ISO L1 path historically loaded doc_summary_prompt
bare, and agenda items couldn't see their own meeting's committee/venue/date
(db.get_agenda_items doesn't join meetings). These tests pin the parity
changes:

  * _load_doc_summary_prompt prepends the general context, brace-escaped so
    the later .format(filename=…, text=…) can't KeyError on edited context.
  * The meeting row is threaded into _item_metadata_block at L1 and L2.
  * The estimator uses the same context-injected prompt as the real run.
  * _resolve_meeting_folder is defensive about missing meetings/dates.
"""
from datetime import date
from types import SimpleNamespace

import pytest

import pipeline.summarizer as summarizer


MEETING = {
    "id": 99,
    "venue_short": "ISO-NE",
    "venue_name": "ISO New England",
    "type_short": "MC",
    "type_name": "Markets Committee",
    "meeting_date": date(2026, 8, 11),
    "title": None,
    "location": "Westborough, MA",
}


def _mk_items():
    # Parent "2" with child "2.1", plus leaf "3".
    return [
        {"id": 1, "item_id": "2", "title": "Parent item", "depth": 0, "parent_id": None},
        {"id": 2, "item_id": "2.1", "title": "Child one", "depth": 1, "parent_id": 1},
        {"id": 4, "item_id": "3", "title": "Leaf item", "depth": 0, "parent_id": None},
    ]


class FakeDB:
    """Just enough of pipeline.db for the summarize runner + estimator."""

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
        return dict(MEETING, id=meeting_id)

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
    """Patch summarizer's collaborators; _load_prompt is slug-sensitive so
    the general-context injection path is exercised for real."""
    llm_calls: list[tuple[str, str]] = []
    prompts = {
        "doc_summary_prompt": "DOCPROMPT {filename}\n{text}",
        "general_context_prompt": "CTX-GENERAL",
    }

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
                        lambda slug: prompts.get(slug, ""))
    monkeypatch.setattr(summarizer, "_get_text_for_doc",
                        lambda d: d.get("raw_content") or "")
    monkeypatch.setattr(summarizer, "_run_meeting_briefing",
                        lambda *a, **k: True)

    def install(fakedb):
        monkeypatch.setattr(summarizer, "db", fakedb)
        return fakedb

    return SimpleNamespace(install=install, llm_calls=llm_calls, prompts=prompts)


def _run(world, docs_by_item):
    fakedb = world.install(FakeDB(_mk_items(), docs_by_item=docs_by_item))
    result = summarizer.run_meeting_summarization(99, client=object(), force_rerun=True)
    assert result["errors"] == []
    return fakedb


def _prompt_for(world, label):
    return next(p for lbl, p in world.llm_calls if lbl == label)


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------

def test_l1_prompt_starts_with_general_context(world):
    _run(world, {4: [_doc("leaf.pdf", "LEAF-DOC")]})
    l1 = _prompt_for(world, "L1 item 3")
    assert l1.startswith("CTX-GENERAL\n\n")
    assert "LEAF-DOC" in l1


def test_context_braces_escaped(world):
    world.prompts["general_context_prompt"] = "CTX {weird}"
    _run(world, {4: [_doc("leaf.pdf", "LEAF-DOC")]})
    l1 = _prompt_for(world, "L1 item 3")
    # The context survives literally, and .format still filled {filename}/{text}
    # (the KeyError fallback would have left the raw placeholders in place).
    assert "CTX {weird}" in l1
    assert "{filename}" not in l1 and "{text}" not in l1
    assert "leaf.pdf" in l1 and "LEAF-DOC" in l1


def test_empty_context_leaves_template_bare(world):
    world.prompts["general_context_prompt"] = ""
    _run(world, {4: [_doc("leaf.pdf", "LEAF-DOC")]})
    l1 = _prompt_for(world, "L1 item 3")
    assert l1.startswith("DOCPROMPT ")


# ---------------------------------------------------------------------------
# Meeting metadata threading
# ---------------------------------------------------------------------------

def test_l1_and_l2_prompts_carry_meeting_metadata(world):
    _run(world, {
        2: [_doc("child1.pdf", "CHILD1-DOC")],
        4: [_doc("leaf.pdf", "LEAF-DOC")],
    })
    l1 = _prompt_for(world, "L1 item 3")
    l2 = _prompt_for(world, "L2 item 2")
    for prompt in (l1, l2):
        assert "**Meeting:** ISO New England Markets Committee — 2026-08-11" in prompt
        assert "**Location:** Westborough, MA" in prompt


def test_metadata_block_unit():
    bare_item = {"item_id": "3", "title": "Leaf item"}
    assert summarizer._item_metadata_block(bare_item) == ""
    block = summarizer._item_metadata_block(bare_item, MEETING)
    assert "**Meeting:** ISO New England Markets Committee — 2026-08-11" in block
    assert "**Location:** Westborough, MA" in block
    assert "**Agenda item:** 3  Leaf item" in block
    # Item-level fields still render after the meeting lines
    rich = summarizer._item_metadata_block(
        {"item_id": "3", "title": "Leaf item", "presenter": "J. Doe", "org": "ISO-NE"},
        MEETING,
    )
    assert rich.index("**Meeting:**") < rich.index("**Presenter:** J. Doe (ISO-NE)")


def test_metadata_block_without_meeting_unchanged():
    item = {"item_id": "3", "title": "Leaf item", "presenter": "J. Doe"}
    block = summarizer._item_metadata_block(item)
    assert block == "**Agenda item:** 3  Leaf item\n**Presenter:** J. Doe"


# ---------------------------------------------------------------------------
# Estimator parity
# ---------------------------------------------------------------------------

def test_estimator_includes_context_chars(world):
    world.prompts["general_context_prompt"] = "CTX-GENERAL " * 50
    world.install(FakeDB(_mk_items(), {4: [_doc("leaf.pdf", "LEAF-DOC")]}))
    with_ctx = summarizer.estimate_summarization_cost(99, mode="all")

    world.prompts["general_context_prompt"] = ""
    world.install(FakeDB(_mk_items(), {4: [_doc("leaf.pdf", "LEAF-DOC")]}))
    without_ctx = summarizer.estimate_summarization_cost(99, mode="all")

    l1_with = next(b for b in with_ctx["model_breakdown"] if b["level"] == 1)
    l1_without = next(b for b in without_ctx["model_breakdown"] if b["level"] == 1)
    assert l1_with["input_tokens"] > l1_without["input_tokens"]


# ---------------------------------------------------------------------------
# Meeting folder resolution
# ---------------------------------------------------------------------------

def test_resolve_meeting_folder_defensive():
    assert summarizer._resolve_meeting_folder(None) is None
    assert summarizer._resolve_meeting_folder({}) is None
    assert summarizer._resolve_meeting_folder(
        {"type_short": "MC", "type_name": "Markets Committee"}) is None  # no date


def test_resolve_meeting_folder_finds_dated_dir(tmp_path, monkeypatch):
    root = tmp_path / "materials" / "Markets Committee"
    (root / "MC_2026-08-11_regular").mkdir(parents=True)
    (root / "MC_2026-09-01_other").mkdir()
    monkeypatch.setattr(summarizer, "_REPO_ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text("storage_root: ./materials\n", encoding="utf-8")
    found = summarizer._resolve_meeting_folder(MEETING)
    assert found is not None and found.name == "MC_2026-08-11_regular"
