"""User-administration routes: serialization + guard rails.

Direct route-function calls against a FakeDB (house convention). Pins:
  * the list never exposes password_hash;
  * PATCH updates role / is_active;
  * self-demotion and self-deactivation are 400s;
  * anything that would leave zero active admins is a 409;
  * unknown user is 404, empty/bogus patches are 400s.
"""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import api.routes.admin_users as au

CALLER = {"id": 1, "email": "ben@example.com", "role": "admin", "is_active": True}


class FakeDB:
    def __init__(self, users):
        self.users = {u["id"]: dict(u) for u in users}

    def list_app_users(self):
        return [dict(u) for u in self.users.values()]

    def get_app_user(self, uid):
        u = self.users.get(uid)
        return dict(u) if u else None

    def update_app_user(self, uid, *, role=None, is_active=None):
        u = self.users[uid]
        if role is not None:
            u["role"] = role
        if is_active is not None:
            u["is_active"] = is_active
        return dict(u)

    def count_active_admins(self, exclude_user_id=None):
        return sum(
            1 for u in self.users.values()
            if u["role"] == "admin" and u["is_active"] and u["id"] != exclude_user_id
        )


def _u(uid, email, role, active=True):
    return {
        "id": uid, "email": email, "name": email.split("@")[0].title(),
        "role": role, "is_active": active, "auth_provider": "local",
        "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "last_login": None,
    }


@pytest.fixture
def two_admins(monkeypatch):
    db = FakeDB([_u(1, "ben@example.com", "admin"),
                 _u(2, "ana@example.com", "admin"),
                 _u(3, "vic@example.com", "viewer")])
    monkeypatch.setattr(au, "db", db)
    return db


@pytest.fixture
def one_admin(monkeypatch):
    db = FakeDB([_u(1, "ben@example.com", "editor"),
                 _u(2, "ana@example.com", "admin")])
    monkeypatch.setattr(au, "db", db)
    return db


def test_list_never_leaks_password_hash(two_admins):
    rows = au.list_users()
    assert len(rows) == 3
    for r in rows:
        assert "password_hash" not in r
    assert rows[0]["created_at"] == "2026-07-01T00:00:00+00:00"  # isoformatted


def test_patch_role_happy_path(two_admins):
    row = au.update_user(3, {"role": "editor"}, CALLER)
    assert row["role"] == "editor"
    assert two_admins.users[3]["role"] == "editor"


def test_patch_deactivate_happy_path(two_admins):
    row = au.update_user(3, {"is_active": False}, CALLER)
    assert row["is_active"] is False


def test_empty_patch_is_400(two_admins):
    with pytest.raises(HTTPException) as ei:
        au.update_user(3, {}, CALLER)
    assert ei.value.status_code == 400


def test_bogus_role_is_400(two_admins):
    with pytest.raises(HTTPException) as ei:
        au.update_user(3, {"role": "superuser"}, CALLER)
    assert ei.value.status_code == 400


def test_non_bool_is_active_is_400(two_admins):
    with pytest.raises(HTTPException) as ei:
        au.update_user(3, {"is_active": "no"}, CALLER)
    assert ei.value.status_code == 400


def test_unknown_user_is_404(two_admins):
    with pytest.raises(HTTPException) as ei:
        au.update_user(99, {"role": "viewer"}, CALLER)
    assert ei.value.status_code == 404


def test_self_demotion_is_400(two_admins):
    with pytest.raises(HTTPException) as ei:
        au.update_user(1, {"role": "viewer"}, CALLER)
    assert ei.value.status_code == 400
    assert two_admins.users[1]["role"] == "admin"  # unchanged


def test_self_deactivation_is_400(two_admins):
    with pytest.raises(HTTPException) as ei:
        au.update_user(1, {"is_active": False}, CALLER)
    assert ei.value.status_code == 400


def test_self_noop_role_is_allowed(two_admins):
    # Re-asserting your current role isn't a demotion — no reason to 400.
    row = au.update_user(1, {"role": "admin"}, CALLER)
    assert row["role"] == "admin"


def test_demoting_one_of_two_admins_is_fine(two_admins):
    row = au.update_user(2, {"role": "editor"}, CALLER)
    assert row["role"] == "editor"


def test_demoting_last_admin_is_409(one_admin):
    caller = {"id": 1, "email": "ben@example.com", "role": "editor"}
    with pytest.raises(HTTPException) as ei:
        au.update_user(2, {"role": "viewer"}, caller)
    assert ei.value.status_code == 409
    assert one_admin.users[2]["role"] == "admin"  # unchanged


def test_deactivating_last_admin_is_409(one_admin):
    caller = {"id": 1, "email": "ben@example.com", "role": "editor"}
    with pytest.raises(HTTPException) as ei:
        au.update_user(2, {"is_active": False}, caller)
    assert ei.value.status_code == 409


def test_reactivating_inactive_admin_is_fine(monkeypatch):
    db = FakeDB([_u(1, "ben@example.com", "admin"),
                 _u(2, "ana@example.com", "admin", active=False)])
    monkeypatch.setattr(au, "db", db)
    row = au.update_user(2, {"is_active": True}, CALLER)
    assert row["is_active"] is True
