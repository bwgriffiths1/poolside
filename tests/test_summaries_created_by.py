"""Manual summary saves record the real editor, not the literal "user".

(The pre-roles code hardcoded created_by="user", which made manual edits
unattributable — pinned here so it can't regress.)
"""
import api.routes.summaries as sr

USER = {"id": 4, "email": "ana@example.com", "role": "editor", "is_active": True}


def test_save_summary_attributes_the_caller(monkeypatch):
    captured = {}

    def fake_save(**kw):
        captured.update(kw)
        return {"id": 1, "version": 2, "status": "approved"}

    monkeypatch.setattr(sr.db, "get_meeting", lambda mid: {"id": mid})
    monkeypatch.setattr(sr.db, "save_manual_summary", fake_save)
    monkeypatch.setattr(sr.lifecycle, "bump_lifecycle", lambda mid: "summarized")

    out = sr.save_summary("meeting", 7, {"detailed": "Edited text."}, USER)

    assert out["is_manual"] is True
    assert captured["created_by"] == "ana@example.com"


def test_save_summary_falls_back_when_email_missing(monkeypatch):
    captured = {}
    monkeypatch.setattr(sr.db, "get_docket", lambda did: {"id": did})
    monkeypatch.setattr(
        sr.db, "save_manual_summary",
        lambda **kw: captured.update(kw) or {"id": 1, "version": 1, "status": "approved"},
    )
    sr.save_summary("docket", 3, {"detailed": "x"}, {"id": 9})
    assert captured["created_by"] == "user"
