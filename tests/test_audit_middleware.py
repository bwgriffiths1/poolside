"""Audit middleware: what gets logged, what never does.

TestClient (no lifespan) with current_user overridden per test and
pipeline.db.record_audit monkeypatched to capture. Pins:
  * non-GET actions log user, route template, params, status;
  * GETs, /api/auth/*, /api/track/*, /api/public/* never log;
  * 401s (no request.state.user) never log; role-denied 403s DO;
  * a db failure inside capture never breaks the response;
  * request.state set during dependency resolution is visible to the
    middleware (the Starlette scope-sharing this design leans on).
"""
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import api.main as m
import pipeline.db as pdb
from api.auth import current_user, make_session_cookie


@pytest.fixture
def captured(monkeypatch):
    rows = []
    monkeypatch.setattr(pdb, "record_audit", lambda entry: rows.append(entry))
    return rows


@pytest.fixture
def client():
    c = TestClient(m.app, raise_server_exceptions=False)
    yield c
    m.app.dependency_overrides.clear()


def _as(role: str):
    user = {
        "id": 3, "email": f"{role}@example.com", "name": role.title(),
        "role": role, "is_active": True,
    }

    # Mirrors the real dependency: the middleware reads request.state.user,
    # so the override must stash it the same way current_user does.
    def _override(request: Request) -> dict:
        request.state.user = user
        return user

    m.app.dependency_overrides[current_user] = _override


def test_editor_write_is_logged_with_route_template(captured, client, monkeypatch):
    _as("editor")
    import api.routes.agenda_items as ai

    class _DB:
        def __getattr__(self, name):
            def _f(*a, **k):
                raise RuntimeError("stub")
            return _f

    monkeypatch.setattr(ai, "db", _DB())
    client.delete("/api/agenda-items/42")

    assert len(captured) == 1
    row = captured[0]
    assert row["user_email"] == "editor@example.com"
    assert row["method"] == "DELETE"
    assert row["path"] == "/api/agenda-items/42"
    assert row["route"] == "/api/agenda-items/{row_id}"
    # Scope path params are pre-validation raw strings — stored as-is.
    assert row["path_params"] == {"row_id": "42"}
    assert row["status"] == 500  # stub db blew up — still audited
    assert isinstance(row["duration_ms"], int)


def test_denied_403_is_logged_with_the_user(captured, client):
    _as("viewer")
    r = client.post("/api/roundups/generate", json={})
    assert r.status_code == 403
    assert len(captured) == 1
    assert captured[0]["status"] == 403
    assert captured[0]["user_email"] == "viewer@example.com"


def test_gets_and_excluded_prefixes_never_log(captured, client, monkeypatch):
    _as("admin")
    import api.routes.meetings as meetings
    monkeypatch.setattr(meetings.db, "list_meetings", lambda **k: [])
    client.get("/api/meetings")

    # /api/auth/* excluded even though it's a POST.
    client.post("/api/auth/logout")
    assert captured == []


def test_unauthenticated_401_never_logs(captured, client):
    r = client.post("/api/roundups/generate", json={})
    assert r.status_code == 401
    assert captured == []


def test_db_failure_never_breaks_the_response(client, monkeypatch):
    _as("viewer")

    def _boom(entry):
        raise RuntimeError("audit db down")

    monkeypatch.setattr(pdb, "record_audit", _boom)
    r = client.post("/api/roundups/generate", json={})
    assert r.status_code == 403  # action outcome unchanged


def test_real_cookie_path_sets_state_for_the_middleware(captured, client, monkeypatch):
    """No overrides: the REAL current_user runs off a signed cookie and its
    request.state stash must reach the middleware after call_next — the
    Starlette scope-sharing behavior the whole design leans on."""
    import api.auth as auth_mod
    monkeypatch.setattr(
        auth_mod, "get_user_by_email",
        lambda email: {"id": 8, "email": email, "name": "V",
                       "role": "viewer", "is_active": True},
    )
    client.cookies.set("poolside_session", make_session_cookie("viewer@example.com"))
    r = client.post("/api/roundups/generate", json={})
    assert r.status_code == 403
    assert len(captured) == 1
    assert captured[0]["user_email"] == "viewer@example.com"


def test_labels_cover_the_hot_actions():
    """Label map spot-check + shape: keys are (METHOD, route) tuples that
    look like real routes, and the fallback renders unmapped rows."""
    from api.routes.admin_activity import _LABELS, _label

    assert _LABELS[("POST", "/api/meetings/{meeting_id}/briefing/approve")] == "Approved briefing"
    assert _LABELS[("PUT", "/api/summaries/{entity_type}/{entity_id}")] == "Edited summary"
    for (method, route) in _LABELS:
        assert method in {"POST", "PUT", "PATCH", "DELETE"}
        assert route.startswith("/api/")
    assert _label({"method": "POST", "route": "/api/new-thing"}) == "POST /api/new-thing"


def test_label_map_routes_exist():
    """Every mapped route template must correspond to a registered route —
    catches drift when an endpoint is renamed."""
    from fastapi.routing import APIRoute
    from api.routes.admin_activity import _LABELS

    real = {(meth, r.path) for r in m.app.routes if isinstance(r, APIRoute)
            for meth in r.methods}
    for key in _LABELS:
        assert key in real, f"label map entry {key} has no matching route"
