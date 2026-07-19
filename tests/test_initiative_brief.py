"""Initiative briefs: prompt assembly, runner status transitions, staleness.

The runner tests drive run_initiative_brief against a fake of the db module
and a stubbed LLM (no Postgres, no network), pinning:

  * items are presented to the model oldest-first with committee/date/vote
    context, image refs stripped, unsummarized items placeholdered;
  * success writes status='complete' + the source snapshot used for
    staleness; failures (no items, empty LLM output, missing template)
    land as status='error' with a message, never as a raised exception.

brief_is_stale is the API-side staleness rule shown in the UI.
"""
from types import SimpleNamespace

import pytest

import pipeline.initiative_brief as ib
from api.routes.initiatives import brief_is_stale


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TAG = {"id": 7, "name": "CAR-SA", "tag_type": "initiative",
       "description": "Capacity auction reform"}


def _mk_items():
    # Deliberately newest-first (the db helper's UI ordering).
    return [
        {
            "item_db_id": 3, "item_id": "5", "item_title": "CAR vote",
            "presenter": "J. Smith", "organization": "ISO-NE",
            "vote_status": "Approved", "meeting_id": 30,
            "meeting_date": "2026-06-10", "type_short": "NPC",
            "type_name": "Participants Committee", "venue": "ISO-NE",
            "summary_detailed": "The committee approved the package.\n"
                                "**Figure:** chart\n<!-- image_id:9 -->",
            "summary_one_line": None,
            "summary_status": "approved", "summary_version": 2,
        },
        {
            "item_db_id": 2, "item_id": "7.1", "item_title": "CAR design update",
            "presenter": None, "organization": None,
            "vote_status": None, "meeting_id": 20,
            "meeting_date": "2026-05-12", "type_short": "MC",
            "type_name": "Markets Committee", "venue": "ISO-NE",
            "summary_detailed": "Design details were presented.",
            "summary_one_line": None,
            "summary_status": "draft", "summary_version": 1,
        },
        {
            "item_db_id": 1, "item_id": "2", "item_title": "CAR future look",
            "presenter": None, "organization": None,
            "vote_status": None, "meeting_id": 40,
            "meeting_date": "2026-07-08", "type_short": "MC",
            "type_name": "Markets Committee", "venue": "ISO-NE",
            "summary_detailed": None, "summary_one_line": None,
            "summary_status": None, "summary_version": None,
        },
    ]


class FakeDB:
    """Just enough of pipeline.db for the brief runner."""

    def __init__(self, tag=TAG, items=None):
        self.tag = tag
        self.items = items if items is not None else _mk_items()
        self.updates: list[dict] = []

    def get_tag(self, tag_id):
        return dict(self.tag) if self.tag and self.tag["id"] == tag_id else None

    def get_initiative_items(self, tag_id):
        return [dict(i) for i in self.items]

    def update_initiative_brief(self, tag_id, **fields):
        self.updates.append({"tag_id": tag_id, **fields})

    def last_status(self):
        for u in reversed(self.updates):
            if "status" in u:
                return u
        return None


TEMPLATE = "[ROLE] analyst\n\n[ITEMS]"


@pytest.fixture
def fake_env(monkeypatch):
    """Stub the module's collaborators; returns (fakedb, calls dict)."""
    fakedb = FakeDB()
    calls = {}

    monkeypatch.setattr(ib, "db", fakedb)
    monkeypatch.setattr(
        ib, "load_prompt",
        lambda slug: TEMPLATE if slug == ib.PROMPT_SLUG else "",
    )
    monkeypatch.setattr(ib, "load_model_config", lambda: {})
    monkeypatch.setattr(ib, "make_client", lambda: SimpleNamespace())

    def fake_call_llm(client, model, prompt, max_tokens=0, label=""):
        calls["model"] = model
        calls["prompt"] = prompt
        calls["max_tokens"] = max_tokens
        return calls.get("reply", "## Key Takeaways\n\n- It moved.")

    monkeypatch.setattr(ib, "call_llm", fake_call_llm)
    return fakedb, calls


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def test_prompt_orders_items_oldest_first(fake_env):
    _, _ = fake_env
    prompt = ib.build_brief_prompt(TAG, _mk_items())
    first = prompt.index("2026-05-12")
    second = prompt.index("2026-06-10")
    third = prompt.index("2026-07-08")
    assert first < second < third
    assert "ITEM 1 of 3" in prompt and "ITEM 3 of 3" in prompt


def test_prompt_carries_context_and_strips_images(fake_env):
    prompt = ib.build_brief_prompt(TAG, _mk_items())
    # Header context: committee, agenda number, presenter, vote.
    assert "NPC meeting 2026-06-10" in prompt
    assert "agenda 5: CAR vote" in prompt
    assert "presented by J. Smith (ISO-NE)" in prompt
    assert "vote: Approved" in prompt
    # Tag context block.
    assert "Code: CAR-SA" in prompt
    assert "Known description: Capacity auction reform" in prompt
    # Image markup never reaches the model.
    assert "image_id" not in prompt
    assert "**Figure:**" not in prompt
    # Unsummarized (future) item is placeholdered, not dropped.
    assert "(No summary available for this item yet.)" in prompt
    # Template injection point respected.
    assert prompt.rstrip().startswith("[ROLE] analyst")


def test_prompt_truncates_long_summaries(fake_env):
    items = _mk_items()
    items[1]["summary_detailed"] = "line\n" * 3000  # ≫ _MAX_ITEM_CHARS
    prompt = ib.build_brief_prompt(TAG, items)
    assert "…(truncated)" in prompt
    assert len(prompt) < 40_000


def test_prompt_requires_template(fake_env, monkeypatch):
    monkeypatch.setattr(ib, "load_prompt", lambda slug: "")
    with pytest.raises(ValueError, match="not found"):
        ib.build_brief_prompt(TAG, _mk_items())


# ---------------------------------------------------------------------------
# Runner status transitions
# ---------------------------------------------------------------------------

def test_run_success_snapshots_sources(fake_env):
    fakedb, calls = fake_env
    assert ib.run_initiative_brief(7) is True
    final = fakedb.last_status()
    assert final["status"] == "complete"
    assert final["brief_md"].startswith("## Key Takeaways")
    assert final["source_item_count"] == 3
    assert final["source_latest_meeting_date"] == "2026-07-08"
    # Default model chain bottoms out at OPUS when config is empty.
    assert calls["model"] == ib.OPUS


def test_run_no_items_errors(fake_env):
    fakedb, _ = fake_env
    fakedb.items = []
    assert ib.run_initiative_brief(7) is False
    final = fakedb.last_status()
    assert final["status"] == "error"
    assert "No agenda items tagged CAR-SA" in final["error_message"]


def test_run_empty_llm_reply_errors(fake_env):
    fakedb, calls = fake_env
    calls["reply"] = "   "
    assert ib.run_initiative_brief(7) is False
    assert fakedb.last_status()["status"] == "error"


def test_run_unknown_tag_errors(fake_env):
    fakedb, _ = fake_env
    assert ib.run_initiative_brief(999) is False
    final = fakedb.last_status()
    assert final["status"] == "error"
    assert "tag not found" in final["error_message"].lower()


def test_run_model_config_chain(fake_env, monkeypatch):
    fakedb, calls = fake_env
    monkeypatch.setattr(ib, "load_model_config",
                        lambda: {"roundup_model": "model-r",
                                 "initiative_brief_model": "model-ib"})
    assert ib.run_initiative_brief(7) is True
    assert calls["model"] == "model-ib"

    monkeypatch.setattr(ib, "load_model_config",
                        lambda: {"roundup_model": "model-r"})
    assert ib.run_initiative_brief(7) is True
    assert calls["model"] == "model-r"


# ---------------------------------------------------------------------------
# Staleness rule
# ---------------------------------------------------------------------------

def _brief(**over):
    base = {"status": "complete", "source_item_count": 3,
            "source_latest_meeting_date": "2026-07-08"}
    base.update(over)
    return base


def test_stale_rules():
    # No brief / not complete → never stale.
    assert brief_is_stale(None, 5, "2026-07-08") is False
    assert brief_is_stale(_brief(status="generating"), 5, "2026-07-08") is False
    # Snapshot matches → fresh.
    assert brief_is_stale(_brief(), 3, "2026-07-08") is False
    # Item set moved → stale.
    assert brief_is_stale(_brief(), 4, "2026-07-08") is True
    # Newer meeting touched the thread → stale.
    assert brief_is_stale(_brief(), 3, "2026-08-01") is True
    # Older max date (item deleted) is caught by the count rule; date alone
    # never goes stale backwards.
    assert brief_is_stale(_brief(), 3, "2026-06-01") is False
