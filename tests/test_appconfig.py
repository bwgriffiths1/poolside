"""Override-precedence tests for pipeline/appconfig.py.

DB accessors are monkeypatched so these run identically with or without a
database (CI has none): what's under test is the merge/fallback logic that
every config/prompt reader in the app now goes through.
"""
import pipeline.appconfig as ac


def test_config_db_overrides_file_key(monkeypatch):
    monkeypatch.setattr(ac, "_db_get_all", lambda: {"lookahead_days": 99})
    cfg = ac.get_config()
    assert cfg["lookahead_days"] == 99          # DB wins
    assert cfg.get("committees"), "file keys must survive the merge"


def test_config_falls_back_to_file_when_db_down(monkeypatch):
    monkeypatch.setattr(ac, "_db_get_all", lambda: None)
    cfg = ac.get_config()
    assert cfg.get("lookahead_days"), "file config lost"
    assert "summarization" in cfg


def test_model_config_key_never_leaks_into_config(monkeypatch):
    monkeypatch.setattr(
        ac, "_db_get_all",
        lambda: {ac.MODEL_CONFIG_KEY: {"meeting_model": "x"}, "lookahead_days": 30},
    )
    cfg = ac.get_config()
    assert ac.MODEL_CONFIG_KEY not in cfg
    assert cfg["lookahead_days"] == 30


def test_prompt_override_wins_over_file(monkeypatch):
    monkeypatch.setattr(ac, "_db_get_prompt",
                        lambda slug: "OVERRIDE" if slug == "doc_summary_prompt" else None)
    assert ac.get_prompt("doc_summary_prompt") == "OVERRIDE"
    # A slug without an override falls through to the repo file.
    body = ac.get_prompt("isone_mc_briefing_prompt")
    assert "Key Takeaways" in body, "repo prompt file not read"


def test_missing_prompt_returns_empty(monkeypatch):
    monkeypatch.setattr(ac, "_db_get_prompt", lambda slug: None)
    assert ac.get_prompt("no_such_prompt_slug") == ""


def test_model_config_merges_db_over_file(monkeypatch):
    monkeypatch.setattr(ac, "_db_get", lambda key: {"meeting_model": "claude-test-9"})
    cfg = ac.get_model_config()
    assert cfg["meeting_model"] == "claude-test-9"      # DB override wins
    assert cfg.get("document_model"), "file keys must survive the merge"


def test_model_config_file_only_when_db_down(monkeypatch):
    monkeypatch.setattr(ac, "_db_get", lambda key: None)
    cfg = ac.get_model_config()
    assert cfg.get("meeting_model"), "file model config lost"
