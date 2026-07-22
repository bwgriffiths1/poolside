"""Docket route logic: creation, job admission, serialization.

Drives the route functions directly against fakes of pipeline.db and the
job service (no Postgres, no threads, no FERC calls), and pins:

  * create normalizes/validates the docket number (400 on garbage, nothing
    created) and is idempotent on the number;
  * create + sync start a job through the service exactly once, and the
    admission guard reports already_running instead of double-spawning;
  * the detail payload groups files under filings, dedupes the intervenor
    roster, and isoformats dates;
  * cancel is a no-op with changed=False on terminal jobs.
"""
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

import api.routes.dockets as dr
from api.routes.dockets import CreateDocketBody

USER = {"email": "ben@example.com"}


class FakeDB:
    """Just enough of pipeline.db for the docket routes."""

    def __init__(self):
        self.dockets: dict[int, dict] = {}
        self.filings: dict[int, list[dict]] = {}
        self.files: dict[int, list[dict]] = {}
        self.summaries: dict[int, dict] = {}
        self.next_id = 1

    def create_docket(self, number, title=None, notes=None, created_by=None):
        for d in self.dockets.values():
            if d["docket_number"] == number:
                return dict(d)
        did = self.next_id
        self.next_id += 1
        row = {"id": did, "docket_number": number, "title": title,
               "notes": notes, "auto_refresh": True, "last_crawled_at": None,
               "created_by": created_by,
               "created_at": datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)}
        self.dockets[did] = row
        self.filings[did] = []
        return dict(row)

    def get_docket(self, did):
        row = self.dockets.get(did)
        return dict(row) if row else None

    def list_dockets(self):
        return [dict(d) for d in self.dockets.values()]

    def update_docket(self, did, **fields):
        self.dockets[did].update(fields)

    def delete_docket(self, did):
        return self.dockets.pop(did, None) is not None

    def list_docket_filings(self, did):
        return [dict(f) for f in self.filings.get(did, [])]

    def list_docket_filing_files(self, did, with_content=False):
        out = []
        for fl in self.filings.get(did, []):
            out.extend(dict(x) for x in self.files.get(fl["id"], []))
        return out

    def get_current_summary(self, entity_type, entity_id):
        return self.summaries.get(entity_id)

    # raw-SQL escape hatch used by _brief_payload's staleness query
    def _conn(self):
        raise AssertionError("staleness query should be bypassed in tests")

    def _cursor(self, conn):  # pragma: no cover
        raise AssertionError


class FakeJobs:
    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self.started: list[tuple[int, str]] = []
        self.active: int | None = None
        self.next_id = 100

    def start_docket_job(self, docket_id, mode="sync", created_by="system"):
        if docket_id not in dr.db.dockets:
            return None
        if self.active is not None:
            return {"job_id": self.active, "already_running": True}
        jid = self.next_id
        self.next_id += 1
        self.jobs[jid] = {"id": jid, "docket_id": docket_id, "mode": mode,
                          "status": "queued", "progress_text": "",
                          "cost_usd": None, "started_at":
                          datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
                          "finished_at": None}
        self.started.append((docket_id, mode))
        return {"job_id": jid, "already_running": False, "mode": mode}

    def active_job_id(self, docket_id):
        return self.active

    def get_job(self, jid):
        row = self.jobs.get(jid)
        return dict(row) if row else None

    def request_cancel(self, jid):
        self.jobs[jid]["status"] = "cancelling"
        return True


@pytest.fixture
def fake(monkeypatch):
    fakedb = FakeDB()
    fakejobs = FakeJobs()
    monkeypatch.setattr(dr, "db", fakedb)
    monkeypatch.setattr(dr, "jobs_service", fakejobs)
    return fakedb, fakejobs


def test_create_normalizes_and_starts_sync(fake):
    fakedb, fakejobs = fake
    out = dr.create_docket(CreateDocketBody(docket_number=" er26-925 "), USER)
    assert out["docket"]["docket_number"] == "ER26-925"
    assert out["docket"]["created_by"] == "ben@example.com"
    assert out["job"]["already_running"] is False
    assert fakejobs.started == [(out["docket"]["id"], "sync")]


def test_create_rejects_garbage_number(fake):
    fakedb, fakejobs = fake
    with pytest.raises(HTTPException) as exc:
        dr.create_docket(CreateDocketBody(docket_number="not a docket"), USER)
    assert exc.value.status_code == 400
    assert fakedb.dockets == {}
    assert fakejobs.started == []


def test_create_idempotent_on_number(fake):
    fakedb, fakejobs = fake
    first = dr.create_docket(CreateDocketBody(docket_number="ER26-925"), USER)
    again = dr.create_docket(CreateDocketBody(docket_number="er26-925"), USER)
    assert again["docket"]["id"] == first["docket"]["id"]
    assert len(fakedb.dockets) == 1


def test_sync_missing_docket_404s(fake):
    with pytest.raises(HTTPException) as exc:
        dr.sync_docket(404, USER)
    assert exc.value.status_code == 404


def test_admission_guard_reports_already_running(fake):
    fakedb, fakejobs = fake
    d = dr.create_docket(CreateDocketBody(docket_number="ER26-925"), USER)
    fakejobs.active = d["job"]["job_id"]
    out = dr.sync_docket(d["docket"]["id"], USER)
    assert out["already_running"] is True
    assert out["job_id"] == d["job"]["job_id"]
    # only the original create's job was ever started
    assert len(fakejobs.started) == 1


def test_state_of_play_starts_brief_job(fake):
    fakedb, fakejobs = fake
    d = dr.create_docket(CreateDocketBody(docket_number="ER26-925"), USER)
    fakejobs.active = None
    out = dr.generate_state_of_play(d["docket"]["id"], USER)
    assert out["mode"] == "brief"
    assert fakejobs.started[-1] == (d["docket"]["id"], "brief")


def test_detail_groups_files_and_dedupes_intervenors(fake, monkeypatch):
    fakedb, fakejobs = fake
    d = dr.create_docket(CreateDocketBody(docket_number="ER26-925"), USER)
    did = d["docket"]["id"]
    fakedb.filings[did] = [
        {"id": 1, "accession_number": "20251230-5436", "category": "Submittal",
         "document_class": "Application/Petition/Request",
         "document_type": "Tariff Filing", "description": "ISO-NE files.",
         "sub_docket": "ER26-925-000", "filed_date": date(2025, 12, 30),
         "issued_date": None, "posted_date": None, "comments_due_date": None,
         "response_due_date": None, "ferc_cite": None,
         "filing_parties": [{"type": "AUTHOR", "org": "ISO New England Inc."}],
         "treatment": "full", "is_docless": False,
         "summary_one_line": "ISO-NE proposes.", "summary_detailed": "…",
         "summary_status": "draft", "summary_version": 1},
        {"id": 2, "accession_number": "20260107-5034", "category": "Submittal",
         "document_class": "Intervention",
         "document_type": "Motion/Notice of Intervention",
         "description": "(doc-less) Motion of MA DPU.", "sub_docket": None,
         "filed_date": date(2026, 1, 7), "issued_date": None,
         "posted_date": None, "comments_due_date": None,
         "response_due_date": None, "ferc_cite": None,
         "filing_parties": [{"type": "AUTHOR", "org": "MA DPU"}],
         "treatment": "skip", "is_docless": True,
         "summary_one_line": None, "summary_detailed": None,
         "summary_status": None, "summary_version": None},
        {"id": 3, "accession_number": "20260108-0001", "category": "Submittal",
         "document_class": "Intervention",
         "document_type": "Motion/Notice of Intervention",
         "description": "(doc-less) Second motion of MA DPU.", "sub_docket": None,
         "filed_date": date(2026, 1, 8), "issued_date": None,
         "posted_date": None, "comments_due_date": None,
         "response_due_date": None, "ferc_cite": None,
         "filing_parties": [{"type": "AUTHOR", "org": "MA DPU"}],
         "treatment": "skip", "is_docless": True,
         "summary_one_line": None, "summary_detailed": None,
         "summary_status": None, "summary_version": None},
    ]
    fakedb.files[1] = [
        {"id": 10, "filing_id": 1, "file_desc": "Transmittal Letter",
         "orig_file_name": "TL.pdf", "file_type": "pdf", "file_size": 100,
         "page_count": 10, "file_list_order": 1, "included": True,
         "has_content": True},
        {"id": 11, "filing_id": 1, "file_desc": "Clean Tariff",
         "orig_file_name": "CT.pdf", "file_type": "pdf", "file_size": 900,
         "page_count": 300, "file_list_order": 2, "included": False,
         "has_content": False},
    ]
    monkeypatch.setattr(dr, "_brief_payload", lambda _id: None)

    out = dr.get_docket(did)
    assert [f["accession_number"] for f in out["filings"]] == [
        "20251230-5436", "20260107-5034", "20260108-0001"]
    root = out["filings"][0]
    assert root["filed_date"] == "2025-12-30"
    assert [x["file_desc"] for x in root["files"]] == [
        "Transmittal Letter", "Clean Tariff"]
    assert root["files"][1]["included"] is False
    assert root["elibrary_url"].endswith("accession_number=20251230-5436")
    # roster deduped to one MA DPU row, earliest date
    assert out["intervenors"] == [{"org": "MA DPU", "date": "2026-01-07"}]


def test_cancel_terminal_job_is_noop(fake):
    fakedb, fakejobs = fake
    d = dr.create_docket(CreateDocketBody(docket_number="ER26-925"), USER)
    jid = d["job"]["job_id"]
    fakejobs.jobs[jid]["status"] = "complete"
    out = dr.cancel_docket_job(jid, USER)
    assert out == {"job_id": jid, "status": "complete", "changed": False}


def test_cancel_active_job(fake):
    fakedb, fakejobs = fake
    d = dr.create_docket(CreateDocketBody(docket_number="ER26-925"), USER)
    jid = d["job"]["job_id"]
    fakejobs.jobs[jid]["status"] = "running"
    out = dr.cancel_docket_job(jid, USER)
    assert out["status"] == "cancelling"
    assert out["changed"] is True


def test_delete_docket(fake):
    fakedb, _ = fake
    d = dr.create_docket(CreateDocketBody(docket_number="ER26-925"), USER)
    assert dr.delete_docket(d["docket"]["id"], USER) == {"deleted": True}
    with pytest.raises(HTTPException):
        dr.delete_docket(d["docket"]["id"], USER)
