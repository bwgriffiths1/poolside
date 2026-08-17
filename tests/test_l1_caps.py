"""L1 input caps: per-doc truncation + per-item total (FERC parity).

The ISO L1 path historically concatenated every document of an agenda item
uncapped — a single text-heavy PDF could blow past the model window and
silently starve its siblings (lost-in-the-middle). These tests pin the
docket_ingest-style guards:

  * over-cap docs are truncated at a line boundary with an explicit
    "…(truncated)" marker;
  * once the per-item total is reached, remaining docs are omitted with an
    explicit "…(further documents omitted for length)" marker;
  * neither marker appears under the default caps;
  * the metadata block is never truncated;
  * the cost estimator mirrors the same capping;
  * config keys summarization.max_chars_per_doc/_item override the defaults.
"""
import pytest

import pipeline.summarizer as summarizer
from pipeline import appconfig


TRUNC = "…(truncated)"
OMIT = "…(further documents omitted for length)"


def _doc(name, content):
    return {"id": abs(hash(name)) % 10_000, "filename": name, "file_type": ".pdf",
            "ceii_skipped": False, "ignored": False, "raw_content": content}


@pytest.fixture
def direct(monkeypatch):
    """Drive _summarize_item_docs directly; returns the assembled prompt."""
    captured: list[str] = []

    def fake_call_llm(client, model, prompt, max_tokens=4096, label=""):
        captured.append(prompt)
        return "SUM"

    monkeypatch.setattr(summarizer, "_call_llm", fake_call_llm)
    monkeypatch.setattr(summarizer, "_load_image_config", lambda: {"enabled": False})
    monkeypatch.setattr(summarizer, "_get_text_for_doc",
                        lambda d: d.get("raw_content") or "")

    def run(docs, caps=None, item=None, meeting=None):
        if caps is not None:
            monkeypatch.setattr(summarizer, "_load_char_caps", lambda: caps)

        class _DB:
            def get_documents_for_item(self, item_id):
                return list(docs)

        monkeypatch.setattr(summarizer, "db", _DB())
        out = summarizer._summarize_item_docs(
            item or {"id": 1, "item_id": "3", "title": "Leaf item"},
            client=object(), model="m",
            doc_summary_prompt="DOC {filename}\n{text}",
            meeting=meeting,
        )
        assert out == "SUM"
        return captured[-1]

    return run


# ---------------------------------------------------------------------------
# Truncation behavior
# ---------------------------------------------------------------------------

def test_per_doc_truncation_marker(direct):
    content = "first-line\n" + "x" * 300 + "\nlast-line"
    prompt = direct([_doc("big.pdf", content)], caps=(100, 10_000))
    assert TRUNC in prompt
    assert "first-line" in prompt
    assert "last-line" not in prompt
    assert OMIT not in prompt


def test_per_item_omission_marker(direct):
    docs = [_doc("doc1.pdf", "A" * 90),
            _doc("doc2.pdf", "B" * 90),
            _doc("doc3.pdf", "C" * 90)]
    prompt = direct(docs, caps=(1000, 150))
    # doc1 (90) fits; doc2 checked at 90 < 150 so it's included (180 total);
    # doc3 is checked at 180 >= 150 and omitted with the marker.
    assert "### [doc1.pdf]" in prompt and "### [doc2.pdf]" in prompt
    assert "### [doc3.pdf]" not in prompt
    assert "C" * 10 not in prompt
    assert OMIT in prompt
    assert TRUNC not in prompt


def test_no_markers_under_default_caps(direct):
    prompt = direct(
        [_doc("a.pdf", "small doc"), _doc("b.pdf", "another small doc")],
        caps=(summarizer._MAX_CHARS_PER_DOC, summarizer._MAX_CHARS_PER_ITEM),
    )
    assert TRUNC not in prompt and OMIT not in prompt


def test_caps_do_not_touch_metadata(direct):
    meeting = {"venue_name": "ISO New England", "type_name": "Markets Committee",
               "meeting_date": "2026-08-11"}
    item = {"id": 1, "item_id": "3", "title": "Leaf item", "presenter": "J. Doe"}
    prompt = direct([_doc("big.pdf", "line\n" + "x" * 500)],
                    caps=(100, 10_000), item=item, meeting=meeting)
    assert "**Presenter:** J. Doe" in prompt
    assert "**Meeting:** ISO New England Markets Committee — 2026-08-11" in prompt
    assert TRUNC in prompt


# ---------------------------------------------------------------------------
# Estimator parity
# ---------------------------------------------------------------------------

def test_estimator_mirrors_caps(monkeypatch):
    docs = [_doc("doc1.pdf", "A" * 90),
            _doc("doc2.pdf", "B" * 90),
            _doc("doc3.pdf", "C" * 90)]
    items = [{"id": 1, "item_id": "3", "title": "Substantive item",
              "depth": 0, "parent_id": None}]

    class _DB:
        def get_agenda_items(self, meeting_id):
            return list(items)

        def get_documents_for_item(self, item_id):
            return list(docs)

        def get_current_summary(self, entity_type, entity_id):
            return None

        def get_meeting(self, meeting_id):
            return {"id": meeting_id, "type_short": "MC", "venue_short": "ISO-NE"}

    prompts = {"doc_summary_prompt": "DOC {filename}\n{text}",
               "general_context_prompt": ""}
    monkeypatch.setattr(summarizer, "db", _DB())
    monkeypatch.setattr(summarizer, "_load_prompt", lambda slug: prompts.get(slug, ""))
    monkeypatch.setattr(summarizer, "_get_committee_prompts", lambda c, v: ("B", "I"))
    monkeypatch.setattr(summarizer, "_load_model_config", lambda: {
        "document_model": "claude-haiku-4-5-20251001",
        "item_model": "claude-haiku-4-5-20251001",
        "meeting_model": "claude-haiku-4-5-20251001",
        "document_max_tokens": 1000,
        "item_max_tokens": 1000,
        "meeting_max_tokens": 1000,
    })
    monkeypatch.setattr(summarizer, "_load_char_caps", lambda: (1000, 150))

    est = summarizer.estimate_summarization_cost(99, mode="all")
    l1 = next(b for b in est["model_breakdown"] if b["level"] == 1)
    # Mirrors the runner: doc1 + doc2 counted (90 + 90), doc3 dropped at the cap.
    expected_chars = len(summarizer._load_doc_summary_prompt()) + 180
    assert l1["input_tokens"] == summarizer._approx_tokens_from_chars(expected_chars)


# ---------------------------------------------------------------------------
# Config override
# ---------------------------------------------------------------------------

def test_caps_config_override(monkeypatch):
    monkeypatch.setattr(appconfig, "get_config",
                        lambda: {"summarization": {"max_chars_per_doc": 42}})
    assert summarizer._load_char_caps() == (42, summarizer._MAX_CHARS_PER_ITEM)

    monkeypatch.setattr(appconfig, "get_config", lambda: {})
    assert summarizer._load_char_caps() == (
        summarizer._MAX_CHARS_PER_DOC, summarizer._MAX_CHARS_PER_ITEM)
