"""Tagline (one_line) edit-in-place: PATCH /api/summaries/{type}/{id}/one_line
updates the CURRENT version without minting a new one, validates input,
and 404s when there is no summary; the .docx cover carries the tagline."""
import pytest
from fastapi import HTTPException

import api.routes.summaries as sm
import pipeline.db as db


def test_set_summary_one_line_updates_current_version(monkeypatch):
    calls = []

    class _Cur:
        def __init__(self):
            self.row = {"id": 9, "version": 4, "status": "approved", "one_line": "New line"}
        def execute(self, sql, params):
            calls.append((sql.strip().split()[0], params))
        def fetchone(self):
            return self.row
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(db, "get_current_summary",
                        lambda et, eid: {"id": 9, "version": 4, "status": "approved"})
    monkeypatch.setattr(db, "_conn", lambda: _Conn())
    monkeypatch.setattr(db, "_cursor", lambda conn: _Cur())
    row = db.set_summary_one_line("meeting", 12, "  New line  ")
    assert row["one_line"] == "New line"
    assert calls[0][0] == "UPDATE" and calls[0][1] == ("New line", 9)
    # Blank clears the tagline (stored as NULL), never as an empty string.
    db.set_summary_one_line("meeting", 12, "   ")
    assert calls[-1][1] == (None, 9)


def test_set_summary_one_line_without_summary_is_none(monkeypatch):
    monkeypatch.setattr(db, "get_current_summary", lambda et, eid: None)
    assert db.set_summary_one_line("meeting", 12, "x") is None


def test_patch_one_line_route(monkeypatch):
    seen = {}

    def fake_set(et, eid, text):
        seen.update(et=et, eid=eid, text=text)
        return {"one_line": text, "version": 4, "status": "approved"}

    monkeypatch.setattr(sm.db, "set_summary_one_line", fake_set)
    out = sm.set_one_line("meeting", 12, {"one_line": "Tagline"}, {"email": "e@x", "role": "editor"})
    assert out == {"entity_type": "meeting", "entity_id": 12, "one_line": "Tagline",
                   "version": 4, "status": "approved"}
    assert seen == {"et": "meeting", "eid": 12, "text": "Tagline"}

    with pytest.raises(HTTPException) as exc:
        sm.set_one_line("meeting", 12, {"one_line": 42}, {})
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        sm.set_one_line("meeting", 12, {"one_line": "x" * 501}, {})
    assert exc.value.status_code == 400

    monkeypatch.setattr(sm.db, "set_summary_one_line", lambda *a: None)
    with pytest.raises(HTTPException) as exc:
        sm.set_one_line("meeting", 12, {"one_line": "Tagline"}, {})
    assert exc.value.status_code == 404
