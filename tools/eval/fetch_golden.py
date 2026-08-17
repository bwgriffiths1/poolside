"""Pull the registered golden cases out of the PRODUCTION database and
freeze them as JSON under tools/eval/golden/ (read-only against prod).

Usage:
    export EVAL_PROD_DATABASE_URL="postgresql://…"   # Railway DATABASE_PUBLIC_URL
    python -m tools.eval.fetch_golden [--cases id1,id2] [--force]

Existing golden files are left untouched unless --force is given — frozen
means frozen; a re-fetch that changes content_sha256 is reported loudly so
drift in prod extraction never silently rewrites a baseline's inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

from tools.eval.cases import CASES

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _payload_sha(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _fetch_meeting(cur, meeting_id: int) -> dict:
    cur.execute(
        """
        SELECT m.*, mt.short_name AS type_short, mt.name AS type_name,
               v.short_name AS venue_short, v.name AS venue_name
        FROM meetings m
        JOIN meeting_types mt ON mt.id = m.meeting_type_id
        JOIN venues v ON v.id = mt.venue_id
        WHERE m.id = %s
        """,
        (meeting_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"meeting {meeting_id} not found in prod")
    return dict(row)


def _fetch_docs(cur, item_id: int) -> list[dict]:
    # raw_content only — no source_url, so a runner can never accidentally
    # re-download; docs without cached text are excluded (and counted).
    cur.execute(
        """
        SELECT d.id, d.filename, d.file_type, d.ceii_skipped, d.ignored,
               d.raw_content
        FROM item_documents idoc
        JOIN documents d ON d.id = idoc.document_id
        WHERE idoc.item_id = %s AND d.raw_content IS NOT NULL
        ORDER BY d.id
        """,
        (item_id,),
    )
    return _rows(cur)


def _fetch_iso_item(cur, item_id: int) -> dict:
    cur.execute("SELECT * FROM agenda_items WHERE id = %s", (item_id,))
    item = cur.fetchone()
    if item is None:
        raise SystemExit(f"agenda item {item_id} not found in prod")
    item = dict(item)
    meeting = _fetch_meeting(cur, item["meeting_id"])
    return {
        "meeting": meeting,
        "items": [item],
        "docs_by_item": {str(item["id"]): _fetch_docs(cur, item["id"])},
    }


def _fetch_iso_meeting(cur, meeting_id: int) -> dict:
    meeting = _fetch_meeting(cur, meeting_id)
    cur.execute(
        "SELECT * FROM agenda_items WHERE meeting_id = %s ORDER BY seq",
        (meeting_id,),
    )
    items = [dict(r) for r in cur.fetchall()]
    # Prod carries an `inactive` column the local schema lacks — filter in
    # Python so this works against either.
    items = [it for it in items if not it.get("inactive")]
    docs_by_item = {str(it["id"]): _fetch_docs(cur, it["id"]) for it in items}
    return {"meeting": meeting, "items": items, "docs_by_item": docs_by_item}


def _fetch_ferc_filing(cur, filing_id: int) -> dict:
    cur.execute(
        """
        SELECT f.*, d.docket_number
        FROM docket_filings f JOIN dockets d ON d.id = f.docket_id
        WHERE f.id = %s
        """,
        (filing_id,),
    )
    filing = cur.fetchone()
    if filing is None:
        raise SystemExit(f"docket filing {filing_id} not found in prod")
    cur.execute(
        """
        SELECT id, file_id, file_desc, orig_file_name, file_type,
               file_list_order, included, raw_content
        FROM docket_filing_files
        WHERE filing_id = %s AND included AND raw_content IS NOT NULL
        ORDER BY file_list_order
        """,
        (filing_id,),
    )
    return {"filing": dict(filing), "files": _rows(cur)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", help="comma-separated case ids (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch and overwrite existing golden files")
    args = ap.parse_args()

    url = os.environ.get("EVAL_PROD_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set EVAL_PROD_DATABASE_URL (Railway Postgres DATABASE_PUBLIC_URL).")

    wanted = list(CASES) if not args.cases else args.cases.split(",")
    unknown = [c for c in wanted if c not in CASES]
    if unknown:
        sys.exit(f"Unknown case id(s): {unknown}. Known: {sorted(CASES)}")

    GOLDEN_DIR.mkdir(exist_ok=True)
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.set_session(readonly=True)
    try:
        with conn.cursor() as cur:
            for case_id in wanted:
                spec = CASES[case_id]
                out = GOLDEN_DIR / f"{case_id}.json"
                if out.exists() and not args.force:
                    print(f"frozen, skipping: {case_id}")
                    continue
                if spec["kind"] == "iso_item":
                    payload = _fetch_iso_item(cur, spec["item_id"])
                elif spec["kind"] == "iso_meeting":
                    payload = _fetch_iso_meeting(cur, spec["meeting_id"])
                elif spec["kind"] == "ferc_filing":
                    payload = _fetch_ferc_filing(cur, spec["filing_id"])
                else:
                    sys.exit(f"unknown kind {spec['kind']}")

                sha = _payload_sha(payload)
                if out.exists():
                    old = json.loads(out.read_text(encoding="utf-8"))
                    if old.get("content_sha256") != sha:
                        print(f"WARNING: {case_id} content drifted "
                              f"({old.get('content_sha256', '?')[:12]} → {sha[:12]})")
                doc = {
                    "schema": 1,
                    "case_id": case_id,
                    "kind": spec["kind"],
                    "description": spec["description"],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "content_sha256": sha,
                    "payload": payload,
                }
                out.write_text(json.dumps(doc, indent=1, default=str),
                               encoding="utf-8")
                size_kb = out.stat().st_size // 1024
                print(f"fetched: {case_id} ({size_kb} KB, sha {sha[:12]})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
