"""Session-cookie sign/verify round-trip — the app's front door."""
import time

from api.auth import make_session_cookie, verify_session_cookie


def test_round_trip():
    cookie = make_session_cookie("ben@bwgriffiths.com")
    assert verify_session_cookie(cookie) == "ben@bwgriffiths.com"


def test_tampered_email_rejected():
    cookie = make_session_cookie("ben@bwgriffiths.com")
    _, expiry, sig = cookie.split("|")
    assert verify_session_cookie(f"attacker@evil.com|{expiry}|{sig}") is None


def test_tampered_expiry_rejected():
    cookie = make_session_cookie("ben@bwgriffiths.com")
    email, expiry, sig = cookie.split("|")
    assert verify_session_cookie(f"{email}|{int(expiry) + 10_000}|{sig}") is None


def test_expired_cookie_rejected():
    email = "ben@bwgriffiths.com"
    past = int(time.time()) - 10
    from api.auth import _sign

    payload = f"{email}|{past}"
    assert verify_session_cookie(f"{payload}|{_sign(payload)}") is None


def test_malformed_cookies_rejected():
    for raw in ("", "abc", "a|b", "a|b|c|d", "ben@x.com|notanumber|deadbeef"):
        assert verify_session_cookie(raw) is None
