"""Image prune: job wiring and admin endpoint flow (db + config stubbed).

The heavy lifting is one SQL statement exercised live against the dev DB;
these tests pin the orchestration around it:

  * the weekly job prunes, vacuums only when something was deleted, and
    stamps app_config `image_prune_last`;
  * a failing prune raises the job_failed notification instead of dying
    silently;
  * the admin endpoint returns fresh stats and stamps who ran it.
"""
import api.routes.admin as admin_mod
import api.scheduler as sched


class Recorder:
    def __init__(self, deleted=5, fail=False, storage_keys=None):
        self.deleted = deleted
        self.fail = fail
        self.storage_keys = storage_keys or []
        self.calls = []
        self.stamped = None
        self.deleted_keys = None

    # pipeline.db surface
    def image_stats(self):
        self.calls.append("stats")
        return {"stored": 100, "stored_bytes": 1000, "referenced": 20,
                "unreferenced_bytes": 800}

    def prune_unreferenced_document_images(self, older_than_days=30):
        self.calls.append(f"prune:{older_than_days}")
        if self.fail:
            raise RuntimeError("boom")
        return {"deleted": self.deleted, "freed_bytes": self.deleted * 100,
                "storage_keys": list(self.storage_keys)}

    def vacuum_document_images(self):
        self.calls.append("vacuum")

    # pipeline.storage surface
    def delete_images(self, keys):
        self.calls.append("delete_objects")
        self.deleted_keys = list(keys)
        return len(keys)

    # pipeline.appconfig surface
    def set_config_key(self, key, value, updated_by="system"):
        self.stamped = (key, value, updated_by)


def _patch_world(monkeypatch, rec):
    import pipeline.appconfig as appconfig
    import pipeline.db as db
    import pipeline.storage as storage

    monkeypatch.setattr(db, "image_stats", rec.image_stats)
    monkeypatch.setattr(db, "prune_unreferenced_document_images",
                        rec.prune_unreferenced_document_images)
    monkeypatch.setattr(db, "vacuum_document_images", rec.vacuum_document_images)
    monkeypatch.setattr(storage, "delete_images", rec.delete_images)
    monkeypatch.setattr(appconfig, "set_config_key", rec.set_config_key)


def test_job_prunes_vacuums_and_stamps(monkeypatch):
    rec = Recorder(deleted=5)
    _patch_world(monkeypatch, rec)
    sched._prune_images_job()
    assert "prune:30" in rec.calls
    assert "vacuum" in rec.calls
    key, value, by = rec.stamped
    assert key == "image_prune_last"
    assert value["deleted"] == 5 and value["freed_bytes"] == 500
    assert by == "scheduler"


def test_job_skips_vacuum_when_nothing_deleted(monkeypatch):
    rec = Recorder(deleted=0)
    _patch_world(monkeypatch, rec)
    sched._prune_images_job()
    assert "vacuum" not in rec.calls
    assert rec.stamped[1]["deleted"] == 0


def test_job_failure_raises_notification(monkeypatch):
    rec = Recorder(fail=True)
    _patch_world(monkeypatch, rec)
    notified = []
    monkeypatch.setattr(sched, "_notify_job_failed",
                        lambda job, exc: notified.append(job))
    sched._prune_images_job()
    assert notified == ["prune_images"]
    assert rec.stamped is None


def test_admin_endpoint_returns_stats_and_stamps_user(monkeypatch):
    rec = Recorder(deleted=3)
    _patch_world(monkeypatch, rec)
    # admin.py binds `db` at import; patch its reference too.
    monkeypatch.setattr(admin_mod.db, "prune_unreferenced_document_images",
                        rec.prune_unreferenced_document_images)
    monkeypatch.setattr(admin_mod.db, "vacuum_document_images",
                        rec.vacuum_document_images)
    monkeypatch.setattr(admin_mod.db, "image_stats", rec.image_stats)

    out = admin_mod.prune_images({"email": "ben@example.com"})
    assert out["deleted"] == 3
    assert out["stats"]["stored"] == 100
    assert rec.stamped[1]["by"] == "ben@example.com"


def test_job_deletes_bucket_objects_for_pruned_rows(monkeypatch):
    """Offloaded rows leave objects behind — the job must pass their keys
    to storage.delete_images, and the key list itself must not be stamped
    into app_config (it's plumbing, not stats)."""
    keys = ["docimg/1/1_0.png", "docimg/2/4_1.png"]
    rec = Recorder(deleted=2, storage_keys=keys)
    _patch_world(monkeypatch, rec)
    sched._prune_images_job()
    assert rec.deleted_keys == keys
    assert rec.stamped[1]["objects_deleted"] == 2
    assert "storage_keys" not in rec.stamped[1]


def test_admin_endpoint_reports_object_deletes(monkeypatch):
    keys = ["docimg/9/2_0.png"]
    rec = Recorder(deleted=1, storage_keys=keys)
    _patch_world(monkeypatch, rec)
    monkeypatch.setattr(admin_mod.db, "prune_unreferenced_document_images",
                        rec.prune_unreferenced_document_images)
    monkeypatch.setattr(admin_mod.db, "vacuum_document_images",
                        rec.vacuum_document_images)
    monkeypatch.setattr(admin_mod.db, "image_stats", rec.image_stats)

    out = admin_mod.prune_images({"email": "ben@example.com"})
    assert rec.deleted_keys == keys
    assert out["objects_deleted"] == 1
    assert "storage_keys" not in out
    assert "storage_keys" not in rec.stamped[1]
