"""PJM agenda backfill: candidate selection plumbing, per-meeting isolation,
and route admission.

Discovery back-creates PJM meetings for the whole current year, but the
refresh cron only walks [-3,+21] days — so retroactively-discovered meetings
have documents and no agenda. The backfill runs refresh_with_agenda over
exactly that set. No Postgres, no threads doing real work.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

import api.routes.pjm as pjm_routes
import api.services.discovery as discovery
from api.routes.pjm import BackfillBody

ADMIN = {"email": "ben@example.com", "role": "admin"}


def _candidates():
    return [
        {"id": 159, "meeting_date": date(2026, 1, 7), "end_date": None,
         "title": "MIC", "external_id": "pjm-mic-20260107",
         "type_short": "MIC", "doc_count": 29},
        {"id": 174, "meeting_date": date(2026, 1, 8), "end_date": None,
         "title": "OC", "external_id": "pjm-oc-20260108",
         "type_short": "OC", "doc_count": 38},
        {"id": 143, "meeting_date": date(2026, 1, 16), "end_date": None,
         "title": "MRC", "external_id": "pjm-mrc-20260116",
         "type_short": "MRC", "doc_count": 3},
    ]


# ── service loop ─────────────────────────────────────────────────────────


def test_backfill_isolates_per_meeting_failures(monkeypatch):
    monkeypatch.setattr(discovery, "_load_config", lambda: {})
    monkeypatch.setattr(
        discovery.db, "list_venue_meetings_missing_agendas",
        lambda venue, since: _candidates(),
    )

    def fake_refresh(mid, cfg):
        if mid == 159:
            return {"steps": [{"step": "parse_agenda", "parsed": True,
                               "n_items": 6, "reason": None}]}
        if mid == 174:
            return {"steps": [{"step": "parse_agenda", "parsed": False,
                               "n_items": 0,
                               "reason": "no document name matches agenda heuristic"}]}
        raise RuntimeError("network exploded")

    monkeypatch.setattr(discovery.orchestrator, "refresh_with_agenda",
                        fake_refresh)

    out = discovery.backfill_pjm_agendas(date(2026, 1, 1))
    assert out["candidates"] == 3
    assert out["parsed"] == 1
    by_id = {r["meeting_id"]: r for r in out["results"]}
    assert by_id[159]["parsed"] is True and by_id[159]["n_items"] == 6
    assert by_id[174]["parsed"] is False
    assert "heuristic" in by_id[174]["reason"]
    # The crash on 143 is recorded, not raised — later meetings still ran.
    assert "network exploded" in by_id[143]["error"]


# ── route admission ──────────────────────────────────────────────────────


def test_dry_run_lists_without_starting(monkeypatch):
    monkeypatch.setattr(
        pjm_routes.db, "list_venue_meetings_missing_agendas",
        lambda venue, since: _candidates(),
    )
    monkeypatch.setattr(pjm_routes, "_backfill_thread", None)
    out = pjm_routes.backfill_agendas(
        BackfillBody(since="2026-01-01", dry_run=True), ADMIN,
    )
    assert out["dry_run"] is True
    assert out["candidates"] == 3
    assert out["meetings"][0]["meeting_id"] == 159
    assert pjm_routes._backfill_thread is None  # nothing started


def test_default_since_is_jan_1(monkeypatch):
    seen = {}

    def capture(venue, since):
        seen["since"] = since
        return []

    monkeypatch.setattr(pjm_routes.db,
                        "list_venue_meetings_missing_agendas", capture)
    pjm_routes.backfill_agendas(BackfillBody(dry_run=True), ADMIN)
    assert seen["since"] == date(date.today().year, 1, 1)


def test_bad_since_400(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        pjm_routes.backfill_agendas(
            BackfillBody(since="January 1", dry_run=True), ADMIN,
        )
    assert exc.value.status_code == 400


def test_second_run_409_while_active(monkeypatch):
    monkeypatch.setattr(
        pjm_routes.db, "list_venue_meetings_missing_agendas",
        lambda venue, since: _candidates(),
    )

    class AliveThread:
        def is_alive(self):
            return True

    monkeypatch.setattr(pjm_routes, "_backfill_thread", AliveThread())
    with pytest.raises(HTTPException) as exc:
        pjm_routes.backfill_agendas(
            BackfillBody(since="2026-01-01", dry_run=False), ADMIN,
        )
    assert exc.value.status_code == 409


def test_apply_starts_thread_and_records_result(monkeypatch):
    monkeypatch.setattr(
        pjm_routes.db, "list_venue_meetings_missing_agendas",
        lambda venue, since: _candidates(),
    )
    ran = {}

    def fake_backfill(since):
        ran["since"] = since
        return {"since": since.isoformat(), "candidates": 3,
                "parsed": 3, "results": []}

    monkeypatch.setattr(pjm_routes.discovery, "backfill_pjm_agendas",
                        fake_backfill)
    monkeypatch.setattr(pjm_routes, "_backfill_thread", None)

    out = pjm_routes.backfill_agendas(
        BackfillBody(since="2026-01-01", dry_run=False), ADMIN,
    )
    assert out["started"] is True and out["candidates"] == 3
    pjm_routes._backfill_thread.join(timeout=5)
    assert ran["since"] == date(2026, 1, 1)

    status = pjm_routes.backfill_agendas_status(ADMIN)
    assert status["running"] is False
    assert status["last_result"]["parsed"] == 3
