"""Mine analyst edits out of prod into a curated regression file.

    export EVAL_PROD_DATABASE_URL="postgresql://…"
    python -m tools.eval.mine_regressions [--out tools/eval/regressions.yaml]

Every `summary_versions` row with is_manual=true is a place Ben corrected the
machine — free regression data ("Ben fixed this once; don't regress"). This
pulls each manual version alongside the auto version it replaced and emits a
YAML skeleton with the first point of divergence excerpted; the `lesson:`
fields start as TODO and are meant to be curated by hand into judge/check
rules. Read-only against prod.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import yaml


def _first_divergence(a: str, b: str, width: int = 240) -> dict:
    n = min(len(a), len(b))
    idx = next((i for i in range(n) if a[i] != b[i]), n)
    lo = max(0, idx - width // 3)
    return {"prior_excerpt": a[lo:lo + width],
            "manual_excerpt": b[lo:lo + width]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).parent / "regressions.yaml"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists (curated?) — use --force to regenerate.")

    url = os.environ.get("EVAL_PROD_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set EVAL_PROD_DATABASE_URL (Railway Postgres DATABASE_PUBLIC_URL).")

    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.set_session(readonly=True)
    entries = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT entity_type, entity_id, version, one_line, detailed,
                       created_at, created_by
                FROM summary_versions WHERE is_manual
                ORDER BY entity_type, entity_id, version
            """)
            manuals = [dict(r) for r in cur.fetchall()]
            for m in manuals:
                cur.execute("""
                    SELECT one_line, detailed FROM summary_versions
                    WHERE entity_type = %s AND entity_id = %s AND version < %s
                      AND NOT is_manual AND detailed IS NOT NULL
                    ORDER BY version DESC LIMIT 1
                """, (m["entity_type"], m["entity_id"], m["version"]))
                prior = cur.fetchone()
                if prior is None or not (m.get("detailed") or "").strip():
                    continue
                entry = {
                    "entity": f"{m['entity_type']}:{m['entity_id']}",
                    "manual_version": m["version"],
                    "edited_at": str(m["created_at"]),
                    "edited_by": m["created_by"],
                    "lesson": "TODO — curate: what did the machine get wrong?",
                }
                entry.update(_first_divergence(prior["detailed"] or "",
                                               m["detailed"] or ""))
                if (m.get("one_line") or "") != (prior.get("one_line") or ""):
                    entry["one_line_before"] = prior.get("one_line")
                    entry["one_line_after"] = m.get("one_line")
                entries.append(entry)
    finally:
        conn.close()

    out.write_text(
        yaml.safe_dump({"regressions": entries}, sort_keys=False,
                       allow_unicode=True, width=100),
        encoding="utf-8")
    print(f"Wrote {len(entries)} regression seed(s) → {out}")


if __name__ == "__main__":
    main()
