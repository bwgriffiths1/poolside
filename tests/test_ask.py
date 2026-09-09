"""Ask Poolside: query relaxation, retrieval fallback, docket scoping,
the two-tier (summaries / documents) source gathering, prompt assembly,
and the route's no-hit short-circuit.

All DB and LLM collaborators are stubbed; these tests pin:

  * or_query strips stopwords, de-dups, and keeps hyphenated initiative
    codes intact (CAR-SA must survive as one term);
  * retrieve_for_question only falls back to the OR query when the strict
    websearch pass leaves fewer than 3 hits, and de-dups across passes;
  * @docket mentions are parsed out of the question, normalized, resolved
    against tracked dockets, and force the corpus to "dockets";
  * gather_sources seeds a scoped docket's state of play first, interleaves
    meeting + docket summary hits by rank, de-dups, and only pulls document
    passages at documents depth;
  * score_passages picks the paragraph windows that carry the question's
    terms and never returns empty for non-empty text;
  * build_ask_prompt numbers sources in rank order, carries provenance
    labels (incl. docket / filing / excerpt forms), strips image markup,
    and refuses to run without its template;
  * the route answers "nothing found" without paying for an LLM call, and
    names untracked dockets in that answer.
"""
from datetime import date, datetime

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


def _docket_hit(etype, eid, **over):
    base = {
        "entity_type": etype, "entity_id": eid, "docket_id": 7,
        "docket_number": "ER26-925", "docket_title": "Prompt capacity market",
        "filing_id": 40 if etype != "docket" else None,
        "accession_number": "20251230-5436" if etype != "docket" else None,
        "document_class": "Comments/Protest" if etype != "docket" else None,
        "filed_date": date(2025, 12, 30) if etype != "docket" else None,
        "filing_parties": [{"type": "AUTHOR", "org": "NEPGA"},
                           {"type": "AGENT", "org": "Law Firm LLP"}],
        "description": "Protest of NEPGA", "snippet": "…", "rank": 0.4,
        "tier": "summary",
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


def test_docket_retrieval_shares_the_fallback_rule(monkeypatch):
    calls = []

    def fake(q, limit=15, docket_ids=None):
        calls.append((q, docket_ids))
        return [] if len(calls) == 1 else [_docket_hit("docket_filing", 40)]

    monkeypatch.setattr(search_svc, "search_docket_summary_hits", fake)
    hits = search_svc.retrieve_docket_summaries("what did NEPGA protest",
                                                docket_ids=[7])
    assert [c[0] for c in calls] == ["what did NEPGA protest", "NEPGA or protest"]
    assert all(c[1] == [7] for c in calls)
    assert [h["entity_id"] for h in hits] == [40]


# ---------------------------------------------------------------------------
# Mentions + scope
# ---------------------------------------------------------------------------

def test_parse_mentions_normalizes_and_strips():
    q, nums = ask_mod.parse_mentions(
        "Compare @er26-925-000 with @EL25-12; does @ER26-925 change anything? "
        "email me@example.com")
    assert nums == ["ER26-925", "EL25-12"]          # de-duped, suffix dropped
    assert "@" not in q.replace("me@example.com", "")  # mentions gone…
    assert "me@example.com" in q                     # …emails untouched
    assert "Compare with" in q                       # whitespace tidied


class _FakeDB:
    def __init__(self, dockets=None, summaries=None):
        self.dockets = dockets or {}
        self.summaries = summaries or {}

    def get_docket_by_number(self, num):
        return self.dockets.get(num)

    def get_current_summary(self, etype, eid):
        return self.summaries.get((etype, eid))


@pytest.fixture
def scope_db(monkeypatch):
    fake = _FakeDB(
        dockets={"ER26-925": {"id": 7, "docket_number": "ER26-925",
                              "title": "Prompt capacity market"}},
        summaries={
            ("docket", 7): {"one_line": "Order accepted the filing.",
                            "detailed": "State of play body.",
                            "created_at": datetime(2026, 7, 23, 12, 0)},
            ("meeting", 1): {"detailed": "Briefing text."},
            ("docket_filing", 40): {"detailed": "Filing summary."},
        },
    )
    monkeypatch.setattr(ask_mod, "db", fake)
    return fake


def test_resolve_scope_mentions_force_docket_corpus(scope_db):
    body = AskBody(question="Where does @ER26-925 stand vs @EL25-12?",
                   corpus="meetings", docket_numbers=["er26-925-001"])
    scope = ask_mod.resolve_scope(body)
    assert scope["corpus"] == "dockets"
    assert scope["docket_ids"] == [7]
    assert scope["unknown_dockets"] == ["EL25-12"]
    assert "@" not in scope["retrieval_question"]


def test_resolve_scope_plain_question_keeps_corpus(scope_db):
    scope = ask_mod.resolve_scope(AskBody(question="Where does CAR-SA stand?"))
    assert scope["corpus"] == "all" and scope["dockets"] == []
    assert scope["retrieval_question"] == "Where does CAR-SA stand?"


# ---------------------------------------------------------------------------
# gather_sources
# ---------------------------------------------------------------------------

@pytest.fixture
def retrieval(monkeypatch):
    calls = {"meeting": [], "docket": [], "document": []}

    def meeting(q, limit, **kw):
        calls["meeting"].append(kw)
        return [_hit("meeting", 1, rank=0.9), _hit("agenda_item", 9, rank=0.2)]

    def docket(q, limit, docket_ids=None):
        calls["docket"].append(docket_ids)
        # The state of play also matches — must de-dup against the seed.
        return [_docket_hit("docket_filing", 40, rank=0.5),
                _docket_hit("docket", 7, rank=0.3)]

    def document(q, limit, **kw):
        calls["document"].append(kw)
        return [{"entity_type": "docket_filing_file", "entity_id": 300,
                 "tier": "document", "passage": "verbatim text",
                 "file_desc": "No description given",
                 "orig_file_name": "protest.pdf", "docket_id": 7,
                 "docket_number": "ER26-925", "filing_id": 40,
                 "accession_number": "20251230-5436", "snippet": "…"}]

    monkeypatch.setattr(ask_mod, "retrieve_for_question", meeting)
    monkeypatch.setattr(ask_mod, "retrieve_docket_summaries", docket)
    monkeypatch.setattr(ask_mod, "retrieve_document_hits", document)
    return calls


def test_gather_all_corpus_interleaves_by_rank(scope_db, retrieval):
    scope = ask_mod.resolve_scope(AskBody(question="Where does CAR-SA stand?"))
    hits = ask_mod.gather_sources(scope)
    assert [(h["entity_type"], h["entity_id"]) for h in hits] == [
        ("meeting", 1), ("docket_filing", 40), ("docket", 7), ("agenda_item", 9),
    ]
    assert retrieval["document"] == []          # summaries depth: no passages
    assert retrieval["docket"] == [None]        # unscoped → all dockets


def test_gather_scoped_docket_seeds_state_of_play(scope_db, retrieval):
    scope = ask_mod.resolve_scope(
        AskBody(question="@ER26-925 what did NEPGA argue?", depth="documents"))
    hits = ask_mod.gather_sources(scope)
    types = [(h["entity_type"], h["entity_id"]) for h in hits]
    assert types[0] == ("docket", 7)            # seeded, match or not
    assert types.count(("docket", 7)) == 1      # de-duped vs the search hit
    assert ("meeting", 1) not in types          # meetings corpus skipped
    assert types[-1] == ("docket_filing_file", 300)
    assert retrieval["meeting"] == []
    assert retrieval["docket"] == [[7]]
    assert retrieval["document"][0]["docket_ids"] == [7]
    assert retrieval["document"][0]["corpus"] == "dockets"


def test_gather_meetings_corpus_passes_filters(scope_db, retrieval):
    scope = ask_mod.resolve_scope(AskBody(question="gas constraint", corpus="meetings"))
    ask_mod.gather_sources(scope, type_short="MC", from_date=date(2026, 1, 1))
    assert retrieval["meeting"] == [{"type_short": "MC", "from_date": date(2026, 1, 1)}]
    assert retrieval["docket"] == []


# ---------------------------------------------------------------------------
# Passage extraction
# ---------------------------------------------------------------------------

def test_score_passages_picks_matching_windows():
    filler = "Nothing relevant here. " * 60          # ~1.4k chars → own window
    text = (filler + "\n\n" + "The seasonal auction design drew protests. "
            "Auctions would run twice a year.\n\n" + filler)
    score, passage = search_svc.score_passages(text, "seasonal auction design")
    assert score > 0
    assert "seasonal auction design" in passage
    assert "Auctions" in passage                      # prefix stem matched
    assert passage.count("Nothing relevant") < 60     # filler windows dropped


def test_score_passages_falls_back_to_opening():
    score, passage = search_svc.score_passages("Opening lines.\n\nMore.", "zzz")
    assert score == 0 and passage.startswith("Opening lines.")
    assert search_svc.score_passages("", "anything") == (0, "")


def test_variant_key_collapses_clean_redline_copies():
    k = search_svc._variant_key
    assert k("M-04-ICAP-v17.0-DRAFT (Clean).pdf") == k("M-04-ICAP v17.0 DRAFT (Redline).pdf")
    assert k("a03_memo.pdf") != k("a04_memo.pdf")


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
        ("docket", 7): {"detailed": "State of play."},
        ("docket_filing", 40): {"detailed": "Filing summary."},
    }
    monkeypatch.setattr(ask_mod, "db", _FakeDB(summaries=summaries))


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
    assert "(Scope:" not in prompt   # unscoped, summaries depth → no scope line


def test_prompt_labels_docket_filing_and_excerpt_sources(ask_env):
    hits = [
        _docket_hit("docket", 7, summary_date=datetime(2026, 7, 23)),
        _docket_hit("docket_filing", 40),
        {"entity_type": "docket_filing_file", "entity_id": 300, "tier": "document",
         "passage": "Verbatim words.", "file_desc": "No description given",
         "orig_file_name": "protest.pdf", "docket_number": "ER26-925",
         "accession_number": "20251230-5436", "filing_parties": []},
        {"entity_type": "document", "entity_id": 55, "tier": "document",
         "raw_content": "Intro.\n\nThe gas constraint design changed.",
         "filename": "a04_gas.pdf", "type_short": "MC",
         "meeting_date": "2026-01-13", "item_id": "4.1", "item_title": "Gas"},
    ]
    scope = {"corpus": "dockets", "depth": "documents",
             "dockets": [{"docket_number": "ER26-925", "title": "Prompt"}],
             "retrieval_question": "gas constraint design"}
    prompt = build_ask_prompt("@ER26-925 what changed?", hits, scope)
    assert ("[1] FERC docket ER26-925 (Prompt capacity market) — "
            "state of play as of 2026-07-23") in prompt
    # Filings don't repeat the title; author = AUTHOR party only.
    assert ("[2] FERC docket ER26-925 — filing 20251230-5436 — Comments/Protest"
            " — filed 2025-12-30 — by NEPGA — Protest of NEPGA") in prompt
    assert "Law Firm LLP" not in prompt
    assert "DOCUMENT EXCERPT: protest.pdf" in prompt      # placeholder desc skipped
    assert "Verbatim excerpt from the underlying document" in prompt
    assert "Verbatim words." in prompt
    # A document hit without a precomputed passage gets one cut here.
    assert ("[4] MC meeting 2026-01-13 — agenda 4.1: Gas — "
            "DOCUMENT EXCERPT: a04_gas.pdf") in prompt
    assert "gas constraint design changed" in prompt
    assert "(Scope: FERC docket ER26-925 (Prompt), including the underlying documents.)" in prompt


def test_prompt_requires_template(ask_env, monkeypatch):
    monkeypatch.setattr(ask_mod, "load_prompt", lambda slug: "")
    with pytest.raises(ValueError, match="not found"):
        build_ask_prompt("q", [_hit("meeting", 1)])


# ---------------------------------------------------------------------------
# Route: no-hit short-circuit + happy path
# ---------------------------------------------------------------------------

def test_ask_no_hits_skips_llm(monkeypatch):
    monkeypatch.setattr(ask_mod, "db", _FakeDB())
    monkeypatch.setattr(ask_mod, "gather_sources", lambda scope, **kw: [])

    def boom():  # pragma: no cover — the assertion
        raise AssertionError("LLM client must not be created on zero hits")

    monkeypatch.setattr(ask_mod, "make_client", boom)
    out = ask_mod.ask(AskBody(question="anything about @EL99-1 nothing"), {})
    assert out["sources"] == []
    assert "couldn't find" in out["answer_md"].lower()
    assert "Not tracked here: EL99-1" in out["answer_md"]
    assert out["scope"]["unknown_dockets"] == ["EL99-1"]
    assert out["cost_usd"] is None


def test_ask_happy_path_serializes_sources(monkeypatch):
    hits = [
        _hit("agenda_item", 9, item_id="7.a", item_title="CAR vote"),
        _docket_hit("docket_filing", 40),
        {"entity_type": "docket_filing_file", "entity_id": 300, "tier": "document",
         "passage": "x", "file_desc": "Attachment B", "docket_id": 7,
         "docket_number": "ER26-925", "filing_id": 40,
         "filed_date": date(2025, 12, 30), "snippet": "…"},
    ]
    monkeypatch.setattr(ask_mod, "gather_sources", lambda scope, **kw: hits)
    monkeypatch.setattr(ask_mod, "db", _FakeDB(
        summaries={("agenda_item", 9): {"detailed": "text"},
                   ("docket_filing", 40): {"detailed": "text"}}))
    monkeypatch.setattr(ask_mod, "load_prompt",
                        lambda slug: TEMPLATE if slug == ask_mod.PROMPT_SLUG else "")
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "m-ask"})
    monkeypatch.setattr(ask_mod, "make_client", lambda: object())
    seen: dict = {}

    def _fake_llm(client, model, prompt, max_tokens=0, label="", effort=None):
        seen["effort"] = effort
        return f"Answer [1]. ({model})"

    monkeypatch.setattr(ask_mod, "call_llm", _fake_llm)

    out = ask_mod.ask(AskBody(question="where does CAR-SA stand?", depth="documents"), {})
    # Ask opts out of the model family's default effort — it's interactive and
    # reads already-summarized text.
    assert seen["effort"] == "low"
    assert out["model_id"] == "m-ask"
    assert out["answer_md"].startswith("Answer [1].")
    assert [s["n"] for s in out["sources"]] == [1, 2, 3]
    assert out["sources"][0]["item_id"] == "7.a"
    assert out["sources"][0]["tier"] == "summary"
    assert out["sources"][1]["docket_number"] == "ER26-925"
    assert out["sources"][1]["filed_date"] == "2025-12-30"   # ISO string, JSON-safe
    assert out["sources"][2]["tier"] == "document"
    assert out["sources"][2]["filename"] == "Attachment B"
    assert out["scope"] == {"corpus": "all", "depth": "documents",
                            "dockets": [], "unknown_dockets": []}
