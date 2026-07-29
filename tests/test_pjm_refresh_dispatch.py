"""pipeline/refresh.py venue registry — dispatch contract.

The old hard gate ("if venue != ISO-NE: error") became VENUE_DOC_FETCHERS.
Pin: PJM is registered and refreshes without the skip error, unregistered
venues get the exact legacy error string (the 30-min cron surfaces it),
and the ISO-NE wrapper still calls fetch_event_docs byte-identically.
"""
from datetime import date

import pipeline.refresh as refresh
from pipeline.pjm_scraper import fetch_docs_for_meeting


def _meeting(venue: str, external_id: str = "123") -> dict:
    return {
        "id": 1,
        "venue_short": venue,
        "type_short": "MC",
        "external_id": external_id,
        "meeting_date": date(2026, 4, 16),
    }


def _patch_db(monkeypatch, meeting: dict) -> None:
    monkeypatch.setattr(refresh.db, "get_meeting", lambda mid: meeting)
    monkeypatch.setattr(refresh.db, "get_existing_filenames", lambda mid: set())


def test_pjm_is_registered():
    assert refresh.VENUE_DOC_FETCHERS["PJM"] is fetch_docs_for_meeting
    assert "ISO-NE" in refresh.VENUE_DOC_FETCHERS


def test_unregistered_venue_gets_legacy_skip_error(monkeypatch):
    _patch_db(monkeypatch, _meeting("NYISO"))
    result = refresh.refresh_meeting_documents(1, config={})
    assert result.errors == ["Venue NYISO has no active scraper — refresh skipped"]
    assert not result.new_docs


def test_pjm_meeting_refreshes_without_skip_error(monkeypatch):
    _patch_db(monkeypatch, _meeting("PJM", external_id="pjm-cifp-rbp-20260416"))
    calls = {}

    def stub_fetcher(meeting, config, session):
        calls["meeting"] = meeting
        calls["config"] = config
        return []

    monkeypatch.setitem(refresh.VENUE_DOC_FETCHERS, "PJM", stub_fetcher)
    result = refresh.refresh_meeting_documents(1, config={"pjm": {}})
    assert result.errors == []
    assert calls["meeting"]["external_id"] == "pjm-cifp-rbp-20260416"


def test_isone_wrapper_calls_fetch_event_docs_byte_identically(monkeypatch):
    _patch_db(monkeypatch, _meeting("ISO-NE", external_id=987654))
    seen = {}

    def stub_fetch(event_id, session=None):
        seen["event_id"] = event_id
        seen["session"] = session
        return []

    monkeypatch.setattr(refresh, "fetch_event_docs", stub_fetch)
    result = refresh.refresh_meeting_documents(1, config={})
    assert result.errors == []
    # the legacy call was fetch_event_docs(str(external_id), session=session)
    assert seen["event_id"] == "987654"
    assert seen["session"] is None
