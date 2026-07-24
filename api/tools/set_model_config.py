"""Push prompts/model_config.json into the app_config DB override.

Why this exists: `appconfig.get_model_config()` merges the DB override *over*
the repo file, key by key, and every UI save writes back the whole merged dict
(api/routes/prompts.py save_model_config). So once the Models tab has been
saved even once, every key it captured is pinned in the DB and editing the
repo file changes nothing in prod. A model or token-budget bump in git needs
this tool (or a UI re-save) to actually take effect.

Usage (from a laptop, against prod):

    DATABASE_URL=<Railway DATABASE_PUBLIC_URL> \\
    python -m api.tools.set_model_config [--apply | --clear] [--yes]

Modes:
  (default)  dry run — print the DB override, the repo file, and the diff
  --apply    write {**db_override, **file} back, so the file's keys win and
             any extra keys only the DB knows about survive
  --clear    delete the model_config row entirely, so the repo file is the
             sole source of truth until the next UI save

Reasoning effort is NOT config — it follows the model family in
pipeline/summarizer.py (_EFFORT_BY_FAMILY), so it needs no DB change.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlparse

from pipeline import appconfig
from pipeline import db


def _db_target() -> str:
    """Host/database the pipeline will actually connect to. pipeline/db.py
    load_dotenv()s a local .env, which can silently shadow the DATABASE_URL
    you meant to use — print what's resolved and look at it."""
    import os
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return "(unset)"
    p = urlparse(url)
    return f"{p.hostname}:{p.port or 5432}{p.path}"


def _diff(before: dict, after: dict) -> list[str]:
    lines = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            lines.append(f"  {key}: {old!r} -> {new!r}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="merge the repo file over the DB override")
    mode.add_argument("--clear", action="store_true",
                      help="delete the DB override so the repo file governs")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = ap.parse_args(argv)

    file_cfg = appconfig._file_model_config()
    db_cfg = appconfig._db_get(appconfig.MODEL_CONFIG_KEY)
    db_cfg = db_cfg if isinstance(db_cfg, dict) else {}
    effective = {**file_cfg, **db_cfg}

    print(f"Database:   {_db_target()}")
    print(f"Repo file:  {json.dumps(file_cfg, indent=2)}")
    print(f"DB override ({len(db_cfg)} key(s)): {json.dumps(db_cfg, indent=2)}")

    if args.clear:
        target = file_cfg
        action = "DELETE the model_config override row"
    elif args.apply:
        target = {**db_cfg, **file_cfg}
        action = "WRITE the merged config back to app_config"
    else:
        target = {**db_cfg, **file_cfg}
        action = None

    changes = _diff(effective, target)
    print("\nEffective config change:")
    print("\n".join(changes) if changes else "  (none — already up to date)")

    if action is None:
        print("\nDry run. Re-run with --apply (merge file over DB) or "
              "--clear (drop the override).")
        return 0
    if not changes and args.clear and not db_cfg:
        print("\nNothing to do.")
        return 0

    if not args.yes:
        print(f"\nAbout to {action} on {_db_target()}.")
        if input("Type 'yes' to continue: ").strip().lower() != "yes":
            print("Aborted.")
            return 1

    if args.clear:
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute("DELETE FROM app_config WHERE key = %s",
                            (appconfig.MODEL_CONFIG_KEY,))
                deleted = cur.rowcount
        print(f"Deleted {deleted} row(s). The repo file now governs.")
    else:
        appconfig.set_model_config(target, updated_by="set_model_config")
        print("Wrote merged model_config to app_config.")

    print(json.dumps(appconfig.get_model_config(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
