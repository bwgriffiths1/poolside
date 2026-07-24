"""Role-gate dependencies: allow/deny matrix.

Pins api.auth.require_admin / require_editor / require_editor_for_writes
against every role × method combination, and that the 403 details stay
distinct from the 401 "not authenticated" (the frontend keys on prefixes).
"""
import pytest
from fastapi import HTTPException

from api.auth import require_admin, require_editor, require_editor_for_writes


class Req:
    def __init__(self, method: str):
        self.method = method


def _user(role):
    u = {"id": 7, "email": f"{role or 'none'}@example.com", "is_active": True}
    if role is not None:
        u["role"] = role
    return u


# ── require_admin ───────────────────────────────────────────────────────

def test_require_admin_allows_admin():
    u = _user("admin")
    assert require_admin(u) is u


@pytest.mark.parametrize("role", ["editor", "viewer", None])
def test_require_admin_blocks_non_admin(role):
    with pytest.raises(HTTPException) as ei:
        require_admin(_user(role))
    assert ei.value.status_code == 403
    assert ei.value.detail == "Requires admin access"


# ── require_editor ──────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["admin", "editor"])
def test_require_editor_allows_editor_and_admin(role):
    u = _user(role)
    assert require_editor(u) is u


@pytest.mark.parametrize("role", ["viewer", None])
def test_require_editor_blocks_viewer(role):
    with pytest.raises(HTTPException) as ei:
        require_editor(_user(role))
    assert ei.value.status_code == 403
    assert ei.value.detail == "Requires editor access"


# ── require_editor_for_writes ───────────────────────────────────────────

@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
@pytest.mark.parametrize("role", ["admin", "editor", "viewer", None])
def test_writes_gate_opens_safe_methods_to_all(method, role):
    u = _user(role)
    assert require_editor_for_writes(Req(method), u) is u


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("role", ["admin", "editor"])
def test_writes_gate_allows_editor_writes(method, role):
    u = _user(role)
    assert require_editor_for_writes(Req(method), u) is u


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("role", ["viewer", None])
def test_writes_gate_blocks_viewer_writes(method, role):
    with pytest.raises(HTTPException) as ei:
        require_editor_for_writes(Req(method), _user(role))
    assert ei.value.status_code == 403
    assert ei.value.detail == "Requires editor access"


def test_403_detail_distinct_from_401():
    # main.tsx distinguishes 401 (session redirect) from 403 (permission
    # toast) by message prefix — the strings must never converge.
    with pytest.raises(HTTPException) as ei:
        require_admin(_user("viewer"))
    assert "not authenticated" not in ei.value.detail
