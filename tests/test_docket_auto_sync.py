"""Scheduled docket check → unattended summarize + state-of-play regen.

Drives api.scheduler._docket_check_job and the docket_jobs on_finish hook
against fakes (no Postgres, no threads, no FERC, no LLM) and pins:

  * new filings that need summaries start ONE sync job (created_by
    scheduler) and raise no immediate notification;
  * the job's completion callback raises docket_synced with the summary
    count and whether the state of play regenerated;
  * a failed auto-sync falls back to docket_filings_new (+ job_failed);
  * auto_summarize=false, all-skip new filings, an active job, or losing
    the admission race keep the manual-path notification;
  * _run_docket_job invokes on_finish after the terminal status is written,
    and a callback exception never changes the job outcome.
"""
from __future__ import annotations

import pytest

import api.scheduler as sched
import api.services.docket_jobs as dj

DOCKET = {"id": 7, "docket_number": "ER26-925", "auto_refresh": True}


class Harness:
    """Monkeypatches every collaborator _docket_check_job imports lazily
    and records what it did."""

    def __init__(self, monkeypatch, *, new=3, pending=3, auto=True,
                 active=None, start_result=None):
        self.notifs: list[dict] = []
        self.started: list[dict] = []
        self.failed: list[str] = []
        self.on_finish = None

        import pipeline.db as db
        import pipeline.docket_ingest as di
        from api.services import notify

        monkeypatch.setattr(db, "list_dockets", lambda: [dict(DOCKET)])
        monkeypatch.setattr(di, "check_for_new_filings", lambda did: new)
        monkeypatch.setattr(di, "pending_summary_count", lambda did: pending)
        monkeypatch.setattr(di, "load_ferc_config",
                            lambda: {"auto_summarize": auto})
        monkeypatch.setattr(dj, "active_job_id", lambda did: active)

        def start(did, mode="sync", created_by="system", on_finish=None):
            self.started.append({"docket_id": did, "mode": mode,
                                 "created_by": created_by})
            self.on_finish = on_finish
            return start_result if start_result is not None else \
                {"job_id": 42, "already_running": False, "mode": mode}

        monkeypatch.setattr(dj, "start_docket_job", start)
        monkeypatch.setattr(
            notify, "create_notification",
            lambda kind, user_id, meeting_id=None, payload=None:
                self.notifs.append({"kind": kind, "payload": payload}) or 1)
        monkeypatch.setattr(
            sched, "_notify_job_failed",
            lambda job, exc: self.failed.append(f"{job}: {exc}"))

    def kinds(self):
        return [n["kind"] for n in self.notifs]


def test_new_filings_start_sync_job_and_defer_notification(monkeypatch):
    h = Harness(monkeypatch, new=3, pending=3)
    sched._docket_check_job()
    assert h.started == [{"docket_id": 7, "mode": "sync",
                          "created_by": "scheduler"}]
    assert h.notifs == []  # nothing until the job reports back
    assert callable(h.on_finish)


def test_completion_raises_docket_synced_with_brief_flag(monkeypatch):
    h = Harness(monkeypatch, new=2, pending=2)
    sched._docket_check_job()
    h.on_finish({"id": 42, "status": "complete", "filings_summarized": 2,
                 "cost_usd": 0.87, "error": None})
    assert h.kinds() == ["docket_synced"]
    p = h.notifs[0]["payload"]
    assert p["docket_id"] == 7 and p["docket_number"] == "ER26-925"
    assert p["count"] == 2 and p["summarized"] == 2
    assert p["brief_updated"] is True
    assert p["cost_usd"] == pytest.approx(0.87)
    assert h.failed == []


def test_completion_with_zero_summaries_says_brief_not_updated(monkeypatch):
    h = Harness(monkeypatch)
    sched._docket_check_job()
    h.on_finish({"id": 42, "status": "complete", "filings_summarized": 0,
                 "cost_usd": 0, "error": "ER26-925-000123: boom"})
    p = h.notifs[0]["payload"]
    assert p["brief_updated"] is False
    assert p["error"] == "ER26-925-000123: boom"


def test_failed_auto_sync_falls_back_to_manual_notification(monkeypatch):
    h = Harness(monkeypatch, new=1, pending=1)
    sched._docket_check_job()
    h.on_finish({"id": 42, "status": "failed", "filings_summarized": 0,
                 "error": "FERC 403"})
    assert h.kinds() == ["docket_filings_new"]
    assert h.notifs[0]["payload"]["count"] == 1
    assert h.failed and "docket_auto_sync" in h.failed[0]
    assert "FERC 403" in h.failed[0]


def test_cancelled_auto_sync_notifies_manually_without_job_failed(monkeypatch):
    h = Harness(monkeypatch)
    sched._docket_check_job()
    h.on_finish({"id": 42, "status": "cancelled", "filings_summarized": 0})
    assert h.kinds() == ["docket_filings_new"]
    assert h.failed == []


def test_auto_off_keeps_manual_path(monkeypatch):
    h = Harness(monkeypatch, new=3, pending=3, auto=False)
    sched._docket_check_job()
    assert h.started == []
    assert h.kinds() == ["docket_filings_new"]
    assert h.notifs[0]["payload"]["count"] == 3


def test_all_skip_new_filings_do_not_spend(monkeypatch):
    # e.g. three Notices landed: stored, but nothing to summarize → no job.
    h = Harness(monkeypatch, new=3, pending=0)
    sched._docket_check_job()
    assert h.started == []
    assert h.kinds() == ["docket_filings_new"]


def test_nothing_new_is_silent(monkeypatch):
    h = Harness(monkeypatch, new=0, pending=5)
    sched._docket_check_job()
    assert h.started == [] and h.notifs == []


def test_active_job_skips_docket_entirely(monkeypatch):
    calls = []
    h = Harness(monkeypatch, new=3, pending=3, active=99)
    import pipeline.docket_ingest as di
    monkeypatch.setattr(di, "check_for_new_filings",
                        lambda did: calls.append(did) or 3)
    sched._docket_check_job()
    assert calls == [] and h.started == [] and h.notifs == []


def test_lost_admission_race_falls_back_to_manual(monkeypatch):
    h = Harness(monkeypatch, start_result={"job_id": 5, "already_running": True})
    sched._docket_check_job()
    assert len(h.started) == 1
    assert h.kinds() == ["docket_filings_new"]


def test_check_exception_is_contained(monkeypatch):
    h = Harness(monkeypatch)
    import pipeline.docket_ingest as di

    def boom(did):
        raise RuntimeError("eLibrary down")

    monkeypatch.setattr(di, "check_for_new_filings", boom)
    sched._docket_check_job()  # must not raise
    assert h.started == [] and h.notifs == []
    assert h.failed and "docket_check" in h.failed[0]


# ── docket_jobs.on_finish plumbing ──────────────────────────────────────

class _FakeJobStore:
    def __init__(self):
        self.row = {"id": 1, "status": "queued", "filings_found": 0,
                    "filings_summarized": 0, "cost_usd": 0.0, "error": None}
        self.seen_in_callback = None


def _wire_job(monkeypatch, store, sync_result=None, sync_exc=None):
    import pipeline.docket_brief as dbf
    import pipeline.docket_ingest as di

    monkeypatch.setattr(dj, "_update_job",
                        lambda job_id, **f: store.row.update(f))
    monkeypatch.setattr(dj, "_job_status", lambda job_id: store.row["status"])
    monkeypatch.setattr(dj, "get_job", lambda job_id: dict(store.row))

    def fake_sync(docket_id, progress=None, **kw):
        if sync_exc:
            raise sync_exc
        return sync_result or {"filings_found": 2, "filings_summarized": 2,
                               "errors": []}

    monkeypatch.setattr(di, "sync_docket", fake_sync)
    monkeypatch.setattr(dbf, "run_docket_brief",
                        lambda did, progress=None, **kw:
                            {"input_tokens": 10, "output_tokens": 5,
                             "cost_usd": 0.01})


def test_run_docket_job_calls_on_finish_with_terminal_row(monkeypatch):
    store = _FakeJobStore()
    _wire_job(monkeypatch, store)

    def on_finish(row):
        store.seen_in_callback = dict(row)

    dj._run_docket_job(1, 7, "sync", on_finish)
    assert store.seen_in_callback["status"] == "complete"
    assert store.seen_in_callback["filings_summarized"] == 2
    assert store.seen_in_callback["cost_usd"] == pytest.approx(0.01)


def test_run_docket_job_on_finish_sees_failure(monkeypatch):
    store = _FakeJobStore()
    _wire_job(monkeypatch, store, sync_exc=RuntimeError("FERC 403"))
    seen = []
    dj._run_docket_job(1, 7, "sync", lambda row: seen.append(row))
    assert seen[0]["status"] == "failed"
    assert "FERC 403" in seen[0]["error"]


def test_run_docket_job_callback_error_does_not_change_outcome(monkeypatch):
    store = _FakeJobStore()
    _wire_job(monkeypatch, store)

    def bad(row):
        raise RuntimeError("notify down")

    dj._run_docket_job(1, 7, "sync", bad)  # must not raise
    assert store.row["status"] == "complete"


def test_run_docket_job_without_callback_unchanged(monkeypatch):
    store = _FakeJobStore()
    _wire_job(monkeypatch, store)
    dj._run_docket_job(1, 7, "sync")
    assert store.row["status"] == "complete"


def test_start_docket_job_threads_on_finish(monkeypatch):
    """The callback reaches the thread target only for a job we start."""
    import pipeline.db as db

    captured = {}

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): self.params = params
        def fetchone(self): return {"id": 11}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(db, "get_docket", lambda did: {"id": did})
    monkeypatch.setattr(db, "_conn", lambda: Conn())
    monkeypatch.setattr(db, "_cursor", lambda conn: Cur())
    monkeypatch.setattr(dj, "active_job_id", lambda did: None)

    class FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            captured["args"] = args
        def start(self): pass

    monkeypatch.setattr(dj.threading, "Thread", FakeThread)
    cb = lambda row: None  # noqa: E731
    res = dj.start_docket_job(7, mode="sync", created_by="scheduler", on_finish=cb)
    assert res == {"job_id": 11, "already_running": False, "mode": "sync"}
    assert captured["args"] == (11, 7, "sync", cb)
