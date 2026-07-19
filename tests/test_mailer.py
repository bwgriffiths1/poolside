"""Email layer: disabled-by-default safety, template content, digest job
gating, and the prefs endpoint whitelist.

No network anywhere: requests.post is stubbed where a send is expected and
the disabled path is pinned to never call it at all.
"""
import pytest

import api.routes.me as me_mod
import api.scheduler as sched
import api.services.mailer as mailer
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# send_email gating
# ---------------------------------------------------------------------------

def test_send_email_noop_without_config(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("POOLSIDE_EMAIL_FROM", raising=False)

    def boom(*a, **k):  # pragma: no cover — the assertion
        raise AssertionError("network must not be touched when mail is disabled")

    monkeypatch.setattr(mailer.requests, "post", boom)
    assert mailer.mail_enabled() is False
    assert mailer.send_email("a@b.c", "s", "<p>x</p>") is False


def test_send_email_posts_when_configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("POOLSIDE_EMAIL_FROM", "Poolside <mail@example.com>")
    sent = {}

    class Resp:
        status_code = 200
        text = "ok"

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update({"url": url, "json": json,
                     "auth": headers.get("Authorization")})
        return Resp()

    monkeypatch.setattr(mailer.requests, "post", fake_post)
    assert mailer.send_email("ben@example.com", "Subject", "<p>hi</p>") is True
    assert sent["url"].endswith("/emails")
    assert sent["auth"] == "Bearer re_test"
    assert sent["json"]["to"] == ["ben@example.com"]
    assert sent["json"]["from"].endswith("<mail@example.com>")


def test_send_email_false_on_rejection(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("POOLSIDE_EMAIL_FROM", "m@example.com")

    class Resp:
        status_code = 422
        text = "bad payload"

    monkeypatch.setattr(mailer.requests, "post", lambda *a, **k: Resp())
    assert mailer.send_email("a@b.c", "s", "x") is False


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def test_briefing_email_content_and_escaping():
    subject, body = mailer.briefing_approved_email({
        "meeting_id": 12,
        "title": "Joint <MC> & RC",
        "committee": "MC",
        "meeting_date": "2026-05-12",
        "approved_by": "ben@example.com",
    })
    assert subject == "Briefing ready: MC 2026-05-12"
    assert "#/briefing/12" in body
    # HTML-escaped title; raw angle brackets must not survive.
    assert "Joint &lt;MC&gt; &amp; RC" in body
    assert "<MC>" not in body
    assert "approved by ben@example.com" in body


def test_digest_email_sections():
    upcoming = [{"meeting_date": "2026-07-21", "type_short": "MC",
                 "title": "Markets Committee"}]
    recent = [{"id": 9, "meeting_date": "2026-07-15", "type_short": "RC",
               "title": "Reliability Committee"}]
    subject, body = mailer.weekly_digest_email(upcoming, recent)
    assert "week ahead" in subject.lower()
    assert "This week's meetings" in body
    assert "Markets Committee" in body
    assert "New briefings from last week" in body
    assert "#/briefing/9" in body


# ---------------------------------------------------------------------------
# Weekly digest job gating
# ---------------------------------------------------------------------------

class _DigestDB:
    def __init__(self, users):
        self.users = users

    def list_users_with_email_pref(self, key):
        assert key == "weekly_digest"
        return self.users

    def list_meetings_overview(self, past_days=0, future_days=7):
        return [{"meeting_date": "2026-07-21", "type_short": "MC", "title": "MC"}]

    def list_recent_approved_briefings(self, days=7):
        return []


def test_digest_job_skips_without_optins(monkeypatch):
    sends = []
    monkeypatch.setattr(mailer, "mail_enabled", lambda: True)
    monkeypatch.setattr(mailer, "send_email",
                        lambda to, s, h: sends.append(to) or True)
    import pipeline.db as real_db
    monkeypatch.setattr(real_db, "list_users_with_email_pref",
                        lambda key: [])
    sched._weekly_digest_job()
    assert sends == []


def test_digest_job_sends_to_optins(monkeypatch):
    sends = []
    fake = _DigestDB(users=[{"id": 1, "email": "ben@example.com", "name": "Ben"}])
    import pipeline.db as real_db
    monkeypatch.setattr(real_db, "list_users_with_email_pref",
                        fake.list_users_with_email_pref)
    monkeypatch.setattr(real_db, "list_meetings_overview",
                        fake.list_meetings_overview)
    monkeypatch.setattr(real_db, "list_recent_approved_briefings",
                        fake.list_recent_approved_briefings)
    monkeypatch.setattr(mailer, "mail_enabled", lambda: True)
    monkeypatch.setattr(mailer, "send_email",
                        lambda to, s, h: sends.append((to, s)) or True)
    sched._weekly_digest_job()
    assert sends == [("ben@example.com", "Poolside — your week ahead")]


# ---------------------------------------------------------------------------
# Prefs endpoint whitelist
# ---------------------------------------------------------------------------

def test_prefs_patch_whitelists_keys(monkeypatch):
    stored = {}

    monkeypatch.setattr(
        me_mod.db, "set_user_email_prefs",
        lambda uid, prefs: stored.update(prefs) or dict(stored),
    )
    out = me_mod.update_my_prefs(
        {"email_prefs": {"weekly_digest": True, "evil_key": True,
                         "briefing_ready": 0}},
        {"id": 1},
    )
    assert stored == {"weekly_digest": True, "briefing_ready": False}
    assert out["email_prefs"] == {"briefing_ready": False, "weekly_digest": True}
    assert "evil_key" not in out["email_prefs"]


def test_prefs_patch_rejects_empty(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        me_mod.update_my_prefs({"email_prefs": {"junk": True}}, {"id": 1})
    assert exc.value.status_code == 400
