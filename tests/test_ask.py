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
    def __init__(self, dockets=None, summaries=None, roster=None):
        self.dockets = dockets or {}
        self.summaries = summaries or {}
        self.roster = roster or []
        self.asks: list[dict] = []
        self.log_calls: list[dict] = []

    def get_docket_by_number(self, num):
        return self.dockets.get(num)

    def get_current_summary(self, etype, eid):
        return self.summaries.get((etype, eid))

    def list_docket_filing_summaries(self, docket_ids):
        return [r for r in self.roster if r["docket_id"] in docket_ids]

    def record_ask(self, entry):
        self.asks.append(entry)
        return {"id": len(self.asks), "created_at": datetime(2026, 9, 9, 1, 2, 3)}

    def list_ask_log(self, limit=20, before_id=None, user_email=None):
        self.log_calls.append({"limit": limit, "before_id": before_id,
                               "user_email": user_email})
        return []


def _fake_prompts(template):
    """load_prompt stand-in: the ask template plus a marker directive per
    detail level, nothing else."""
    def load(slug):
        if slug == ask_mod.PROMPT_SLUG:
            return template
        if slug.startswith("ask_detail_"):
            return f"DIRECTIVE {slug.removeprefix('ask_detail_').upper()}"
        return ""
    return load


_USER = {"id": 3, "email": "ben@example.com", "role": "admin"}


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
        calls.setdefault("limits", []).append(limit)
        return [_hit("meeting", 1, rank=0.9), _hit("agenda_item", 9, rank=0.2)]

    def docket(q, limit, docket_ids=None):
        calls["docket"].append(docket_ids)
        calls.setdefault("limits", []).append(limit)
        # The state of play also matches — must de-dup against the seed.
        return [_docket_hit("docket_filing", 40, rank=0.5),
                _docket_hit("docket", 7, rank=0.3)]

    def document(q, limit, **kw):
        calls["document"].append({**kw, "limit": limit})
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


def test_gather_caps_follow_detail_level(scope_db, retrieval):
    scope = ask_mod.resolve_scope(AskBody(question="gas constraint", depth="documents"))
    ask_mod.gather_sources(scope, detail="brief")
    assert retrieval["limits"][-2:] == [12, 12]
    assert retrieval["document"][-1]["limit"] == 6
    assert retrieval["document"][-1]["passage_chars"] == 3200
    ask_mod.gather_sources(scope, detail="deep")
    assert retrieval["limits"][-2:] == [40, 40]
    assert retrieval["document"][-1]["limit"] == 8
    assert retrieval["document"][-1]["passage_chars"] == 4000


def test_gather_deep_docket_scope_includes_full_roster(scope_db, retrieval):
    scope_db.roster = [
        {"filing_id": 41, "docket_id": 7, "docket_number": "ER26-925",
         "accession_number": "20260201-0001", "document_class": "Comments/Protest",
         "description": "Comments of Party B", "filed_date": date(2026, 2, 1),
         "filing_parties": [], "treatment": "brief"},
        {"filing_id": 40, "docket_id": 7, "docket_number": "ER26-925",
         "accession_number": "20251230-5436", "document_class": "Comments/Protest",
         "description": "Protest of NEPGA", "filed_date": date(2025, 12, 30),
         "filing_parties": [], "treatment": "full"},
    ]
    scope = ask_mod.resolve_scope(AskBody(question="@ER26-925 every party's position"))
    hits = ask_mod.gather_sources(scope, detail="deep")
    keys = [(h["entity_type"], h["entity_id"]) for h in hits]
    # State of play, then the whole roster newest-first, then ranked extras
    # de-duplicated against it (filing 40 appears once).
    assert keys[:3] == [("docket", 7), ("docket_filing", 41), ("docket_filing", 40)]
    assert keys.count(("docket_filing", 40)) == 1
    assert hits[1]["tier"] == "summary" and hits[1]["accession_number"] == "20260201-0001"
    # Standard detail does NOT pull the roster.
    hits_std = ask_mod.gather_sources(scope, detail="standard")
    assert ("docket_filing", 41) not in [(h["entity_type"], h["entity_id"]) for h in hits_std]


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

TEMPLATE = "[RULES] cite [n]\n\n[DETAIL]\n\nQ: [QUESTION]\n\n[SOURCES]"


@pytest.fixture
def ask_env(monkeypatch):
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(TEMPLATE))
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
    # Default detail level's directive fills the placeholder, before the Q.
    assert prompt.index("DIRECTIVE STANDARD") < prompt.index("Q: Where")
    assert "[DETAIL]" not in prompt


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


def test_prompt_requires_detail_directive(ask_env, monkeypatch):
    monkeypatch.setattr(ask_mod, "load_prompt",
                        lambda slug: TEMPLATE if slug == ask_mod.PROMPT_SLUG else "")
    with pytest.raises(ValueError, match="ask_detail_deep"):
        build_ask_prompt("q", [_hit("meeting", 1)], detail="deep")


def test_prompt_appends_directive_when_placeholder_missing(ask_env, monkeypatch):
    # A prod prompt_overrides copy that predates [DETAIL] still gets the level.
    legacy = "[RULES] cite [n]\n\nQ: [QUESTION]\n\n[SOURCES]"
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(legacy))
    prompt = build_ask_prompt("q", [_hit("meeting", 1)], detail="deep")
    assert prompt.rstrip().endswith("DIRECTIVE DEEP")
    assert prompt.index("=== SOURCE [1]") < prompt.index("DIRECTIVE DEEP")


def test_state_of_play_exempt_from_source_cap(monkeypatch):
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(TEMPLATE))
    sop = "S" * 9000                      # > brief/standard caps, < SOP ceiling
    filing = "F" * 7000                   # > brief cap (6000), < deep cap
    monkeypatch.setattr(ask_mod, "db", _FakeDB(summaries={
        ("docket", 7): {"detailed": sop},
        ("docket_filing", 40): {"detailed": filing},
    }))
    hits = [_docket_hit("docket", 7), _docket_hit("docket_filing", 40)]
    brief = build_ask_prompt("q", hits, detail="brief")
    assert sop in brief                               # never truncated
    assert filing not in brief and "…(truncated)" in brief
    deep = build_ask_prompt("q", hits, detail="deep")
    assert filing in deep and "…(truncated)" not in deep


def test_sources_block_budget_trims_tail(monkeypatch):
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(TEMPLATE))
    monkeypatch.setattr(ask_mod, "_MAX_SOURCES_BLOCK_CHARS", 500)
    monkeypatch.setattr(ask_mod, "db", _FakeDB(summaries={
        ("meeting", i): {"detailed": f"body{i} " * 40} for i in range(1, 6)
    }))
    hits = [_hit("meeting", i) for i in range(1, 6)]
    prompt = build_ask_prompt("q", hits)
    assert "=== SOURCE [1]" in prompt and "=== SOURCE [5]" not in prompt
    # The hits list is trimmed in step so the response's source list matches
    # what the model actually saw.
    assert len(hits) < 5


# ---------------------------------------------------------------------------
# Route: no-hit short-circuit + happy path
# ---------------------------------------------------------------------------

def test_ask_no_hits_skips_llm(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(ask_mod, "db", fake)
    monkeypatch.setattr(ask_mod, "gather_sources", lambda scope, **kw: [])

    def boom():  # pragma: no cover — the assertion
        raise AssertionError("LLM client must not be created on zero hits")

    monkeypatch.setattr(ask_mod, "make_client", boom)
    out = ask_mod.ask(AskBody(question="anything about @EL99-1 nothing"), _USER)
    assert out["sources"] == []
    assert "couldn't find" in out["answer_md"].lower()
    assert "Not tracked here: EL99-1" in out["answer_md"]
    assert out["scope"]["unknown_dockets"] == ["EL99-1"]
    assert out["cost_usd"] is None
    # Even a no-result exchange is logged.
    assert len(fake.asks) == 1 and fake.asks[0]["model_id"] is None
    assert out["id"] == 1 and out["detail"] == "standard"


def test_ask_happy_path_serializes_sources(monkeypatch):
    hits = [
        _hit("agenda_item", 9, item_id="7.a", item_title="CAR vote"),
        _docket_hit("docket_filing", 40),
        {"entity_type": "docket_filing_file", "entity_id": 300, "tier": "document",
         "passage": "x", "file_desc": "Attachment B", "docket_id": 7,
         "docket_number": "ER26-925", "filing_id": 40,
         "filed_date": date(2025, 12, 30), "snippet": "…"},
    ]
    gathered: dict = {}

    def _gather(scope, **kw):
        gathered.update(kw)
        return hits

    monkeypatch.setattr(ask_mod, "gather_sources", _gather)
    fake = _FakeDB(summaries={("agenda_item", 9): {"detailed": "text"},
                              ("docket_filing", 40): {"detailed": "text"}})
    monkeypatch.setattr(ask_mod, "db", fake)
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(TEMPLATE))
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "m-ask"})
    monkeypatch.setattr(ask_mod, "make_client", lambda: object())
    seen: dict = {}

    def _fake_llm(client, model, prompt, max_tokens=0, label="", effort=None):
        seen["effort"] = effort
        return f"Answer [1]. ({model})"

    monkeypatch.setattr(ask_mod, "call_llm", _fake_llm)

    out = ask_mod.ask(AskBody(question="where does CAR-SA stand?", depth="documents"), _USER)
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
    assert gathered["detail"] == "standard"
    # Logged with the serialized sources and the caller's identity.
    entry = fake.asks[0]
    assert entry["user_email"] == "ben@example.com" and entry["user_id"] == 3
    assert entry["detail"] == "standard" and entry["model_id"] == "m-ask"
    assert [s["n"] for s in entry["sources"]] == [1, 2, 3]
    assert entry["answer_md"].startswith("Answer [1].")
    assert entry["duration_ms"] >= 0
    assert out["id"] == 1 and out["created_at"] == "2026-09-09T01:02:03"


def test_ask_log_failure_does_not_break_answer(monkeypatch):
    monkeypatch.setattr(ask_mod, "gather_sources", lambda scope, **kw: [_hit("meeting", 1)])

    class _Broken(_FakeDB):
        def record_ask(self, entry):
            raise RuntimeError("db down")

    monkeypatch.setattr(ask_mod, "db", _Broken(summaries={("meeting", 1): {"detailed": "t"}}))
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(TEMPLATE))
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "m"})
    monkeypatch.setattr(ask_mod, "make_client", lambda: object())
    monkeypatch.setattr(ask_mod, "call_llm", lambda *a, **k: "Answer [1].")
    out = ask_mod.ask(AskBody(question="anything at all"), _USER)
    assert out["answer_md"] == "Answer [1]." and "id" not in out


def test_deep_raises_effort_floor_only_when_unset(monkeypatch):
    monkeypatch.setattr(ask_mod, "gather_sources", lambda scope, **kw: [_hit("meeting", 1)])
    monkeypatch.setattr(ask_mod, "db", _FakeDB(summaries={("meeting", 1): {"detailed": "t"}}))
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(TEMPLATE))
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "m"})
    monkeypatch.setattr(ask_mod, "make_client", lambda: object())
    seen: list = []

    def _llm(client, model, prompt, max_tokens=0, label="", effort=None):
        seen.append(effort)
        assert "DIRECTIVE DEEP" in prompt or "DIRECTIVE STANDARD" in prompt
        return "A [1]."

    monkeypatch.setattr(ask_mod, "call_llm", _llm)
    ask_mod.ask(AskBody(question="anything at all", detail="deep"), _USER)
    ask_mod.ask(AskBody(question="anything at all", detail="deep", effort="low"), _USER)
    ask_mod.ask(AskBody(question="anything at all"), _USER)
    assert seen == ["medium", "low", ask_mod.DEFAULT_EFFORT]


def test_ask_body_rejects_unknown_detail():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AskBody(question="anything at all", detail="exhaustive")


def test_ask_history_scopes_to_own_unless_admin(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(ask_mod, "db", fake)
    viewer = {"id": 9, "email": "v@example.com", "role": "viewer"}
    out = ask_mod.ask_history(limit=500, all=True, user=viewer)
    assert fake.log_calls[-1] == {"limit": 100, "before_id": None,
                                  "user_email": "v@example.com"}
    assert out == {"items": [], "next_before_id": None}
    ask_mod.ask_history(limit=5, before_id=77, all=True, user=_USER)
    assert fake.log_calls[-1] == {"limit": 5, "before_id": 77, "user_email": None}


def test_serialize_log_row_matches_live_shape():
    from decimal import Decimal
    row = {"id": 4, "created_at": datetime(2026, 9, 9), "user_email": "b@x",
           "question": "q", "answer_md": "a [1]", "sources": [{"n": 1}],
           "scope": {"corpus": "all"}, "model_id": "m", "effort": "low",
           "detail": "deep", "cost_usd": Decimal("0.1234"), "duration_ms": 900}
    out = ask_mod._serialize_log_row(row)
    assert out["cost_usd"] == 0.1234 and out["created_at"] == "2026-09-09T00:00:00"
    assert out["sources"] == [{"n": 1}] and out["detail"] == "deep"


# ---------------------------------------------------------------------------
# Model + effort selection
# ---------------------------------------------------------------------------

def test_resolve_model_allowlist(monkeypatch):
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "m-cfg"})
    assert ask_mod.resolve_model(None) == "m-cfg"          # config default
    assert ask_mod.resolve_model("") == "m-cfg"
    assert ask_mod.resolve_model("claude-opus-5") == "claude-opus-5"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ask_mod.resolve_model("gpt-9")                      # not free text
    assert exc.value.status_code == 422


def test_ask_passes_model_and_effort_to_llm(monkeypatch):
    hits = [_hit("meeting", 1)]
    monkeypatch.setattr(ask_mod, "gather_sources", lambda scope, **kw: hits)
    monkeypatch.setattr(ask_mod, "db", _FakeDB(
        summaries={("meeting", 1): {"detailed": "text"}}))
    monkeypatch.setattr(ask_mod, "load_prompt", _fake_prompts(TEMPLATE))
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "m-ask"})
    monkeypatch.setattr(ask_mod, "make_client", lambda: object())
    seen: dict = {}

    def _fake_llm(client, model, prompt, max_tokens=0, label="", effort=None):
        seen["model"], seen["effort"] = model, effort
        return "Answer [1]."

    monkeypatch.setattr(ask_mod, "call_llm", _fake_llm)

    out = ask_mod.ask(AskBody(question="where does CAR-SA stand?",
                              model="claude-opus-5", effort="max"), _USER)
    assert seen == {"model": "claude-opus-5", "effort": "max"}
    assert out["model_id"] == "claude-opus-5" and out["effort"] == "max"

    out = ask_mod.ask(AskBody(question="where does CAR-SA stand?"), _USER)
    assert seen == {"model": "m-ask", "effort": ask_mod.DEFAULT_EFFORT}


def test_ask_body_rejects_unknown_effort():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AskBody(question="anything at all", effort="ultra")


def test_ask_options_lists_models_and_defaults(monkeypatch):
    monkeypatch.setattr(ask_mod, "load_model_config", lambda: {"ask_model": "claude-sonnet-5"})
    out = ask_mod.ask_options({})
    ids = [m["id"] for m in out["models"]]
    assert "claude-sonnet-5" in ids and "claude-opus-5" in ids
    haiku = next(m for m in out["models"] if m["id"].startswith("claude-haiku"))
    assert haiku["effort"] is False                          # UI greys effort out
    assert out["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert out["details"] == ["brief", "standard", "deep"]
    assert out["default_model"] == "claude-sonnet-5"
    assert out["default_effort"] == "low"
    assert out["default_detail"] == "standard"
