"""Discovery-time reconciliation: a scraped calendar group must extend an
existing meeting (matched by any day-event ID, or by shared materials) and
only create a row when it is genuinely new."""
from datetime import date

import pytest

from api.services import discovery

COMMITTEE = {"name": "NEPOOL Markets Committee", "short": "MC"}

EV = {
    "primary_event_id": "160099",
    "all_event_ids": ["160099", "160100"],
    "title": "Markets Committee Meeting",
    "dates": [date(2026, 7, 8), date(2026, 7, 9)],
    "location": "Cape Cod",
}

DOCS = [{"filename": f"doc{i}.pdf",
         "url": f"https://iso-ne.com/static-assets/documents/doc{i}.pdf"}
        for i in range(10)]


@pytest.fixture
def calls(monkeypatch):
    """Stub every db/scraper touchpoint and record what discovery does."""
    record = {"extended": [], "created": [], "doc_fetches": 0}

    monkeypatch.setattr(discovery, "_resolve_meeting_type_id", lambda *a: 7)
    monkeypatch.setattr(
        discovery.db, "extend_meeting_span",
        lambda mid, s, e, add_event_ids=None: record["extended"].append(
            (mid, s, e, add_event_ids)) or {"id": mid},
    )
    monkeypatch.setattr(
        discovery, "_create_discovered_meeting",
        lambda **kw: record["created"].append(kw) or 999,
    )

    def fetch_docs(event_id):
        record["doc_fetches"] += 1
        return DOCS

    monkeypatch.setattr(discovery.pl_scraper, "fetch_event_docs", fetch_docs)
    return record


def test_known_event_id_extends_instead_of_creating(calls, monkeypatch):
    existing = {"id": 141, "meeting_date": date(2026, 7, 7)}
    monkeypatch.setattr(discovery.db, "find_meeting_by_event_ids",
                        lambda mt, ids: existing)

    created = discovery._reconcile_isone_event(COMMITTEE, EV)

    assert created is False
    assert calls["created"] == []
    assert calls["extended"] == [(141, "2026-07-08", "2026-07-09",
                                  ["160099", "160100"])]
    assert calls["doc_fetches"] == 0  # no HTTP when IDs already match


def test_materials_guard_catches_fresh_event_ids(calls, monkeypatch):
    # No stored ID matches (ISO re-posted the tail under new IDs), but a
    # same-committee meeting overlaps the span and holds the same materials.
    monkeypatch.setattr(discovery.db, "find_meeting_by_event_ids",
                        lambda mt, ids: None)
    monkeypatch.setattr(
        discovery.db, "find_overlapping_meetings",
        lambda mt, s, e, slack_days=0: [
            {"id": 141, "meeting_date": date(2026, 7, 7)}],
    )
    monkeypatch.setattr(discovery.db, "get_document_urls",
                        lambda mid: {d["url"] for d in DOCS})

    created = discovery._reconcile_isone_event(COMMITTEE, EV)

    assert created is False
    assert calls["created"] == []
    assert [e[0] for e in calls["extended"]] == [141]
    assert calls["doc_fetches"] == 1


def test_genuinely_new_meeting_is_created(calls, monkeypatch):
    monkeypatch.setattr(discovery.db, "find_meeting_by_event_ids",
                        lambda mt, ids: None)
    monkeypatch.setattr(discovery.db, "find_overlapping_meetings",
                        lambda mt, s, e, slack_days=0: [])

    created = discovery._reconcile_isone_event(COMMITTEE, EV)

    assert created is True
    assert calls["extended"] == []
    assert len(calls["created"]) == 1
    kw = calls["created"][0]
    assert kw["external_id"] == "160099"
    assert kw["external_ids"] == ["160099", "160100"]
    assert kw["meeting_date"] == date(2026, 7, 8)
    assert kw["end_date"] == date(2026, 7, 9)


def test_overlapping_but_different_materials_creates(calls, monkeypatch):
    # Same committee, overlapping day, disjoint materials — a real second
    # session, not a duplicate.
    monkeypatch.setattr(discovery.db, "find_meeting_by_event_ids",
                        lambda mt, ids: None)
    monkeypatch.setattr(
        discovery.db, "find_overlapping_meetings",
        lambda mt, s, e, slack_days=0: [
            {"id": 141, "meeting_date": date(2026, 7, 7)}],
    )
    monkeypatch.setattr(discovery.db, "get_document_urls",
                        lambda mid: {"https://iso-ne.com/other/unrelated.pdf"})

    created = discovery._reconcile_isone_event(COMMITTEE, EV)

    assert created is True
    assert calls["extended"] == []
