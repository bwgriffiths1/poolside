"""Deep-dive route logic: creation, admission guard, serialization.

Drives the route functions directly against a fake of pipeline.db (no
Postgres, no threads doing real work — the job entry point is stubbed), and
pins:

  * create validates document ids up front (400, nothing created);
  * create links documents in selection order and spawns exactly one run;
  * the claim guard makes concurrent generate/rerun calls a no-op
    (no second thread) while a live claim holds;
  * the serializer resolves image refs and isoformats timestamps.
"""
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

import api.routes.deep_dives as dd
from api.routes.deep_dives import CreateDeepDiveBody


class FakeDB:
    """Just enough of pipeline.db for the deep-dive routes."""

    def __init__(self):
        self.reports: dict[int, dict] = {}
        self.docs: dict[int, list[tuple[int, int]]] = {}  # report -> [(doc, seq)]
        self.known_docs = {10, 11, 12}
        self.claims_granted = True
        self.next_id = 1

    # -- documents ---------------------------------------------------------
    def get_document(self, doc_id):
        return {"id": doc_id} if doc_id in self.known_docs else None

    # -- reports -----------------------------------------------------------
    def create_deep_dive_report(self, title, config=None, prompt_slug=None,
                                model_id=None, created_by="system"):
        rid = self.next_id
        self.next_id += 1
        row = {
            "id": rid, "title": title, "status": "draft",
            "config": config or {}, "prompt_slug": prompt_slug,
            "model_id": model_id, "report_md": None, "error_message": None,
            "created_by": created_by,
            "created_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        }
        self.reports[rid] = row
        self.docs[rid] = []
        return dict(row)

    def get_deep_dive_report(self, rid):
        row = self.reports.get(rid)
        return dict(row) if row else None

    def list_deep_dive_reports(self, limit=50):
        return [dict(r) for r in self.reports.values()]

    def update_deep_dive_report(self, rid, **fields):
        self.reports[rid].update(fields)

    def delete_deep_dive_report(self, rid):
        self.reports.pop(rid, None)
        self.docs.pop(rid, None)

    def claim_deep_dive_report(self, rid, stale_minutes=15):
        if not self.claims_granted:
            return None
        self.reports[rid]["status"] = "generating"
        return dict(self.reports[rid])

    # -- document links ----------------------------------------------------
    def add_deep_dive_document(self, rid, doc_id, seq=0):
        self.docs[rid].append((doc_id, seq))

    def get_deep_dive_documents(self, rid):
        return [
            {
                "id": doc_id, "seq": seq, "filename": f"doc{doc_id}.pdf",
                "file_type": "pdf", "meeting_id": 5,
                "meeting_date": date(2026, 5, 12), "end_date": None,
                "meeting_title": "Markets Committee",
                "type_short": "MC", "type_name": "Markets Committee",
                "venue_short": "ISO-NE",
            }
            for doc_id, seq in self.docs.get(rid, [])
        ]


@pytest.fixture
def fake(monkeypatch):
    fakedb = FakeDB()
    runs: list[int] = []
    monkeypatch.setattr(dd, "db", fakedb)
    monkeypatch.setattr(dd, "_run_deep_dive_job", runs.append)
    return fakedb, runs


USER = {"email": "ben@example.com"}


def _drain_threads():
    """The routes spawn a daemon thread per run; with the job stubbed it
    finishes instantly — join anything the test spawned."""
    import threading
    for t in threading.enumerate():
        if t.name.startswith("deep-dive-"):
            t.join(timeout=2)


def test_create_validates_documents(fake):
    fakedb, runs = fake
    with pytest.raises(HTTPException) as exc:
        dd.create_report(
            CreateDeepDiveBody(title="Bad", document_ids=[10, 999]), USER
        )
    assert exc.value.status_code == 400
    assert "999" in exc.value.detail
    assert fakedb.reports == {}          # nothing half-created
    assert runs == []


def test_create_links_docs_and_runs_once(fake):
    fakedb, runs = fake
    out = dd.create_report(
        CreateDeepDiveBody(title="  CAR-SA docs  ", document_ids=[12, 10]),
        USER,
    )
    _drain_threads()

    assert out["title"] == "CAR-SA docs"           # trimmed
    assert out["status"] == "generating"           # claim flipped it
    assert out["created_by"] == "ben@example.com"
    # Selection order preserved through seq.
    assert fakedb.docs[out["id"]] == [(12, 0), (10, 1)]
    assert runs == [out["id"]]
    # Serialization: sources joined, dates isoformatted.
    assert out["source_count"] == 2
    assert out["sources"][0]["meeting_date"] == "2026-05-12"
    assert out["created_at"].startswith("2026-07-18T12:00")


def test_claim_guard_blocks_second_run(fake):
    fakedb, runs = fake
    out = dd.create_report(
        CreateDeepDiveBody(title="T", document_ids=[10]), USER
    )
    _drain_threads()
    assert runs == [out["id"]]

    # A live claim holds: rerun must not spawn a second thread.
    fakedb.claims_granted = False
    again = dd.rerun_report(out["id"])
    _drain_threads()
    assert runs == [out["id"]]
    assert again["id"] == out["id"]


def test_rerun_missing_and_empty(fake):
    fakedb, _ = fake
    with pytest.raises(HTTPException) as exc:
        dd.rerun_report(404)
    assert exc.value.status_code == 404

    row = fakedb.create_deep_dive_report("empty")
    with pytest.raises(HTTPException) as exc:
        dd.rerun_report(row["id"])
    assert exc.value.status_code == 400


def test_report_body_resolves_image_refs(fake):
    fakedb, _ = fake
    row = fakedb.create_deep_dive_report("imgs")
    fakedb.update_deep_dive_report(
        row["id"], status="complete",
        report_md="Intro\n\n<!-- image_id:42 -->\n\nEnd",
    )
    out = dd.get_report(row["id"])
    assert "![figure 42](/api/images/42)" in out["report_md"]
    assert "<!--" not in out["report_md"]


def test_list_omits_bodies(fake):
    fakedb, _ = fake
    row = fakedb.create_deep_dive_report("listed")
    fakedb.update_deep_dive_report(row["id"], status="complete",
                                   report_md="big body")
    rows = dd.list_reports()
    assert len(rows) == 1
    assert "report_md" not in rows[0]
    assert rows[0]["source_count"] == 0
