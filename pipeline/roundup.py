"""
pipeline/roundup.py — Monthly cross-committee roundup ("state of play").

Synthesizes every committee briefing in a (venue, calendar month) into a
single ISO-wide report, with the most recent prior roundup fed back in as
continuity context. Works entirely from stored briefing markdown — no
document re-reads, no images — so it is one text-only LLM call.

Pipeline:
  1. Collect the month's meeting briefings (image refs stripped)
  2. Excerpt the latest prior complete roundup for [PRIOR CONTEXT]
  3. Build prompt from monthly_roundup_prompt.md
  4. One LLM call (model_config "roundup_model", else "meeting_model")
  5. Store result + token usage on the monthly_roundups row

Status lives on the row (deep_dive_reports pattern):
  draft → generating → complete | error
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Callable

import pipeline.db_new as db
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

PROMPT_SLUG = "monthly_roundup_prompt"

# Briefing markdown stores kept images as "**Figure:** caption" lines followed
# by "<!-- image_id:N -->" comments (see summarizer.replace_keep_images_inline);
# plain markdown images may appear in hand-edited briefings. All are noise for
# a text-only synthesis pass.
_IMAGE_COMMENT_RE = re.compile(r"<!--\s*image_id:\d+\s*-->")
_FIGURE_LINE_RE = re.compile(r"^\*\*Figure:\*\*.*$", re.MULTILINE)
_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def strip_image_refs(md: str) -> str:
    """Remove inline image markup/captions and collapse the blank runs left behind."""
    md = _IMAGE_COMMENT_RE.sub("", md or "")
    md = _FIGURE_LINE_RE.sub("", md)
    md = _IMAGE_MD_RE.sub("", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def month_label(month: date) -> str:
    """'2026-06-01' → 'June 2026'."""
    return month.strftime("%B %Y")


def _fmt_meeting_dates(row: dict) -> str:
    start = str(row.get("meeting_date") or "")
    end = row.get("end_date")
    return f"{start} to {end}" if end and str(end) != start else start


def roundup_excerpt(md: str, max_chars: int = 6000) -> str:
    """The Key Takeaways + Executive Summary + workstream portion of a prior
    roundup (everything before the per-committee recap), lightly truncated —
    mirrors summarizer._briefing_summary_excerpt for briefings."""
    low = (md or "").lower()
    cut = low.find("## committee roundup")
    excerpt = (md[:cut] if cut != -1 else md or "").strip()
    excerpt = excerpt.lstrip("-").strip()  # drop a leading '---' rule
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated)"
    return excerpt


def collect_roundup_inputs(roundup: dict) -> tuple[list[dict], dict | None]:
    """(briefings for the roundup's month, latest prior complete roundup or None)."""
    briefings = db.get_month_briefings(roundup["venue_short"], roundup["month"])
    prior = db.get_latest_prior_roundup(roundup["venue_id"], roundup["month"])
    return briefings, prior


def build_roundup_prompt(roundup: dict, briefings: list[dict],
                         prior: dict | None) -> str:
    """Assemble the full prompt: template + [PRIOR CONTEXT] + month's briefings.

    Raises ValueError when the template is missing — callers surface that as
    an error status rather than silently generating without instructions.
    """
    template = load_prompt(PROMPT_SLUG)
    if not template:
        raise ValueError(f"Prompt template '{PROMPT_SLUG}' not found")

    label = month_label(roundup["month"])
    venue = roundup.get("venue_short") or ""

    if prior:
        exc = roundup_excerpt(prior.get("report_md") or "")
        prior_section = (
            f"### Prior roundup — {month_label(prior['month'])}\n\n{exc}"
            if exc else "None available."
        )
    else:
        prior_section = "None available."

    covered = "; ".join(
        f"{b['type_short']} {_fmt_meeting_dates(b)}" for b in briefings
    )
    blocks = []
    for i, b in enumerate(briefings, start=1):
        header = (
            f"=== BRIEFING {i} of {len(briefings)} — "
            f"{b.get('venue_short', venue)} {b.get('type_name') or b['type_short']} "
            f"({b['type_short']}), {_fmt_meeting_dates(b)} ==="
        )
        blocks.append(f"{header}\n\n{strip_image_refs(b.get('detailed') or '')}")

    context_block = (
        f"[PRIOR CONTEXT]\n\n{prior_section}\n\n---\n\n"
        f"[THIS MONTH'S BRIEFINGS]\n\n"
        f"Month under review: {label} ({venue})\n"
        f"Meetings covered: {covered}\n\n"
        + "\n\n".join(blocks)
    )

    if "[BRIEFINGS]" in template:
        prompt = template.replace("[BRIEFINGS]", context_block)
    else:
        prompt = template + "\n\n" + context_block

    general_context = load_prompt("general_context_prompt")
    if general_context:
        prompt = general_context + "\n\n" + prompt
    return prompt


def run_monthly_roundup(
    roundup_id: int,
    client=None,
    progress_fn: Callable[[str], None] | None = None,
) -> bool:
    """
    Generate the roundup for an existing monthly_roundups row.
    Returns True on success; on failure the row carries status='error'
    with error_message set.
    """
    def _progress(msg: str) -> None:
        logger.info("Roundup %d: %s", roundup_id, msg)
        try:
            db.update_monthly_roundup(roundup_id, progress_text=msg)
        except Exception:
            logger.exception("failed to write progress for roundup %d", roundup_id)
        if progress_fn:
            progress_fn(msg)

    roundup = db.get_monthly_roundup(roundup_id)
    if not roundup:
        logger.error("Roundup %d not found", roundup_id)
        return False

    label = month_label(roundup["month"])
    db.update_monthly_roundup(roundup_id, status="generating", error_message=None)
    _progress(f"Collecting {label} briefings...")

    try:
        briefings, prior = collect_roundup_inputs(roundup)
        if not briefings:
            raise ValueError(
                f"No briefings found for {roundup.get('venue_short', '')} {label}"
            )

        # Provenance first, so the UI can show sources while generating.
        db.set_roundup_meetings(roundup_id, [b["id"] for b in briefings])

        prompt = build_roundup_prompt(roundup, briefings, prior)

        cfg = load_model_config()
        model = (roundup.get("model_id")
                 or cfg.get("roundup_model")
                 or cfg.get("meeting_model", OPUS))
        max_tokens = int(cfg.get("roundup_max_tokens")
                         or cfg.get("meeting_max_tokens", 32768))

        if client is None:
            client = make_client()

        _progress(
            f"Synthesizing {len(briefings)} briefing(s)"
            + (f" + prior roundup ({month_label(prior['month'])})" if prior else "")
            + f" with {model}..."
        )

        with capture_usage() as usage_log:
            result = call_llm(client, model, prompt, max_tokens=max_tokens,
                              label=f"roundup {roundup_id} ({label})")
        totals = totals_from_usage_log(usage_log)

        result = clean_output(result)
        if not result.strip():
            raise ValueError("LLM returned an empty roundup")

        db.update_monthly_roundup(
            roundup_id,
            status="complete",
            report_md=result,
            model_id=model,
            error_message=None,
            input_tokens=int(totals.get("input_tokens", 0)),
            output_tokens=int(totals.get("output_tokens", 0)),
            cost_usd=float(totals.get("cost_usd", 0.0)),
        )
        _progress("Roundup complete.")
        return True

    except Exception as exc:
        logger.exception("Roundup %d failed: %s", roundup_id, exc)
        db.update_monthly_roundup(roundup_id, status="error",
                                  error_message=str(exc))
        return False
