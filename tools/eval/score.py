"""Score an eval run: mechanical checks, LLM-judge rubric, pairwise duels.

    python -m tools.eval.score --run baseline                 # mechanical only
    python -m tools.eval.score --run baseline --judge         # + opus rubric
    python -m tools.eval.score --run baseline --pairwise fable-l3   # A/B duel

Mechanical checks are free and run on every produced output (TLDR discipline,
no KEEP_IMAGE residue, no unfilled placeholders, briefing parses through
api.briefing_parser). The judge grades one PRIMARY output per case
(agenda_item / docket_filing / meeting briefing) against the frozen source
text, on the roadmap rubric: faithfulness, decision-relevant completeness,
context-sufficiency for a non-follower, actionability, format. Pairwise mode
judges two runs' primary outputs with position swap (two calls per case) and
reports flip-consistent verdicts.

Results are written to <run>/scores.json and printed as a table.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pipeline.summarizer as summarizer
from tools.eval.cases import CASES

EVAL_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = EVAL_DIR / "golden"
RUNS_DIR = EVAL_DIR / "runs"
JUDGE_PROMPT = (EVAL_DIR / "judge_prompt.md").read_text(encoding="utf-8")

_PRIMARY_ENTITY = {"iso_item": "agenda_item", "iso_meeting": "meeting",
                   "ferc_filing": "docket_filing"}

_PAIRWISE_PROMPT = """You are comparing two summaries of the SAME source \
material, produced for an energy market analyst whose reader does not follow \
this committee/docket. Judge which better serves that reader on faithfulness \
to the source, decision-relevant completeness, context-sufficiency, and \
actionability — in that priority order.

[SOURCE MATERIAL]
{source}

---

[SUMMARY A]
{a}

---

[SUMMARY B]
{b}

---

Return ONLY a JSON object: {{"winner": "A"|"B"|"tie", "margin": \
"slight"|"clear", "rationale": "one sentence"}}"""


def _parse_json_reply(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in judge reply: {text[:200]!r}")
    blob = text[start:end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # call_llm's _clean_output escapes $ as \$ (and models sometimes emit
        # other Markdown-style escapes like \-) — none are valid JSON escapes.
        # Drop the backslash from any invalid escape sequence and retry.
        import re
        return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', "", blob))


def _extract_pairwise(reply: str) -> dict:
    """Field-level extraction for the tiny pairwise schema — judges routinely
    put unescaped quotes inside `rationale`, which breaks strict JSON."""
    import re
    m = re.search(r'"winner"\s*:\s*"(A|B|tie)"', reply)
    if not m:
        raise ValueError(f"no winner field in pairwise reply: {reply[:200]!r}")
    margin = re.search(r'"margin"\s*:\s*"(slight|clear)"', reply)
    rationale = re.search(r'"rationale"\s*:\s*"(.+?)"\s*\}', reply, re.DOTALL)
    return {
        "winner": m.group(1),
        "margin": margin.group(1) if margin else None,
        "rationale": rationale.group(1) if rationale else None,
    }


def _case_source(case_id: str, run_rec: dict, cap: int) -> str:
    """The text the primary output should be judged against."""
    golden = json.loads((GOLDEN_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    payload = golden["payload"]
    kind = golden["kind"]
    if kind == "iso_item":
        parts = [f"### [{d['filename']}]\n\n{d.get('raw_content') or ''}"
                 for docs in payload["docs_by_item"].values() for d in docs]
    elif kind == "ferc_filing":
        parts = [f"### [{f.get('file_desc') or f.get('orig_file_name')}]\n\n"
                 f"{f.get('raw_content') or ''}" for f in payload["files"]]
    else:  # iso_meeting: the briefing is judged against its own item summaries
        parts = [f"### Item {o['entity_id']}\n\n{o['detailed']}"
                 for o in run_rec["outputs"]
                 if o["entity_type"] == "agenda_item"]
    src = "\n\n---\n\n".join(parts)
    if len(src) > cap:
        src = src[:cap].rsplit("\n", 1)[0] + "\n…(truncated for judging)"
    return src


def _primary_output(rec: dict) -> dict | None:
    entity = _PRIMARY_ENTITY[rec["kind"]]
    matches = [o for o in rec["outputs"] if o["entity_type"] == entity]
    return matches[-1] if matches else None


def _mechanical(rec: dict) -> list[dict]:
    checks = []
    for o in rec["outputs"]:
        body = o.get("detailed") or ""
        one = o.get("one_line")
        c = {
            "entity": f"{o['entity_type']}:{o['entity_id']}",
            "one_line_present": bool(one),
            "one_line_word_count": len((one or "").split()),
            "one_line_ok": bool(one) and len(one.split()) <= 35,
            "body_nonempty": bool(body.strip()),
            "body_no_tldr_lead": not body.lstrip().lower().startswith("tldr"),
            "no_keep_image_residue": "KEEP_IMAGE" not in body,
            "no_unfilled_placeholders": not any(
                ph in body for ph in ("{text}", "{filename}", "{doc_summaries}")),
        }
        if o["entity_type"] == "meeting":
            try:
                from api.briefing_parser import parse_briefing_markdown
                b = parse_briefing_markdown(body, {
                    "title": "Eval briefing", "subtitle": "", "headline": "",
                    "generated_at": "", "model": "", "word_count": 0,
                    "reading_time": 0,
                })
                c["briefing_parses"] = True
                c["briefing_sections"] = len(b.sections)
                c["briefing_takeaways"] = len(b.tldr or [])
            except Exception as exc:
                c["briefing_parses"] = False
                c["briefing_parse_error"] = str(exc)[:200]
        c["all_ok"] = all(v for k, v in c.items()
                          if isinstance(v, bool))
        checks.append(c)
    return checks


def _judge_case(client, model: str, case_id: str, rec: dict, cap: int) -> dict:
    primary = _primary_output(rec)
    if primary is None:
        return {"error": "no primary output"}
    prompt = JUDGE_PROMPT.format(
        source=_case_source(case_id, rec, cap),
        summary=(primary.get("one_line") or "") + "\n\n" + primary["detailed"],
    )
    with summarizer.capture_usage() as usage:
        reply = summarizer.call_llm(client, model, prompt, max_tokens=16384,
                                    label=f"judge {case_id}")
    try:
        verdict = _parse_json_reply(reply)
    except Exception as exc:
        return {"error": str(exc)}
    verdict["judge_cost_usd"] = summarizer.totals_from_usage_log(usage)["cost_usd"]
    return verdict


def _pairwise_case(client, model: str, case_id: str, rec_a: dict, rec_b: dict,
                   cap: int) -> dict:
    a, b = _primary_output(rec_a), _primary_output(rec_b)
    if a is None or b is None:
        return {"error": "missing primary output"}
    source = _case_source(case_id, rec_a, cap)
    verdicts = []
    for first, second, mapping in ((a, b, {"A": "a", "B": "b"}),
                                   (b, a, {"A": "b", "B": "a"})):
        prompt = _PAIRWISE_PROMPT.format(
            source=source, a=first["detailed"], b=second["detailed"])
        reply = summarizer.call_llm(client, model, prompt, max_tokens=4096,
                                    label=f"pairwise {case_id}")
        try:
            v = _extract_pairwise(reply)
            verdicts.append({"winner": mapping.get(v["winner"], "tie"),
                             "margin": v.get("margin"),
                             "rationale": v.get("rationale")})
        except Exception as exc:
            verdicts.append({"error": str(exc)})
    valid = [v["winner"] for v in verdicts if "winner" in v]
    winners = set(valid)
    if not valid:
        verdict = "error"
    elif len(winners) == 1:
        verdict = valid[0]
    else:
        verdict = "split"
    return {
        "orders": verdicts,
        "consistent": len(valid) == 2 and len(winners) == 1 and "tie" not in winners,
        "verdict": verdict,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run id under tools/eval/runs/")
    ap.add_argument("--cases", help="comma-separated case ids (default: all in the run)")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--pairwise", help="second run id to duel against")
    ap.add_argument("--judge-model", default="claude-opus-5")
    ap.add_argument("--source-cap", type=int, default=60_000)
    args = ap.parse_args()

    run_dir = RUNS_DIR / args.run
    # Only registered case records — run dirs also hold manifest/scores files.
    recs = {p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(run_dir.glob("*.json")) if p.stem in CASES}
    if not recs:
        sys.exit(f"No case results in {run_dir}")
    if args.cases:
        wanted = args.cases.split(",")
        missing = [c for c in wanted if c not in recs]
        if missing:
            sys.exit(f"Not in this run: {missing}")
        recs = {c: recs[c] for c in wanted}

    scores: dict = {"run": args.run, "mechanical": {}, "judge": {},
                    "pairwise": {}}
    print(f"{'case':34} {'outputs':>7} {'mech':>5}", flush=True)
    for case_id, rec in recs.items():
        checks = _mechanical(rec)
        scores["mechanical"][case_id] = checks
        ok = sum(1 for c in checks if c["all_ok"])
        print(f"{case_id:34} {len(checks):>7} {ok:>3}/{len(checks)}")

    needs_llm = args.judge or args.pairwise
    if needs_llm:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY is not set (see poolside-legacy/.env).")
        client = summarizer.make_client()

    if args.judge:
        print(f"\nJudging with {args.judge_model}…")
        for case_id, rec in recs.items():
            v = scores["judge"][case_id] = _judge_case(
                client, args.judge_model, case_id, rec, args.source_cap)
            if "error" in v:
                print(f"{case_id:34} ERROR {v['error'][:80]}")
            else:
                print(f"{case_id:34} overall {v.get('overall')}  "
                      f"faith {v.get('faithfulness')}  compl {v.get('completeness')}  "
                      f"ctx {v.get('context_sufficiency')}  act {v.get('actionability')}  "
                      f"fmt {v.get('format')}")

    if args.pairwise:
        other_dir = RUNS_DIR / args.pairwise
        others = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                  for p in sorted(other_dir.glob("*.json")) if p.stem in CASES}
        shared = sorted(set(recs) & set(others))
        print(f"\nPairwise vs '{args.pairwise}' on {len(shared)} case(s) "
              f"(a={args.run}, b={args.pairwise})…")
        for case_id in shared:
            v = scores["pairwise"][case_id] = _pairwise_case(
                client, args.judge_model, case_id, recs[case_id],
                others[case_id], args.source_cap)
            print(f"{case_id:34} {v.get('verdict', 'error')}"
                  f"{' (order-consistent)' if v.get('consistent') else ''}")

    out = run_dir / "scores.json"
    out.write_text(json.dumps(scores, indent=1), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
