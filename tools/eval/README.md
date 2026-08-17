# Summarization eval harness

Measure summary quality before/after prompt, model, or architecture changes —
Batch 2 of `Docs/summary-quality-roadmap.md`. Runs the REAL pipeline code
(`run_meeting_summarization`, `docket_ingest.summarize_filing`) over frozen
production cases with only the edges swapped: no Postgres, no images, no DB
prompt overrides (hermetic by design), no network beyond the Anthropic API.

## Setup

```bash
export ANTHROPIC_API_KEY=…        # locally: poolside-legacy/.env
# prod pulls (fetch/mine only) additionally need:
export EVAL_PROD_DATABASE_URL=…   # Railway Postgres DATABASE_PUBLIC_URL
```

## Flow

```bash
# 1. Freeze the golden inputs (once; re-running skips existing files)
python -m tools.eval.fetch_golden

# 2. Baseline run — current working-tree prompts + repo model config
python -m tools.eval.runner --run-id baseline

# 3. A variant: pinned prompt set from any git ref, and/or model overrides
python -m tools.eval.runner --run-id pre-batch1 --prompts-ref 716e594
python -m tools.eval.runner --run-id fable-l3 --model meeting_model=claude-fable-5

# 4. Score
python -m tools.eval.score --run baseline                  # mechanical, free
python -m tools.eval.score --run baseline --judge          # + opus-5 rubric
python -m tools.eval.score --run fable-l3 --pairwise baseline   # A/B duel

# 5. Human blind test (the June-MC pattern)
python -m tools.eval.blind --a baseline --b fable-l3 \
    --case iso_meeting_june_mc --out ~/Desktop/blind_mc

# 6. Regression seeds from analyst edits (curate lessons by hand)
python -m tools.eval.mine_regressions
```

## Cases

See `cases.py` — 10 frozen prod cases: five ISO items (thin admin → 11-doc
vote package, MC/TC/PAC), the full June 2026 MC meeting (18 items — this is
the expensive case, ≈$2–3/run; exclude with `--cases`), and four FERC
filings covering the initial/order/full-comment/brief-motion tiers. Golden
payloads are committed so baselines replay forever; `fetch_golden` warns if
prod content has drifted from a frozen sha and never overwrites without
`--force`.

## Judging rubric

`judge_prompt.md`: faithfulness, decision-relevant completeness,
context-sufficiency for an energy-literate non-follower, actionability,
format — 1–5 each plus named defects. The meeting briefing is judged against
the item summaries it was synthesized from; items/filings against the frozen
source text (capped at `--source-cap`, default 60k chars). Pairwise mode
runs both orderings and only counts order-consistent verdicts as decisive.

## Cost guide

Mechanical scoring is free. A full 10-case run ≈ $4–7 (dominated by the June
MC meeting case); judging ≈ $0.15–0.30/case; pairwise ≈ 2× judge. The
Fable-vs-Opus L3 comparison from the roadmap is: baseline vs
`--model meeting_model=claude-fable-5` (optionally
`--model meeting_max_tokens=98304`), scored `--pairwise` on
`iso_meeting_june_mc` plus the two opus-tier FERC cases.
