"""End-to-end role enforcement over HTTP — the one TestClient harness.

Everything else in tests/ calls route functions directly; this file proves
the mount wiring (router-level Depends) actually blocks/admits each role,
which direct calls can't exercise. TestClient without a context manager
never runs the lifespan, so no migrations / DB / scheduler are touched;
handlers that would hit Postgres get a stub db whose failures surface as
500 — anything except 401/403 proves the gate admitted the request.
"""
import pytest
from fastapi.testclient import TestClient

import api.main as m
from api.auth import current_user, make_session_cookie


class _ExplodingDB:
    """Any attribute is a callable that raises — handlers die AFTER the
    role gates, turning 'gate admitted it' into a 500 we can assert on."""

    def __getattr__(self, name):
        def _boom(*a, **k):
            raise RuntimeError(f"stub db hit: {name}")
        return _boom


@pytest.fixture
def client():
    c = TestClient(m.app, raise_server_exceptions=False)
    yield c
    m.app.dependency_overrides.clear()


def _as(role: str):
    m.app.dependency_overrides[current_user] = lambda: {
        "id": 42, "email": f"{role}@example.com", "name": role.title(),
        "role": role, "is_active": True,
    }


# ── Viewer ──────────────────────────────────────────────────────────────

def test_viewer_blocked_from_editor_write(client):
    _as("viewer")
    r = client.post("/api/roundups/generate", json={})
    assert r.status_code == 403
    assert r.json()["detail"] == "Requires editor access"


def test_viewer_blocked_from_admin_router(client):
    _as("viewer")
    assert client.put("/api/prompts/some-slug", json={}).status_code == 403
    assert client.get("/api/admin/usage").status_code == 403


def test_viewer_keeps_self_service_writes(client, monkeypatch):
    _as("viewer")
    import api.routes.watches as watches
    monkeypatch.setattr(watches, "db", _ExplodingDB())
    r = client.post("/api/watches/by-meeting/1")
    assert r.status_code not in (401, 403)  # gate admitted; stub db 500s

    import api.routes.me as me_route
    monkeypatch.setattr(
        me_route.db, "set_user_email_prefs",
        lambda uid, updates: dict(updates),
    )
    r = client.patch("/api/me/prefs", json={"email_prefs": {"weekly_digest": True}})
    assert r.status_code == 200


def test_viewer_keeps_ask(client):
    _as("viewer")
    # Empty body fails the handler's own validation — the point is it gets
    # past the gates (no 401/403), not that the ask succeeds.
    r = client.post("/api/ask", json={})
    assert r.status_code not in (401, 403)


def test_viewer_reads_stay_open(client, monkeypatch):
    _as("viewer")
    import api.routes.meetings as meetings
    monkeypatch.setattr(meetings.db, "list_meetings", lambda **k: [])
    r = client.get("/api/meetings")
    assert r.status_code not in (401, 403)


# ── Editor ──────────────────────────────────────────────────────────────

def test_editor_blocked_from_admin_surface(client):
    _as("editor")
    assert client.put("/api/prompts/some-slug", json={}).status_code == 403
    assert client.get("/api/admin/usage").status_code == 403
    assert client.post("/api/admin/discover").status_code == 403
    assert client.put("/api/admin/config", json={}).status_code == 403


def test_editor_admitted_to_content_writes(client, monkeypatch):
    _as("editor")
    import api.routes.agenda_items as ai
    monkeypatch.setattr(ai, "db", _ExplodingDB())
    r = client.delete("/api/agenda-items/1")
    assert r.status_code not in (401, 403)

    import api.routes.admin as admin_route
    monkeypatch.setattr(admin_route, "db", _ExplodingDB())
    r = client.post("/api/admin/refresh-materials/1")
    assert r.status_code not in (401, 403)  # editor workflow, not admin-only


# ── Admin ───────────────────────────────────────────────────────────────

def test_admin_admitted_everywhere(client, monkeypatch):
    _as("admin")
    import api.routes.admin_dashboard as dash
    monkeypatch.setattr(dash, "db", _ExplodingDB())
    assert client.get("/api/admin/usage").status_code not in (401, 403)

    import api.routes.admin_users as au
    monkeypatch.setattr(au, "db", _ExplodingDB())
    assert client.get("/api/admin/users").status_code not in (401, 403)


# ── Real cookie path (no overrides) ─────────────────────────────────────

def test_forged_cookie_viewer_gets_403_end_to_end(client, monkeypatch):
    """Covers current_user's new (request, cookie) signature over HTTP:
    a real signed cookie for an is_active viewer → editor write → 403."""
    import api.auth as auth_mod
    monkeypatch.setattr(
        auth_mod, "get_user_by_email",
        lambda email: {"id": 9, "email": email, "name": "V",
                       "role": "viewer", "is_active": True},
    )
    client.cookies.set("poolside_session", make_session_cookie("viewer@example.com"))
    r = client.post("/api/roundups/generate", json={})
    assert r.status_code == 403


def test_no_cookie_is_401_not_403(client):
    r = client.post("/api/roundups/generate", json={})
    assert r.status_code == 401
    assert r.json()["detail"] == "not authenticated"
