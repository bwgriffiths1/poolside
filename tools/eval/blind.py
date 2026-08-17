"""Build a blind A/B package from two runs' outputs on one case.

    python -m tools.eval.blind --a baseline --b fable-l3 \
        --case iso_meeting_june_mc --out ~/Desktop/blind_mc

Meeting-kind cases render through the REAL parse→docx path
(api.briefing_parser + pipeline.briefing) into blind_1.docx / blind_2.docx;
other kinds get blind_1.md / blind_2.md. Which run is 1 vs 2 is randomized
and recorded in key.json — read the documents before the key (that's the
point). Same pattern as the legacy June MC one-shot A/B set.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent / "runs"


def _primary(rec: dict) -> dict:
    entity = {"iso_item": "agenda_item", "iso_meeting": "meeting",
              "ferc_filing": "docket_filing"}[rec["kind"]]
    matches = [o for o in rec["outputs"] if o["entity_type"] == entity]
    if not matches:
        sys.exit(f"run has no {entity} output for this case")
    return matches[-1]


def _load(run_id: str, case_id: str) -> dict:
    path = RUNS_DIR / run_id / f"{case_id}.json"
    if not path.exists():
        sys.exit(f"missing {path} — run the runner first")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="first run id")
    ap.add_argument("--b", required=True, help="second run id")
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args()

    rec_a, rec_b = _load(args.a, args.case), _load(args.b, args.case)
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = [(args.a, _primary(rec_a)), (args.b, _primary(rec_b))]
    random.shuffle(entries)

    key = {"case": args.case, "assignment": {}}
    for slot, (run_id, output) in enumerate(entries, start=1):
        body = output["detailed"]
        if rec_a["kind"] == "iso_meeting":
            from api.briefing_parser import parse_briefing_markdown
            from pipeline.briefing import render_briefing_docx
            briefing = parse_briefing_markdown(body, {
                "title": f"Blind briefing {slot}", "subtitle": "",
                "headline": output.get("one_line") or "",
                "generated_at": "", "model": "", "word_count": 0,
                "reading_time": 0,
            })
            data = render_briefing_docx(
                briefing, committee=f"Blind briefing {slot}",
                meeting_dates=[])
            path = out_dir / f"blind_{slot}.docx"
            path.write_bytes(data)
        else:
            path = out_dir / f"blind_{slot}.md"
            one = output.get("one_line")
            path.write_text((f"TLDR: {one}\n\n" if one else "") + body,
                            encoding="utf-8")
        key["assignment"][f"blind_{slot}"] = run_id
        print(f"blind_{slot} ← (hidden)  {path}")

    (out_dir / "key.json").write_text(json.dumps(key, indent=2),
                                      encoding="utf-8")
    print(f"key (read AFTER ranking): {out_dir / 'key.json'}")


if __name__ == "__main__":
    main()
