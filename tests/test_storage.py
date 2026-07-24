"""pipeline/storage.py: driver resolution and the dual-read byte accessor.

The module is the single seam between image rows and their bytes, so these
tests pin the contract everything else relies on:

  * env-driven driver resolution (unset → disabled → legacy DB behavior;
    partial S3 config fails safe; S3 beats the dev filesystem driver);
  * FSDriver roundtrip semantics (put/get/delete_many);
  * get_image_bytes precedence: in-memory bytes → storage_key → legacy
    base64 → None, with failures falling through rather than raising.
"""
import base64

import pytest

import pipeline.storage as storage

_ALL_VARS = (
    "POOLSIDE_STORAGE_BUCKET",
    "POOLSIDE_STORAGE_ACCESS_KEY",
    "POOLSIDE_STORAGE_SECRET_KEY",
    "POOLSIDE_STORAGE_ENDPOINT",
    "POOLSIDE_STORAGE_REGION",
    "POOLSIDE_STORAGE_DIR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Each test starts unconfigured and leaves no cached driver behind
    (a stray POOLSIDE_STORAGE_DIR in a dev .env must not leak in)."""
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    storage._reset_for_tests()
    yield
    storage._reset_for_tests()


def _enable_fs(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLSIDE_STORAGE_DIR", str(tmp_path))
    storage._reset_for_tests()


# ── driver resolution ────────────────────────────────────────────────────


def test_unconfigured_means_disabled():
    assert storage.get_driver() is None
    assert not storage.storage_enabled()
    assert storage.delete_images(["docimg/1/1_0.png"]) == 0
    with pytest.raises(storage.StorageError):
        storage.put_image("docimg/1/1_0.png", b"x")


def test_partial_s3_config_fails_safe(monkeypatch):
    monkeypatch.setenv("POOLSIDE_STORAGE_BUCKET", "b")
    storage._reset_for_tests()
    assert storage.get_driver() is None  # loud log, DB writes continue


def test_fs_driver_activates(monkeypatch, tmp_path):
    _enable_fs(monkeypatch, tmp_path)
    assert isinstance(storage.get_driver(), storage.FSDriver)


def test_s3_beats_fs(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLSIDE_STORAGE_BUCKET", "b")
    monkeypatch.setenv("POOLSIDE_STORAGE_ACCESS_KEY", "k")
    monkeypatch.setenv("POOLSIDE_STORAGE_SECRET_KEY", "s")
    monkeypatch.setenv("POOLSIDE_STORAGE_DIR", str(tmp_path))
    storage._reset_for_tests()
    # boto3 import is deferred to first use, so resolving stays cheap
    assert isinstance(storage.get_driver(), storage.S3Driver)


def test_image_key_layout():
    assert storage.image_key(42, 3, 1) == "docimg/42/3_1.png"


# ── FSDriver roundtrip ───────────────────────────────────────────────────


def test_fs_roundtrip_and_delete(monkeypatch, tmp_path):
    _enable_fs(monkeypatch, tmp_path)
    key = storage.image_key(7, 2, 0)
    storage.put_image(key, b"png-bytes")
    assert (tmp_path / "docimg" / "7" / "2_0.png").read_bytes() == b"png-bytes"
    assert storage.get_driver().get(key) == b"png-bytes"
    # delete is best-effort and counts only real removals
    assert storage.delete_images([key, "docimg/9/9_9.png", ""]) == 1
    assert not (tmp_path / "docimg" / "7" / "2_0.png").exists()


def test_fs_get_missing_raises(monkeypatch, tmp_path):
    _enable_fs(monkeypatch, tmp_path)
    with pytest.raises(storage.StorageError):
        storage.get_driver().get("docimg/404/1_0.png")


# ── get_image_bytes precedence ───────────────────────────────────────────


def test_in_memory_bytes_win():
    row = {"image_bytes": b"fresh", "image_b64": base64.b64encode(b"stale").decode()}
    assert storage.get_image_bytes(row) == b"fresh"


def test_legacy_b64_row():
    row = {"image_b64": base64.b64encode(b"legacy").decode()}
    assert storage.get_image_bytes(row) == b"legacy"


def test_corrupt_b64_returns_none():
    assert storage.get_image_bytes({"id": 5, "image_b64": "%%%not-base64"}) is None


def test_storage_key_row(monkeypatch, tmp_path):
    _enable_fs(monkeypatch, tmp_path)
    key = storage.image_key(1, 1, 0)
    storage.put_image(key, b"offloaded")
    assert storage.get_image_bytes({"storage_key": key}) == b"offloaded"


def test_storage_key_with_storage_disabled_falls_back_to_b64():
    row = {
        "storage_key": "docimg/1/1_0.png",
        "image_b64": base64.b64encode(b"still-here").decode(),
    }
    assert storage.get_image_bytes(row) == b"still-here"


def test_missing_object_falls_back_then_none(monkeypatch, tmp_path):
    _enable_fs(monkeypatch, tmp_path)
    assert storage.get_image_bytes({"storage_key": "docimg/9/1_0.png"}) is None
    row = {
        "storage_key": "docimg/9/1_0.png",
        "image_b64": base64.b64encode(b"fallback").decode(),
    }
    assert storage.get_image_bytes(row) == b"fallback"


def test_empty_row_is_none():
    assert storage.get_image_bytes({}) is None
