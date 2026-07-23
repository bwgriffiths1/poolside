"""FERC docket "state of play" — the docket-level rollup.

Synthesizes every filing summary on a docket (plus the intervenor roster
and the procedural timeline) into one narrative: what was filed, who
weighed in and which way, what the Commission has done, what's pending.
One text-only LLM call, initiative_brief.py's shape.

Unlike initiative briefs, the output lives in
summary_versions(entity_type='docket') rather than a status row, so the
existing editor / version-history / restore machinery applies:

  - Regeneration loads the CURRENT version first — including any manual
    user edits — and feeds it back as [PRIOR STATE OF PLAY] with
    instructions to carry user corrections forward (the roundup
    prior-context pattern).
  - The new version is created as a draft then approved, superseding the
    prior. Nothing is lost: every earlier version stays restorable.

Generation status/progress/cost ride on the docket_jobs row (mode='brief');
this module just does the work and returns totals.
"""
from __future__ import annotations

import logging

import pipeline.db as db
from pipeline.docket_ingest import author_orgs
from pipeline.summarizer import (
    OPUS,
    call_llm,
    capture_usage,
    clean_output,
    load_model_config,
    load_prompt,
    make_client,
    totals_from_usage_log,
)

logger = logging.getLogger(__name__)

PROMPT_SLUG = "ferc_state_of_play_prompt"

# Per-filing ceiling keeps a 100-filing docket inside a sane context window;
# the tail of a long detailed summary is the least load-bearing part. The
# two anchor documents (initial filing, orders) get double the room — their
# summaries are deliberately deeper and carry the docket's spine.
_MAX_FILING_CHARS = 6000
_MAX_ANCHOR_CHARS = 12000
# The prior state-of-play is context, not source material — excerpt it.
_MAX_PRIOR_CHARS = 20_000


def _fmt_filing_header(i: int, total: int, f: dict) -> str:
    who = "; ".join(author_orgs(f.get("filing_parties"))) or "?"
    bits = [f"=== FILING {i} of {total}"]
    # Flag the two anchor documents so the synthesis leans on them: the
    # initial filing is the proposal everything reacts to; orders are how
    # FERC decides. The rest is responsive.
    if f.get("role") == "initial":
        bits.append("★ THE INITIAL FILING — the proposal this docket is about")
    elif f.get("role") == "order":
        bits.append("★ FERC ORDER — a Commission decision")
    bits += [
        f"{f.get('filed_date') or f.get('issued_date') or '?'}",
        f"{f.get('document_class') or '?'} / {f.get('document_type') or '?'}",
        f"by {who}",
        f"accession {f.get('accession_number')}",
    ]
    if f.get("ferc_cite"):
        bits.append(f"cite {f['ferc_cite']}")
    return " — ".join(bits) + " ==="


def _filing_body(f: dict) -> str:
    body = (f.get("summary_detailed") or f.get("summary_one_line") or "").strip()
    if not body:
        return "(No summary available for this filing.)"
    cap = (_MAX_ANCHOR_CHARS if f.get("role") in ("initial", "order")
           else _MAX_FILING_CHARS)
    if len(body) > cap:
        body = body[:cap].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated)"
    return body


def _intervenor_block(filings: list[dict]) -> str:
    """Deduped roster from the Intervention filings, chronological."""
    seen: set[str] = set()
    rows: list[str] = []
    for f in sorted(filings, key=lambda r: str(r.get("filed_date") or "")):
        if f.get("document_class") != "Intervention":
            continue
        for org in author_orgs(f.get("filing_parties")):
            if org not in seen:
                seen.add(org)
                rows.append(f"- {org} ({f.get('filed_date') or '?'})")
    return "\n".join(rows) if rows else "(none recorded)"


def _timeline_block(filings: list[dict]) -> str:
    """One line per filing, oldest first — the procedural record including
    skip-tier items (notices etc.) that carry no summary. The doc-less
    intervention wave stays out (the roster covers it); an Intervention
    paired with a protest (doc-ful, substantive) earns its line."""
    rows = []
    for f in sorted(filings, key=lambda r: str(r.get("filed_date")
                                               or r.get("issued_date") or "")):
        if (f.get("document_class") == "Intervention"
                and f.get("treatment") == "skip"):
            continue
        who = "; ".join(author_orgs(f.get("filing_parties"))) or "?"
        rows.append(f"- {f.get('filed_date') or f.get('issued_date') or '?'} — "
                    f"[{f.get('document_class') or '?'}] {who}: "
                    f"{(f.get('description') or '')[:140]}")
    return "\n".join(rows) if rows else "(no filings)"


def _pending_deadlines(filings: list[dict]) -> str:
    rows = []
    for f in filings:
        for label, key in (("Comments due", "comments_due_date"),
                           ("Response due", "response_due_date")):
            if f.get(key):
                rows.append(f"- {label} {f[key]} (on {f.get('accession_number')}: "
                            f"{(f.get('description') or '')[:100]})")
    return "\n".join(rows) if rows else "(none recorded)"


def build_brief_prompt(docket: dict, filings: list[dict],
                       prior_md: str | None) -> str:
    """Assemble the full prompt: template + docket context + summaries
    oldest-first (+ the prior, possibly user-edited, state of play)."""
    template = load_prompt(PROMPT_SLUG)
    if not template:
        raise ValueError(f"Prompt template '{PROMPT_SLUG}' not found")

    summarized = [f for f in filings
                  if f.get("summary_detailed") or f.get("summary_one_line")]
    ordered = sorted(summarized, key=lambda r: (str(r.get("filed_date")
                                                    or r.get("issued_date") or ""),
                                                str(r.get("accession_number"))))
    total = len(ordered)
    blocks = [f"{_fmt_filing_header(i, total, f)}\n\n{_filing_body(f)}"
              for i, f in enumerate(ordered, start=1)]

    context_block = (
        f"[DOCKET]\n\n"
        f"Number: {docket.get('docket_number')}\n"
        + (f"Title: {docket['title']}\n" if docket.get("title") else "")
        + (f"Notes: {docket['notes']}\n" if docket.get("notes") else "")
        + f"\n[INTERVENORS — chronological]\n\n{_intervenor_block(filings)}\n"
        + "\n[PROCEDURAL TIMELINE — every filing, oldest first]\n\n"
        + f"{_timeline_block(filings)}\n"
        + f"\n[PENDING DEADLINES]\n\n{_pending_deadlines(filings)}\n"
        + "\n[FILING SUMMARIES — OLDEST FIRST]\n\n"
        + "\n\n".join(blocks)
    )

    if prior_md:
        excerpt = prior_md[:_MAX_PRIOR_CHARS]
        if len(prior_md) > _MAX_PRIOR_CHARS:
            excerpt = excerpt.rsplit("\n", 1)[0] + "\n…(truncated)"
        context_block += (
            "\n\n[PRIOR STATE OF PLAY — the previous version of this report; "
            "it may contain manual analyst edits and corrections. Preserve "
            "analyst-added framing, corrections, and emphasis wherever they "
            "remain accurate; update or extend them for the new filings "
            "above.]\n\n" + excerpt
        )

    if "[FILINGS]" in template:
        prompt = template.replace("[FILINGS]", context_block)
    else:
        prompt = template + "\n\n" + context_block

    general_context = load_prompt("general_context_prompt")
    if general_context:
        prompt = general_context + "\n\n" + prompt
    return prompt


def run_docket_brief(docket_id: int, client=None,
                     progress=logger.info) -> dict:
    """Generate (or regenerate) the state of play for one docket.

    Returns {"summary_id", "input_tokens", "output_tokens", "cost_usd"}.
    Raises on failure — the caller (job service) owns error bookkeeping."""
    docket = db.get_docket(docket_id)
    if not docket:
        raise ValueError(f"Docket {docket_id} not found")
    number = docket.get("docket_number") or f"docket {docket_id}"

    progress(f"Collecting filings for {number}…")
    filings = db.list_docket_filings(docket_id)
    if not any(f.get("summary_detailed") or f.get("summary_one_line")
               for f in filings):
        raise ValueError(f"No filing summaries on {number} yet — run a sync first")

    prior = db.get_current_summary("docket", docket_id)
    prior_md = (prior or {}).get("detailed")

    prompt = build_brief_prompt(docket, filings, prior_md)

    cfg = load_model_config()
    model = (cfg.get("ferc_state_of_play_model")
             or cfg.get("meeting_model", OPUS))
    max_tokens = int(cfg.get("ferc_state_of_play_max_tokens") or 16384)

    if client is None:
        client = make_client()

    progress(f"Synthesizing state of play for {number} with {model}…")
    with capture_usage() as usage_log:
        result = call_llm(client, model, prompt, max_tokens=max_tokens,
                          label=f"ferc_state_of_play {number}")
    totals = totals_from_usage_log(usage_log)

    result = clean_output(result)
    if not result.strip():
        raise ValueError("LLM returned an empty state of play")

    row = db.create_summary_version(
        entity_type="docket",
        entity_id=docket_id,
        one_line=None,
        detailed=result,
        model_id=model,
        is_manual=False,
        status="draft",
        created_by="system",
    )
    # Make it current; the prior version (user edits included) stays in
    # history and remains restorable.
    db.approve_summary_version(row["id"])
    logger.info("State of play for %s: complete (v%s).", number, row["version"])
    return {"summary_id": row["id"],
            "input_tokens": int(totals.get("input_tokens", 0)),
            "output_tokens": int(totals.get("output_tokens", 0)),
            "cost_usd": float(totals.get("cost_usd", 0.0))}
