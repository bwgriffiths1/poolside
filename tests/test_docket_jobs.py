"""_run_docket_job orchestration: the sync-then-brief contract.

Fakes the docket_jobs row helpers and the pipeline entry points (no
Postgres, no threads, no FERC, no LLM) and pins:

  * brief mode crawls eLibrary BEFORE regenerating, and regenerates even
    when the crawl found nothing new;
  * brief mode still completes (error recorded) when the crawl raises —
    a FERC outage must not block an explicit regen;
  * sync mode is unchanged: crawl failure stays fatal, and the brief only
    chains when new summaries landed;
  * cancellation during the crawl cancels the job without regenerating.
"""
import pytest

import api.services.docket_jobs as dj
import pipeline.docket_brief as docket_brief
import pipeline.docket_ingest as docket_ingest

BRIEF_TOTALS = {"input_tokens": 11, "output_tokens": 7, "cost_usd": 0.05}


class Rig:
    """Recorded _update_job writes + call order of the pipeline fakes."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.calls: list[str] = []


@pytest.fixture
def rig(monkeypatch):
    r = Rig()

    def fake_update(job_id, **fields):
        r.rows.setdefault(job_id, {}).update(fields)

    monkeypatch.setattr(dj, "_update_job", fake_update)
    monkeypatch.setattr(dj, "_job_status", lambda job_id: "running")

    # _run_docket_job imports these from the pipeline modules at call time,
    # so patching the module attributes is enough.
    r.set_sync = lambda fn: monkeypatch.setattr(docket_ingest, "sync_docket", fn)
    r.set_brief = lambda fn: monkeypatch.setattr(
        docket_brief, "run_docket_brief", fn)
    return r


def _sync_returning(rig, found=0, summarized=0):
    def sync(docket_id, progress=None):
        rig.calls.append("sync")
        return {"filings_found": found, "filings_summarized": summarized,
                "errors": []}
    return sync


def _brief(rig):
    def brief(docket_id, progress=None):
        rig.calls.append("brief")
        return dict(BRIEF_TOTALS)
    return brief


def test_brief_crawls_first_and_regenerates_even_with_nothing_new(rig):
    rig.set_sync(_sync_returning(rig, found=0, summarized=0))
    rig.set_brief(_brief(rig))

    dj._run_docket_job(1, 42, "brief")

    assert rig.calls == ["sync", "brief"]
    row = rig.rows[1]
    assert row["status"] == "complete"
    assert row["filings_found"] == 0
    assert row["filings_summarized"] == 0
    assert row["error"] is None
    assert row["cost_usd"] == pytest.approx(BRIEF_TOTALS["cost_usd"])


def test_brief_records_crawl_pickups_in_the_job_row(rig):
    rig.set_sync(_sync_returning(rig, found=3, summarized=2))
    rig.set_brief(_brief(rig))

    dj._run_docket_job(1, 42, "brief")

    row = rig.rows[1]
    assert row["status"] == "complete"
    assert row["filings_found"] == 3
    assert row["filings_summarized"] == 2


def test_brief_survives_a_crawl_failure(rig):
    def sync(docket_id, progress=None):
        rig.calls.append("sync")
        raise RuntimeError("FERC 520 streak")

    rig.set_sync(sync)
    rig.set_brief(_brief(rig))

    dj._run_docket_job(1, 42, "brief")

    assert rig.calls == ["sync", "brief"]
    row = rig.rows[1]
    assert row["status"] == "complete"
    assert "eLibrary check failed" in row["error"]
    assert "FERC 520 streak" in row["error"]


def test_sync_crawl_failure_stays_fatal(rig):
    def sync(docket_id, progress=None):
        rig.calls.append("sync")
        raise RuntimeError("FERC 520 streak")

    rig.set_sync(sync)
    rig.set_brief(_brief(rig))

    dj._run_docket_job(1, 42, "sync")

    assert rig.calls == ["sync"]
    row = rig.rows[1]
    assert row["status"] == "failed"
    assert "FERC 520 streak" in row["error"]


def test_sync_skips_brief_when_nothing_new(rig):
    rig.set_sync(_sync_returning(rig, found=1, summarized=0))
    rig.set_brief(_brief(rig))

    dj._run_docket_job(1, 42, "sync")

    assert rig.calls == ["sync"]
    assert rig.rows[1]["status"] == "complete"


def test_sync_chains_brief_when_new_summaries_landed(rig):
    rig.set_sync(_sync_returning(rig, found=2, summarized=2))
    rig.set_brief(_brief(rig))

    dj._run_docket_job(1, 42, "sync")

    assert rig.calls == ["sync", "brief"]
    row = rig.rows[1]
    assert row["status"] == "complete"
    assert row["filings_summarized"] == 2
    assert row["cost_usd"] == pytest.approx(BRIEF_TOTALS["cost_usd"])


def test_cancel_during_crawl_cancels_without_regenerating(rig):
    def sync(docket_id, progress=None):
        rig.calls.append("sync")
        raise dj._JobCancelled()

    rig.set_sync(sync)
    rig.set_brief(_brief(rig))

    dj._run_docket_job(1, 42, "brief")

    assert rig.calls == ["sync"]
    assert rig.rows[1]["status"] == "cancelled"
