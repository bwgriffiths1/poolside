"""api/resummarize.py path selection + argument plumbing.

The single-item Re-run endpoint historically diverged from the full runner:
it loaded doc_summary_prompt bare (no general context) and hardcoded
extract_images=False / meeting_folder=None, so a Re-run on an image-enabled
deployment silently produced image-less summaries. These tests pin the fixed
plumbing on both paths (A: parent rollup, B: leaf doc summary) by
monkeypatching the summarizer entry points bound into the rz namespace.
"""
from datetime import date
from pathlib import Path

import pytest

import api.resummarize as rz


MEETING = {
    "id": 99,
    "venue_short": "ISO-NE",
    "type_short": "MC",
    "type_name": "Markets Committee",
    "meeting_date": date(2026, 8, 11),
}

PARENT = {"id": 10, "item_id": "2", "title": "Parent item", "parent_id": None, "meeting_id": 99}
CHILD = {"id": 11, "item_id": "2.1", "title": "Child one", "parent_id": 10, "meeting_id": 99}
LEAF = {"id": 12, "item_id": "3", "title": "Leaf item", "parent_id": None, "meeting_id": 99}


class FakeDB:
    def __init__(self, items, docs_by_item=None, summaries=None):
        self.items = {it["id"]: it for it in items}
        self.docs_by_item = docs_by_item or {}
        self.summaries = summaries or {}

    def get_agenda_item(self, item_id):
        return self.items.get(item_id)

    def get_meeting(self, meeting_id):
        return dict(MEETING, id=meeting_id)

    def get_agenda_items(self, meeting_id):
        return list(self.items.values())

    def get_documents_for_item(self, item_id):
        return list(self.docs_by_item.get(item_id, []))

    def get_current_summary(self, entity_type, entity_id):
        return self.summaries.get((entity_type, entity_id))


class Capture:
    def __init__(self, ret):
        self.calls: list[tuple[tuple, dict]] = []
        self.ret = ret

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


@pytest.fixture
def harness(monkeypatch):
    def build(fakedb, img_enabled=False, folder=None):
        caps = {
            "doc_summary": Capture(True),
            "own_docs": Capture("OWN-TEXT"),
            "rollup": Capture(True),
            "resolver": Capture(folder),
        }
        monkeypatch.setattr(rz, "db", fakedb)
        monkeypatch.setattr(rz, "make_client", lambda: object())
        monkeypatch.setattr(rz, "load_model_config", lambda: {
            "document_model": "doc-m", "item_model": "item-m",
            "document_max_tokens": 111, "item_max_tokens": 222,
        })
        monkeypatch.setattr(rz, "get_committee_prompts",
                            lambda t, v: ("B", "ITEM {item_id} {title} {doc_summaries}"))
        monkeypatch.setattr(rz, "load_doc_summary_prompt", lambda: "DOCPROMPT")
        monkeypatch.setattr(rz, "load_image_config", lambda: {"enabled": img_enabled})
        monkeypatch.setattr(rz, "resolve_meeting_folder", caps["resolver"])
        monkeypatch.setattr(rz, "run_item_doc_summary", caps["doc_summary"])
        monkeypatch.setattr(rz, "summarize_item_docs_text", caps["own_docs"])
        monkeypatch.setattr(rz, "run_item_rollup", caps["rollup"])
        monkeypatch.setattr(rz.lifecycle, "bump_lifecycle", lambda mid: "summarized")
        return caps

    return build


def _doc(name):
    return {"id": 1, "filename": name, "file_type": ".pdf",
            "ceii_skipped": False, "ignored": False}


# ---------------------------------------------------------------------------
# Path B — leaf with documents
# ---------------------------------------------------------------------------

def test_path_b_threads_images_and_meeting(harness):
    caps = harness(
        FakeDB([LEAF], docs_by_item={12: [_doc("leaf.pdf")]}),
        img_enabled=True, folder=Path("/fake/folder"),
    )
    result = rz.resummarize_agenda_item(12)
    assert result["ok"] is True and result["level"] == 1

    (_, kwargs), = caps["doc_summary"].calls
    assert kwargs["extract_images"] is True
    assert kwargs["meeting_folder"] == Path("/fake/folder")
    assert kwargs["meeting"]["type_short"] == "MC"
    assert kwargs["doc_summary_prompt"] == "DOCPROMPT"
    assert caps["resolver"].calls, "resolver should run when images are enabled"


def test_path_b_images_disabled(harness):
    caps = harness(FakeDB([LEAF], docs_by_item={12: [_doc("leaf.pdf")]}),
                   img_enabled=False)
    result = rz.resummarize_agenda_item(12)
    assert result["ok"] is True and result["level"] == 1

    (_, kwargs), = caps["doc_summary"].calls
    assert kwargs["extract_images"] is False
    assert kwargs["meeting_folder"] is None
    assert not caps["resolver"].calls, "resolver must not run when images are off"


# ---------------------------------------------------------------------------
# Path A — parent with children
# ---------------------------------------------------------------------------

def test_path_a_threads_images_meeting_and_pseudo_child(harness):
    caps = harness(
        FakeDB(
            [PARENT, CHILD],
            docs_by_item={10: [_doc("parent-memo.pdf")]},
            summaries={("agenda_item", 11): {"detailed": "C1-SUMMARY"}},
        ),
        img_enabled=True, folder=Path("/fake/folder"),
    )
    result = rz.resummarize_agenda_item(10)
    assert result["ok"] is True and result["level"] == 2

    (own_args, own_kwargs), = caps["own_docs"].calls
    assert own_args[3] == "DOCPROMPT"
    assert own_kwargs["extract_images"] is True
    assert own_kwargs["meeting_folder"] == Path("/fake/folder")
    assert own_kwargs["meeting"]["type_short"] == "MC"

    (_, rollup_kwargs), = caps["rollup"].calls
    assert rollup_kwargs["meeting"]["type_short"] == "MC"
    child_summaries = rollup_kwargs["child_summaries"]
    assert any(s.get("detailed") == "C1-SUMMARY" for _, s in child_summaries)
    pseudo = [(c, s) for c, s in child_summaries
              if c.get("title") == "Materials filed directly under this item"]
    assert len(pseudo) == 1
    assert pseudo[0][1]["detailed"] == "OWN-TEXT"


def test_path_a_requires_some_input(harness):
    harness(FakeDB([PARENT, CHILD]))  # children exist, none summarized, no docs
    result = rz.resummarize_agenda_item(10)
    assert result["ok"] is False and result["level"] == 2
    assert "Re-run those child items first" in result["reason"]


# ---------------------------------------------------------------------------
# Path C — nothing to do
# ---------------------------------------------------------------------------

def test_path_c_no_inputs(harness):
    harness(FakeDB([LEAF]))
    result = rz.resummarize_agenda_item(12)
    assert result["ok"] is False and result["level"] is None
