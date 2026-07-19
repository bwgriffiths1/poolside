"""Ask Poolside: query relaxation, retrieval fallback, prompt assembly,
and the route's no-hit short-circuit.

All DB and LLM collaborators are stubbed; these tests pin:

  * or_query strips stopwords, de-dups, and keeps hyphenated initiative
    codes intact (CAR-SA must survive as one term);
  * retrieve_for_question only falls back to the OR query when the strict
    websearch pass leaves fewer than 3 hits, and de-dups across passes;
  * build_ask_prompt numbers sources in rank order, carries provenance
    labels, strips image markup, and refuses to run without its template;
  * the route answers "nothing found" without paying for an LLM call.
"""
import pytest

import api.routes.ask as ask_mod
import api.services.search as search_svc
from api.routes.ask import AskBody, build_ask_prompt


# ---------------------------------------------------------------------------
# or_query
# ---------------------------------------------------------------------------

def test_or_query_strips_stopwords_and_dedups():
    q = search_svc.or_query("What is the latest status of CAR-SA and the car-sa vote?")
    # CAR-SA survives hyphenated, appears once; stopwords vanish.
    assert q == "CAR-SA or vote"


def test_or_query_empty_when_only_stopwords():
    assert search_svc.or_query("what is the status") == ""


# ---------------------------------------------------------------------------
# retrieve_for_question
# ---------------------------------------------------------------------------

def _hit(etype, eid, **over):
    base = {
        "entity_type": etype, "entity_id": eid, "meeting_id": 1,
        "meeting_title": "MC", "meeting_date": "2026-05-12",
        "venue": "ISO-NE", "type_short": "MC", "item_id": None,
        "item_title": None, "presenter": None, "organization": None,
        "snippet": "…", "rank": 0.5,
    }
    base.update(over)
    return base


def test_retrieval_no_fallback_when_enough_hits(monkeypatch):
    calls = []

    def fake_search(q, limit=15, **kw):
        calls.append(q)
        return [_hit("meeting", i) for i in range(3)]

    monkeypatch.setattr(search_svc, "search_summary_hits", fake_search)
    hits = search_svc.retrieve_for_question("CAR-SA seasonal auction design")
    assert len(hits) == 3
    assert calls == ["CAR-SA seasonal auction design"]


def test_retrieval_falls_back_and_dedups(monkeypatch):
    calls = []

    def fake_search(q, limit=15, **kw):
        calls.append(q)
        # First (strict websearch) pass finds one hit; the relaxed pass —
        # any later call — returns an overlap plus something new.
        if len(calls) == 1:
            return [_hit("meeting", 1)]
        return [_hit("meeting", 1), _hit("agenda_item", 9)]

    monkeypatch.setattr(search_svc, "search_summary_hits", fake_search)
    hits = search_svc.retrieve_for_question("where does CAR-SA stand")
    assert calls == ["where does CAR-SA stand", "CAR-SA"]  # relaxed pass ran
    # meeting#1 deduped across passes; item#9 appended.
    assert [(h["entity_type"], h["entity_id"]) for h in hits] == [
        ("meeting", 1), ("agenda_item", 9),
    ]


# ---------------------------------------------------------------------------
# build_ask_prompt
# ---------------------------------------------------------------------------

TEMPLATE = "[RULES] cite [n]\n\nQ: [QUESTION]\n\n[SOURCES]"


@pytest.fixture
def ask_env(monkeypatch):
    monkeypatch.setattr(
        ask_mod, "load_prompt",
        lambda slug: TEMPLATE if slug == ask_mod.PROMPT_SLUG else "",
    )
    summaries = {
        ("meeting", 1): {"detailed": "Briefing text.\n<!-- image_id:4 -->"},
        ("agenda_item", 9): {"detailed": "Item text\n**Figure:** x"},
    }
    monkeypatch.setattr(
        ask_mod, "db",
        type("FakeDB", (), {
            "get_current_summary": staticmethod(
                lambda etype, eid: summaries.get((etype, eid))
            ),
        }),
    )


def test_prompt_numbers_and_labels_sources(ask_env):
    hits = [
        _hit("meeting", 1),
        _hit("agenda_item", 9, item_id="7.a", item_title="CAR vote",
             presenter="J. Smith", organization="ISO-NE"),
    ]
    prompt = build_ask_prompt("Where does CAR-SA stand?", hits)
    assert "Q: Where does CAR-SA stand?" in prompt
    assert "[1] MC meeting 2026-05-12 — meeting briefing" in prompt
    assert ("[2] MC meeting 2026-05-12 — agenda 7.a: CAR vote — "
            "presented by J. Smith (ISO-NE)") in prompt
    # Rule text with literal [n] untouched by the replace.
    assert "[RULES] cite [n]" in prompt
    # Image markup never reaches the model.
    assert "image_id" not in prompt and "**Figure:**" not in prompt


def test_prompt_requires_template(ask_env, monkeypatch):
    monkeypatch.setattr(ask_mod, "load_prompt", lambda slug: "")
    with pytest.raises(ValueError, match="not found"):
        build_ask_prompt("q", [_hit("meeting", 1)])


# ---------------------------------------------------------------------------
# Route: no-hit short-circuit
# ---------------------------------------------------------------------------

def test_ask_no_hits_skips_llm(monkeypatch):
    monkeypatch.setattr(ask_mod, "retrieve_for_question",
                        lambda q, limit, **kw: [])

    def boom():  # pragma: no cover — the assertion
        raise AssertionError("LLM client must not be created on zero hits")

    monkeypatch.setattr(ask_mod, "make_client", boom)
    out = ask_mod.ask(AskBody(question="anything about nothing"), {})
    assert out["sources"] == []
    assert "couldn't find" in out["answer_md"].lower()
    assert out["cost_usd"] is None


def test_ask_happy_path_serializes_sources(monkeypatch):
    hits = [
        _hit("agenda_item", 9, item_id="7.a", item_title="CAR vote"),
        _hit("meeting", 1),
    ]
    monkeypatch.setattr(ask_mod, "retrieve_for_question",
                        lambda q, limit, **kw: hits)
    monkeypatch.setattr(ask_mod, "load_prompt",
                        lambda slug: TEMPLATE if slug == ask_mod.PROMPT_SLUG else "")
    monkeypatch.setattr(
        ask_mod, "db",
        type("FakeDB", (), {
            "get_current_summary": staticmethod(lambda e, i: {"detailed": "text"}),
        }),
    )
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "m-ask"})
    monkeypatch.setattr(ask_mod, "make_client", lambda: object())
    monkeypatch.setattr(
        ask_mod, "call_llm",
        lambda client, model, prompt, max_tokens=0, label="": f"Answer [1]. ({model})",
    )

    out = ask_mod.ask(AskBody(question="where does CAR-SA stand?"), {})
    assert out["model_id"] == "m-ask"
    assert out["answer_md"].startswith("Answer [1].")
    assert [s["n"] for s in out["sources"]] == [1, 2]
    assert out["sources"][0]["item_id"] == "7.a"
