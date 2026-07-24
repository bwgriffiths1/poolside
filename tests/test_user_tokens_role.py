"""Invite/reset tokens: role plumbing + email queueing.

Direct route-function calls with a fake db._conn/_cursor pair that captures
SQL params (house convention — user_tokens runs inline SQL). Pins:
  * invites validate + default the role and write it to the row;
  * accept creates the user with the token's role ('viewer' for pre-016
    tokens);
  * mail off → emailed=False and the copy-URL is still returned;
  * mail on → the invite email is queued with the chosen role.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import api.routes.user_tokens as ut

ADMIN = {"id": 1, "email": "ben@example.com", "role": "admin", "is_active": True}


class FakeCursor:
    """Captures executes; INSERT ... RETURNING gets a row rebuilt from the
    bound params so the route's serializer has something real to chew."""

    def __init__(self, state):
        self.state = state

    def execute(self, sql, params=None):
        self.state["queries"].append((" ".join(sql.split()), params))
        s = sql.upper()
        if "INSERT INTO USER_TOKENS" in s and "'INVITE'" in s:
            token, email, name, role, created_by, expires_at = params
            self.state["row"] = {
                "id": 5, "token": token, "purpose": "invite", "email": email,
                "name": name, "role": role, "created_by": created_by,
                "created_at": datetime(2026, 7, 23, tzinfo=timezone.utc),
                "expires_at": expires_at, "used_at": None,
            }
        elif "INSERT INTO USER_TOKENS" in s:
            token, email, name, created_by, expires_at = params
            self.state["row"] = {
                "id": 6, "token": token, "purpose": "password_reset",
                "email": email, "name": name, "role": "viewer",
                "created_by": created_by,
                "created_at": datetime(2026, 7, 23, tzinfo=timezone.utc),
                "expires_at": expires_at, "used_at": None,
            }
        elif s.startswith("SELECT * FROM USER_TOKENS"):
            self.state["row"] = self.state.get("select_row")

    def fetchone(self):
        return self.state.get("row")

    def fetchall(self):
        return []


@pytest.fixture
def fake(monkeypatch):
    state = {"queries": [], "row": None, "select_row": None}

    @contextmanager
    def _conn():
        yield "conn"

    @contextmanager
    def _cursor(conn):
        yield FakeCursor(state)

    monkeypatch.setattr(ut.db, "_conn", _conn)
    monkeypatch.setattr(ut.db, "_cursor", _cursor)
    monkeypatch.setattr(ut, "get_user_by_email", lambda email: None)
    monkeypatch.setattr(ut.mailer, "mail_enabled", lambda: False)
    monkeypatch.setattr(ut.mailer, "_base_url", lambda: "https://pool.test")
    return state


def test_invite_writes_chosen_role(fake):
    out = ut.create_invite(
        {"email": "New@X.com", "name": "New User", "role": "editor"}, ADMIN)
    insert = next(q for q in fake["queries"] if "INSERT" in q[0])
    assert "editor" in insert[1]
    assert out["role"] == "editor"
    assert out["emailed"] is False
    assert out["accept_url"] == f"https://pool.test/#/accept/{out['token']}"


def test_invite_role_defaults_to_viewer(fake):
    out = ut.create_invite({"email": "a@x.com", "name": "A"}, ADMIN)
    insert = next(q for q in fake["queries"] if "INSERT" in q[0])
    assert "viewer" in insert[1]
    assert out["role"] == "viewer"


def test_invite_rejects_unknown_role(fake):
    with pytest.raises(HTTPException) as ei:
        ut.create_invite(
            {"email": "a@x.com", "name": "A", "role": "superuser"}, ADMIN)
    assert ei.value.status_code == 400
    assert not any("INSERT" in q[0] for q in fake["queries"])


def test_invite_queues_email_when_mail_on(fake, monkeypatch):
    monkeypatch.setattr(ut.mailer, "mail_enabled", lambda: True)
    sent = []
    monkeypatch.setattr(
        ut, "_send_token_email",
        lambda kind, to, payload: sent.append((kind, to, payload)),
    )
    out = ut.create_invite(
        {"email": "a@x.com", "name": "A", "role": "admin"}, ADMIN)
    assert out["emailed"] is True
    assert sent and sent[0][0] == "invite" and sent[0][1] == "a@x.com"
    assert sent[0][2]["role"] == "admin"
    assert sent[0][2]["accept_url"].startswith("https://pool.test/#/accept/")


def test_reset_includes_accept_url_and_emailed_flag(fake, monkeypatch):
    monkeypatch.setattr(
        ut, "get_user_by_email",
        lambda email: {"id": 3, "email": email, "name": "Ana"},
    )
    out = ut.create_password_reset({"email": "ana@x.com"}, ADMIN)
    assert out["emailed"] is False
    assert "/#/accept/" in out["accept_url"]


def _valid_invite_row(role):
    return {
        "id": 5, "token": "tok", "purpose": "invite", "email": "a@x.com",
        "name": "A", "role": role, "used_at": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
    }


def test_accept_creates_user_with_token_role(fake, monkeypatch):
    fake["select_row"] = _valid_invite_row("editor")
    created = {}
    monkeypatch.setattr(
        ut, "create_user",
        lambda **kw: created.update(kw) or {"id": 10, **kw},
    )
    out = ut.public_token_accept("tok", {"password": "hunter22"})
    assert out["ok"] is True
    assert created["role"] == "editor"


def test_accept_pre016_token_defaults_to_viewer(fake, monkeypatch):
    row = _valid_invite_row(None)
    fake["select_row"] = row
    created = {}
    monkeypatch.setattr(
        ut, "create_user",
        lambda **kw: created.update(kw) or {"id": 10, **kw},
    )
    ut.public_token_accept("tok", {"password": "hunter22"})
    assert created["role"] == "viewer"


def test_preview_exposes_invite_role(fake):
    fake["select_row"] = _valid_invite_row("admin")
    out = ut.public_token_preview("tok")
    assert out["role"] == "admin"
