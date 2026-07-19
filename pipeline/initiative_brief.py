"""
pipeline/initiative_brief.py — per-initiative "story so far" synthesis.

Turns every agenda item tagged with one initiative code (CAR-SA, GISWG, ...)
into a single narrative brief: what the initiative is, how it has moved
across committees and meetings, where it stands, and what comes next. Works
entirely from stored item summaries — no document re-reads, no images — so
it is one text-only LLM call.

Status lives on the initiative_briefs row (monthly_roundups pattern):
  draft → generating → complete | error
The row also snapshots source_item_count / source_latest_meeting_date so the
UI can flag the brief as stale once new tagged items land.
"""
from __future__ import annotations

import logging

import pipeline.db as db
from pipeline.roundup import strip_image_refs
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

PROMPT_SLUG = "initiative_brief_prompt"

# Per-item ceiling keeps a 50-item initiative inside a sane context window;
# the tail of a long detailed summary is the least load-bearing part.
_MAX_ITEM_CHARS = 5000


def _fmt_item_header(i: int, total: int, item: dict) -> str:
    bits = [
        f"=== ITEM {i} of {total}",
        f"{item.get('type_short', '?')} meeting {item.get('meeting_date', '?')}",
        f"agenda {item.get('item_id') or '?'}: {item.get('item_title') or 'Untitled'}",
    ]
    who = item.get("presenter")
    if who:
        org = item.get("organization")
        bits.append(f"presented by {who}" + (f" ({org})" if org else ""))
    if item.get("vote_status"):
        bits.append(f"vote: {item['vote_status']}")
    return " — ".join(bits) + " ==="


def _item_body(item: dict) -> str:
    body = (item.get("summary_detailed") or item.get("summary_one_line") or "").strip()
    if not body:
        return "(No summary available for this item yet.)"
    body = strip_image_refs(body)
    if len(body) > _MAX_ITEM_CHARS:
        body = body[:_MAX_ITEM_CHARS].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated)"
    return body


def build_brief_prompt(tag: dict, items: list[dict]) -> str:
    """Assemble the full prompt: template + the initiative's items oldest-first.

    Raises ValueError when the template is missing — callers surface that as
    an error status rather than silently generating without instructions.
    """
    template = load_prompt(PROMPT_SLUG)
    if not template:
        raise ValueError(f"Prompt template '{PROMPT_SLUG}' not found")

    # get_initiative_items returns newest-first for the UI; a story reads
    # oldest-first.
    ordered = sorted(items, key=lambda r: (str(r.get("meeting_date") or ""),
                                           str(r.get("item_id") or "")))
    blocks = []
    total = len(ordered)
    for i, item in enumerate(ordered, start=1):
        blocks.append(f"{_fmt_item_header(i, total, item)}\n\n{_item_body(item)}")

    code = tag.get("name") or ""
    desc = (tag.get("description") or "").strip()
    context_block = (
        f"[INITIATIVE]\n\n"
        f"Code: {code}\n"
        + (f"Known description: {desc}\n" if desc else "")
        + f"Tagged appearances: {total}\n\n"
        f"[TAGGED AGENDA ITEMS — OLDEST FIRST]\n\n"
        + "\n\n".join(blocks)
    )

    if "[ITEMS]" in template:
        prompt = template.replace("[ITEMS]", context_block)
    else:
        prompt = template + "\n\n" + context_block

    general_context = load_prompt("general_context_prompt")
    if general_context:
        prompt = general_context + "\n\n" + prompt
    return prompt


def run_initiative_brief(tag_id: int, client=None) -> bool:
    """Generate the brief for an existing initiative_briefs row.
    Returns True on success; on failure the row carries status='error'."""
    tag = db.get_tag(tag_id)
    if not tag:
        db.update_initiative_brief(tag_id, status="error",
                                   error_message="Initiative tag not found")
        return False

    code = tag.get("name") or f"tag {tag_id}"
    db.update_initiative_brief(tag_id, status="generating", error_message=None)
    logger.info("Initiative brief %s: collecting items...", code)

    try:
        items = db.get_initiative_items(tag_id)
        if not items:
            raise ValueError(f"No agenda items tagged {code}")

        prompt = build_brief_prompt(tag, items)

        cfg = load_model_config()
        model = (cfg.get("initiative_brief_model")
                 or cfg.get("roundup_model")
                 or cfg.get("meeting_model", OPUS))
        max_tokens = int(cfg.get("initiative_brief_max_tokens") or 16384)

        if client is None:
            client = make_client()

        logger.info("Initiative brief %s: synthesizing %d item(s) with %s...",
                    code, len(items), model)
        with capture_usage() as usage_log:
            result = call_llm(client, model, prompt, max_tokens=max_tokens,
                              label=f"initiative_brief {code}")
        totals = totals_from_usage_log(usage_log)

        result = clean_output(result)
        if not result.strip():
            raise ValueError("LLM returned an empty brief")

        dates = [i.get("meeting_date") for i in items if i.get("meeting_date")]
        db.update_initiative_brief(
            tag_id,
            status="complete",
            brief_md=result,
            model_id=model,
            error_message=None,
            input_tokens=int(totals.get("input_tokens", 0)),
            output_tokens=int(totals.get("output_tokens", 0)),
            cost_usd=float(totals.get("cost_usd", 0.0)),
            source_item_count=len(items),
            source_latest_meeting_date=max(dates) if dates else None,
        )
        logger.info("Initiative brief %s: complete.", code)
        return True

    except Exception as exc:
        logger.exception("Initiative brief %s failed: %s", code, exc)
        db.update_initiative_brief(tag_id, status="error",
                                   error_message=str(exc))
        return False
