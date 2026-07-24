"""Backfill document_images bytes into object storage.

Moves every legacy row (image_b64 populated, storage_key NULL) into the
configured bucket, then frees the DB bytes: per row, upload the PNG → set
storage_key/size_bytes → NULL out image_b64. Batched, idempotent and
resumable — rerun after any interruption and it continues where it left
off (completed rows no longer match the predicate; a re-upload overwrites
the same deterministic key harmlessly).

Usage (from a laptop, against prod):

    DATABASE_URL=<Railway DATABASE_PUBLIC_URL> \\
    POOLSIDE_STORAGE_BUCKET=... \\
    POOLSIDE_STORAGE_ACCESS_KEY=... \\
    POOLSIDE_STORAGE_SECRET_KEY=... \\
    POOLSIDE_STORAGE_ENDPOINT=https://storage.railway.app \\
    python -m api.tools.backfill_image_storage [--batch-size 25] [--limit N]
        [--dry-run] [--no-vacuum] [--yes]

Ends with VACUUM FULL on document_images (skip with --no-vacuum) so the
volume actually shrinks — DELETE/UPDATE alone never returns disk space.
Avoid the Sunday 05:00 ET prune window.

Safety rails:
  * refuses to run without object storage configured;
  * prints the resolved DB target and storage driver and asks for
    confirmation (pipeline/db.py load_dotenv()s a local .env, which can
    silently shadow the DATABASE_URL you meant to use — look at what it
    prints);
  * corrupt base64 rows are reported and skipped, never modified;
  * the UPDATE re-checks image_b64 IS NOT NULL, so a row pruned or
    re-extracted mid-flight is left alone.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import sys
from urllib.parse import urlparse

from pipeline import db, storage


def _db_target() -> str:
    """host:port/dbname of the effective DATABASE_URL — never the password."""
    import os
    url = os.environ.get("DATABASE_URL", "")
    try:
        p = urlparse(url)
        return f"{p.hostname}:{p.port or 5432}{p.path}"
    except Exception:
        return "<unparseable DATABASE_URL>"


def _pending(cur) -> dict:
    cur.execute(
        """SELECT COUNT(*) AS rows,
                  COALESCE(SUM(length(image_b64)), 0) AS b64_bytes
             FROM document_images
            WHERE image_b64 IS NOT NULL AND storage_key IS NULL"""
    )
    return dict(cur.fetchone())


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Move legacy document_images.image_b64 bytes to object storage."
    )
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many rows (0 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report pending work and exit")
    ap.add_argument("--no-vacuum", action="store_true",
                    help="skip the VACUUM FULL at the end")
    ap.add_argument("--yes", action="store_true",
                    help="skip the interactive confirmation")
    args = ap.parse_args()

    driver = storage.get_driver()
    if driver is None:
        print("Object storage is not configured (POOLSIDE_STORAGE_* unset) — "
              "refusing to run.", file=sys.stderr)
        return 2

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            pending = _pending(cur)

    print(f"database : {_db_target()}")
    print(f"storage  : {driver.describe()}")
    print(f"pending  : {pending['rows']} row(s), "
          f"{pending['b64_bytes'] / 1_048_576:.1f} MB of base64")

    if args.dry_run or not pending["rows"]:
        if not pending["rows"]:
            print("Nothing to do.")
        return 0

    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    moved = corrupt = vanished = 0
    moved_bytes = 0
    last_id = 0  # keyset cursor: corrupt rows are passed over, not re-selected

    while True:
        if args.limit and moved + corrupt >= args.limit:
            print(f"--limit {args.limit} reached, stopping.")
            break
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute(
                    """SELECT id, document_id, page_or_slide, img_index, image_b64
                         FROM document_images
                        WHERE image_b64 IS NOT NULL AND storage_key IS NULL
                          AND id > %s
                        ORDER BY id
                        LIMIT %s""",
                    (last_id, args.batch_size),
                )
                rows = cur.fetchall()
                if not rows:
                    break
                for row in rows:
                    last_id = row["id"]
                    try:
                        raw = base64.b64decode(row["image_b64"])
                    except (binascii.Error, ValueError) as exc:
                        print(f"  ! id={row['id']}: corrupt base64, skipping ({exc})")
                        corrupt += 1
                        continue
                    key = storage.image_key(
                        row["document_id"], row["page_or_slide"], row["img_index"]
                    )
                    driver.put(key, raw, content_type="image/png")  # StorageError aborts
                    cur.execute(
                        """UPDATE document_images
                              SET storage_key = %s, size_bytes = %s, image_b64 = NULL
                            WHERE id = %s AND image_b64 IS NOT NULL""",
                        (key, len(raw), row["id"]),
                    )
                    if cur.rowcount == 0:
                        # pruned or re-extracted mid-flight; the uploaded
                        # object sits under the row's deterministic key and
                        # is harmless either way.
                        vanished += 1
                    else:
                        moved += 1
                        moved_bytes += len(raw)
            # _conn() commits here — one transaction per batch
        print(f"  {moved}/{pending['rows']} moved "
              f"({moved_bytes / 1_048_576:.1f} MB uploaded)")

    print(f"\ndone: moved={moved} corrupt-skipped={corrupt} vanished={vanished} "
          f"({moved_bytes / 1_048_576:.1f} MB)")

    if moved and not args.no_vacuum:
        print("VACUUM FULL document_images (reclaims the freed disk)...")
        db.vacuum_document_images()

    stats = db.image_stats()
    print(f"image_stats: stored={stats['stored']} offloaded={stats['offloaded']} "
          f"stored_bytes={stats['stored_bytes'] / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
