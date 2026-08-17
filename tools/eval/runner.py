"""Execute the REAL summarization pipeline over frozen golden cases.

    export ANTHROPIC_API_KEY=…       # lives in poolside-legacy/.env locally
    python -m tools.eval.runner --run-id baseline \
        [--cases id1,id2] [--prompts-ref <git-ref>] [--model key=value]... \
        [--workers 3]

What runs is the actual code — `run_meeting_summarization` for ISO cases and
`docket_ingest.summarize_filing` for FERC cases — with only the edges swapped:
`pipeline.db` → EvalDB (frozen payload in, versions captured out), prompts →
the working tree's prompts/ files or any git ref (`--prompts-ref
716e594` replays the pre-Batch-1 prompt set forever), models → repo
model_config.json overlaid with `--model` pairs (e.g. `--model
meeting_model=claude-fable-5` for the Fable-vs-Opus comparison). Images are
off; DB prompt overrides are deliberately bypassed (evals are hermetic).

Outputs land in tools/eval/runs/<run-id>/: manifest.json plus one
<case_id>.json per case with the produced versions, usage, and cost.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pipeline.docket_ingest as docket_ingest
import pipeline.summarizer as summarizer
from tools.eval.cases import CASES
from tools.eval.fakedb import EvalDB

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
GOLDEN_DIR = EVAL_DIR / "golden"
RUNS_DIR = EVAL_DIR / "runs"


class PromptSource:
    """Slug → prompt text, from the working tree or a pinned git ref."""

    def __init__(self, git_ref: str | None = None):
        self.git_ref = git_ref
        self._cache: dict[str, str] = {}

    def get(self, slug: str) -> str:
        if slug not in self._cache:
            if self.git_ref:
                proc = subprocess.run(
                    ["git", "-C", str(REPO_ROOT), "show",
                     f"{self.git_ref}:prompts/{slug}.md"],
                    capture_output=True, text=True)
                self._cache[slug] = proc.stdout if proc.returncode == 0 else ""
            else:
                path = REPO_ROOT / "prompts" / f"{slug}.md"
                self._cache[slug] = (
                    path.read_text(encoding="utf-8") if path.exists() else "")
        return self._cache[slug]


def _build_model_cfg(overrides: list[str]) -> dict:
    cfg = {**summarizer._DEFAULT_MODELS, **summarizer._DEFAULT_MAX_TOKENS}
    mc_path = REPO_ROOT / "prompts" / "model_config.json"
    if mc_path.exists():
        cfg.update(json.loads(mc_path.read_text(encoding="utf-8")))
    for pair in overrides:
        key, _, value = pair.partition("=")
        if not value:
            sys.exit(f"--model expects key=value, got {pair!r}")
        cfg[key] = int(value) if value.isdigit() else value
    return cfg


@contextmanager
def _patched(evaldb: EvalDB, prompts: PromptSource, model_cfg: dict,
             workers: int, skip_briefing: bool):
    saved: list[tuple[object, str, object]] = []

    def swap(mod, name, val):
        saved.append((mod, name, getattr(mod, name)))
        setattr(mod, name, val)

    swap(summarizer, "db", evaldb)
    swap(docket_ingest, "db", evaldb)
    swap(summarizer, "_load_prompt", prompts.get)
    swap(summarizer, "_load_model_config", lambda: dict(model_cfg))
    swap(summarizer, "_load_image_config", lambda: {"enabled": False})
    swap(summarizer, "_load_parallel_workers", lambda: workers)
    swap(summarizer, "_load_char_caps",
         lambda: (summarizer._MAX_CHARS_PER_DOC, summarizer._MAX_CHARS_PER_ITEM))
    if skip_briefing:
        swap(summarizer, "_run_meeting_briefing", lambda *a, **k: True)
    try:
        yield
    finally:
        for mod, name, val in reversed(saved):
            setattr(mod, name, val)


def _run_case(case: dict, client, prompts: PromptSource, model_cfg: dict,
              workers: int) -> dict:
    payload = case["payload"]
    kind = case["kind"]
    started = time.time()

    if kind in ("iso_item", "iso_meeting"):
        world = EvalDB(meeting=payload["meeting"], items=payload["items"],
                       docs_by_item=payload["docs_by_item"])
        meeting = payload["meeting"]
        with _patched(world, prompts, model_cfg, workers,
                      skip_briefing=(kind == "iso_item")), \
                summarizer.capture_usage() as usage:
            result = summarizer.run_meeting_summarization(
                meeting["id"], client,
                committee_short=meeting.get("type_short") or "MC",
                venue_short=meeting.get("venue_short") or "ISO-NE",
                force_rerun=True,
            )
    elif kind == "ferc_filing":
        world = EvalDB(filing_files=payload["files"])
        with _patched(world, prompts, model_cfg, workers, skip_briefing=False), \
                summarizer.capture_usage() as usage:
            ok = docket_ingest.summarize_filing(
                payload["filing"], ferc=None, client=client, cfg={},
                model_cfg=model_cfg, progress=print)
            result = {"ok": ok}
    else:
        raise SystemExit(f"unknown kind {kind}")

    outputs = [r for r in world.created if r.get("detailed")]
    return {
        "case_id": case["case_id"],
        "kind": kind,
        "content_sha256": case["content_sha256"],
        "duration_s": round(time.time() - started, 1),
        "runner_result": result,
        "usage": summarizer.totals_from_usage_log(usage),
        "outputs": outputs,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--cases", help="comma-separated case ids (default: all with golden files)")
    ap.add_argument("--prompts-ref",
                    help="git ref for the prompt set (default: working tree)")
    ap.add_argument("--model", action="append", default=[],
                    metavar="KEY=VALUE",
                    help="model_config override, repeatable (e.g. meeting_model=claude-fable-5)")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Locally it lives in "
                 "/Users/bwgriffiths/Documents/Analysis/poolside-legacy/.env")

    available = sorted(p.stem for p in GOLDEN_DIR.glob("*.json"))
    wanted = args.cases.split(",") if args.cases else available
    missing = [c for c in wanted if c not in available]
    if missing:
        sys.exit(f"No golden file for: {missing}. Run fetch_golden first. "
                 f"Available: {available}")
    unknown = [c for c in wanted if c not in CASES]
    if unknown:
        sys.exit(f"Not in the case registry: {unknown}")

    run_dir = RUNS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    git_sha = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()

    prompts = PromptSource(args.prompts_ref)
    model_cfg = _build_model_cfg(args.model)
    client = summarizer.make_client()

    manifest = {
        "run_id": args.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "prompts_ref": args.prompts_ref,
        "model_overrides": args.model,
        "workers": args.workers,
        "cases": wanted,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                           encoding="utf-8")

    total_cost = 0.0
    for case_id in wanted:
        case = json.loads((GOLDEN_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
        print(f"── {case_id} ({case['kind']}) ──")
        rec = _run_case(case, client, prompts, model_cfg, args.workers)
        (run_dir / f"{case_id}.json").write_text(
            json.dumps(rec, indent=1, default=str), encoding="utf-8")
        cost = rec["usage"].get("cost_usd", 0.0)
        total_cost += cost
        print(f"   {len(rec['outputs'])} output(s), {rec['duration_s']}s, "
              f"${cost:.2f}")

    print(f"\nRun '{args.run_id}' complete — total ${total_cost:.2f} → {run_dir}")


if __name__ == "__main__":
    main()
