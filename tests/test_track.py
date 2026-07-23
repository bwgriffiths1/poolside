"""Read-analytics beacon + readership serialization.

Direct route-function calls with monkeypatched db (house convention).
The SQL dedupe window itself is exercised against the live dev DB in the
PR's verification recipe — here we pin validation, the db call shape, and
the title-enrichment fallback for deleted entities.
"""
import pytest
from fastapi import HTTPException

import api.routes.admin_activity as aa
import api.routes.track as tr

USER = {"id": 5, "email": "viewer@example.com", "role": "viewer", "is_active": True}


def test_track_records_a_view(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tr.db, "record_page_view",
        lambda **kw: calls.append(kw) or True,
    )
    resp = tr.track_view({"entity_type": "briefing", "entity_id": 42}, USER)
    assert resp.status_code == 204
    assert calls == [{
        "user_id": 5, "user_email": "viewer@example.com",
        "entity_type": "briefing", "entity_id": 42,
    }]


def test_track_coerces_string_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(tr.db, "record_page_view", lambda **kw: calls.append(kw))
    tr.track_view({"entity_type": "docket", "entity_id": "7"}, USER)
    assert calls[0]["entity_id"] == 7


@pytest.mark.parametrize("body", [
    {"entity_type": "prompt", "entity_id": 1},   # not a tracked type
    {"entity_type": "", "entity_id": 1},
    {"entity_id": 1},
    {"entity_type": "meeting", "entity_id": "abc"},
    {"entity_type": "meeting"},
])
def test_track_rejects_bad_payloads(monkeypatch, body):
    called = []
    monkeypatch.setattr(tr.db, "record_page_view", lambda **kw: called.append(kw))
    with pytest.raises(HTTPException) as ei:
        tr.track_view(body, USER)
    assert ei.value.status_code == 400
    assert called == []


def test_views_summary_enriches_titles_and_flags_deleted(monkeypatch):
    from datetime import datetime, timezone
    rows = [
        {"entity_type": "briefing", "entity_id": 1, "views": 3,
         "unique_viewers": 2,
         "last_viewed_at": datetime(2026, 7, 23, tzinfo=timezone.utc)},
        {"entity_type": "docket", "entity_id": 99, "views": 1,
         "unique_viewers": 1,
         "last_viewed_at": datetime(2026, 7, 22, tzinfo=timezone.utc)},
    ]
    monkeypatch.setattr(aa.db, "page_view_summary", lambda days: rows)
    monkeypatch.setattr(
        aa.db, "entity_titles",
        lambda t, ids: {1: "MC · Jul 2026"} if t == "briefing" else {},
    )
    out = aa.views_summary(days=30)
    assert out[0]["title"] == "MC · Jul 2026"
    assert out[0]["last_viewed_at"] == "2026-07-23T00:00:00+00:00"
    # Docket 99 was deleted — the log outlives it.
    assert out[1]["title"] == "deleted docket #99"


def test_views_recent_batches_lookups_per_type(monkeypatch):
    from datetime import datetime, timezone
    rows = [
        {"user_email": "a@x.com", "entity_type": "meeting", "entity_id": 1,
         "viewed_at": datetime(2026, 7, 23, tzinfo=timezone.utc)},
        {"user_email": "b@x.com", "entity_type": "meeting", "entity_id": 2,
         "viewed_at": datetime(2026, 7, 23, tzinfo=timezone.utc)},
        {"user_email": "a@x.com", "entity_type": "meeting", "entity_id": 1,
         "viewed_at": datetime(2026, 7, 22, tzinfo=timezone.utc)},
    ]
    lookups = []
    monkeypatch.setattr(aa.db, "recent_page_views", lambda limit: rows)
    monkeypatch.setattr(
        aa.db, "entity_titles",
        lambda t, ids: lookups.append((t, ids)) or {1: "RC Jul 23", 2: "PAC Jul 28"},
    )
    out = aa.views_recent(limit=50)
    # One batched lookup for the whole type, deduped ids.
    assert lookups == [("meeting", [1, 2])]
    assert [r["title"] for r in out] == ["RC Jul 23", "PAC Jul 28", "RC Jul 23"]


def test_days_and_limit_are_clamped(monkeypatch):
    seen = {}
    monkeypatch.setattr(aa.db, "page_view_summary",
                        lambda days: seen.setdefault("days", days) and [])
    monkeypatch.setattr(aa.db, "recent_page_views",
                        lambda limit: seen.setdefault("limit", limit) and [])
    aa.views_summary(days=10_000)
    aa.views_recent(limit=10_000)
    assert seen == {"days": 365, "limit": 200}
