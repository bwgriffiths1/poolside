"""Object storage for extracted document images (document_images rows).

Historically the PNG bytes lived base64-encoded in document_images.image_b64,
which made that one table ~85% of the database and kept ratcheting the
Railway Postgres volume up (2026-07-23 disk-full incident). Bytes now go to
an S3-compatible bucket (a Railway Storage Bucket in prod) under a
deterministic storage_key; the DB row keeps metadata only.

Activation is env-driven and optional, mirroring api/services/mailer.py:
with no POOLSIDE_STORAGE_* vars set, get_driver() is None and every write
keeps landing in image_b64 exactly as before. Prod must never silently
write to ephemeral disk, so the filesystem driver only activates via an
explicit POOLSIDE_STORAGE_DIR (dev convenience — no bucket or creds needed).

Env vars:
    POOLSIDE_STORAGE_BUCKET       bucket name (presence gates the S3 driver)
    POOLSIDE_STORAGE_ACCESS_KEY   S3 access key id
    POOLSIDE_STORAGE_SECRET_KEY   S3 secret key
    POOLSIDE_STORAGE_ENDPOINT     endpoint URL (Railway: https://storage.railway.app)
    POOLSIDE_STORAGE_REGION       region, default "auto"
    POOLSIDE_STORAGE_DIR          filesystem driver root (dev only)

Readers must go through get_image_bytes(), which resolves in-memory bytes,
then the bucket, then legacy base64 — so rows written in either era keep
serving during and after the backfill (api/tools/backfill_image_storage.py).
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """A storage operation failed (missing object, network, config)."""


class S3Driver:
    """Any S3-compatible endpoint: Railway Storage Bucket, R2, AWS S3, MinIO.

    The boto3 import and client construction are deferred to first use so
    importing this module (and resolving the driver) never requires boto3 —
    only actually talking to a bucket does.
    """

    def __init__(self, bucket: str, access_key: str, secret_key: str,
                 endpoint: str | None = None, region: str = "auto"):
        self.bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self.endpoint = endpoint or None
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover
                raise StorageError(f"boto3 not installed: {exc}") from exc
            # Tight timeouts: a bucket outage must degrade image requests,
            # not hang uvicorn's worker threads.
            self._client = boto3.client(
                "s3",
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                endpoint_url=self.endpoint,
                region_name=self.region,
                config=Config(
                    connect_timeout=5,
                    read_timeout=10,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        return self._client

    def describe(self) -> str:
        return f"s3 bucket={self.bucket} endpoint={self.endpoint or 'aws-default'}"

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        try:
            self._get_client().put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
            )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"put {key}: {exc}") from exc

    def get(self, key: str) -> bytes:
        try:
            resp = self._get_client().get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"get {key}: {exc}") from exc

    def delete_many(self, keys: list[str]) -> int:
        deleted = 0
        client = self._get_client()
        for i in range(0, len(keys), 1000):  # S3 delete_objects cap
            chunk = keys[i:i + 1000]
            try:
                resp = client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
                )
                deleted += len(chunk) - len(resp.get("Errors") or [])
            except Exception as exc:
                raise StorageError(f"delete_objects: {exc}") from exc
        return deleted


class FSDriver:
    """Local-filesystem stand-in for dev: same interface, keys become paths."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def describe(self) -> str:
        return f"filesystem root={self.root}"

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise StorageError(f"put {key}: {exc}") from exc

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError(f"get {key}: {exc}") from exc

    def delete_many(self, keys: list[str]) -> int:
        deleted = 0
        for key in keys:
            try:
                self._path(key).unlink()
                deleted += 1
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise StorageError(f"delete {key}: {exc}") from exc
        return deleted


_driver: S3Driver | FSDriver | None = None
_resolved = False


def get_driver() -> S3Driver | FSDriver | None:
    """Resolve the configured driver once (None = storage disabled)."""
    global _driver, _resolved
    if _resolved:
        return _driver
    bucket = os.environ.get("POOLSIDE_STORAGE_BUCKET", "").strip()
    access = os.environ.get("POOLSIDE_STORAGE_ACCESS_KEY", "").strip()
    secret = os.environ.get("POOLSIDE_STORAGE_SECRET_KEY", "").strip()
    fs_dir = os.environ.get("POOLSIDE_STORAGE_DIR", "").strip()
    if bucket and access and secret:
        _driver = S3Driver(
            bucket=bucket,
            access_key=access,
            secret_key=secret,
            endpoint=os.environ.get("POOLSIDE_STORAGE_ENDPOINT", "").strip() or None,
            region=os.environ.get("POOLSIDE_STORAGE_REGION", "").strip() or "auto",
        )
    elif bucket or access or secret:
        # Half-configured is a config mistake, not a request for DB mode —
        # but failing safe (DB writes) keeps summarize runs alive; the loud
        # log is the signal to fix the env.
        logger.error(
            "Incomplete POOLSIDE_STORAGE_* config (need BUCKET + ACCESS_KEY "
            "+ SECRET_KEY) — object storage disabled, writes stay in the DB"
        )
    elif fs_dir:
        _driver = FSDriver(Path(fs_dir))
    _resolved = True
    return _driver


def _reset_for_tests() -> None:
    global _driver, _resolved
    _driver = None
    _resolved = False


def storage_enabled() -> bool:
    return get_driver() is not None


def image_key(document_id: int, page_or_slide: int, img_index: int) -> str:
    """Deterministic object key, aligned with the table's
    UNIQUE(document_id, page_or_slide, img_index) — a re-extract upsert
    overwrites the same object instead of orphaning a new one."""
    return f"docimg/{document_id}/{page_or_slide}_{img_index}.png"


def put_image(key: str, data: bytes) -> None:
    driver = get_driver()
    if driver is None:
        raise StorageError("object storage is not configured")
    driver.put(key, data, content_type="image/png")


def get_image_bytes(row: Mapping) -> bytes | None:
    """The single byte accessor for a document_images row (or row-shaped
    dict), whatever era it was written in:

      1. in-memory "image_bytes" — freshly extracted rows and the dicts
         _fetch_images_for_refs builds carry raw bytes already;
      2. "storage_key" — fetch from the bucket (failure logged, falls
         through so a legacy b64 copy can still serve);
      3. legacy "image_b64" — base64-decode;
      4. None — caller 404s or skips the figure.
    """
    raw = row.get("image_bytes")
    if raw:
        return bytes(raw)
    key = row.get("storage_key")
    if key:
        driver = get_driver()
        if driver is not None:
            try:
                return driver.get(key)
            except StorageError as exc:
                logger.warning("storage read failed for %s: %s", key, exc)
        else:
            logger.warning(
                "row has storage_key %s but object storage is not configured", key
            )
    b64 = row.get("image_b64")
    if b64:
        try:
            return base64.b64decode(b64)
        except (binascii.Error, ValueError) as exc:
            logger.warning("corrupt image_b64 for image id=%s: %s", row.get("id"), exc)
    return None


def delete_images(keys: list[str]) -> int:
    """Best-effort object deletion (prune job). Never raises; returns the
    number of objects deleted (0 when disabled or nothing to do)."""
    keys = [k for k in keys if k]
    driver = get_driver()
    if driver is None or not keys:
        return 0
    try:
        return driver.delete_many(keys)
    except StorageError as exc:
        logger.warning("storage delete failed (%d key(s)): %s", len(keys), exc)
        return 0
