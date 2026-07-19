"""Ask Poolside — cited Q&A over the summary corpus.

Retrieval is the shared full-text layer (api/services/search.py) with an
OR-relaxed fallback for natural-language questions; generation is one
text-only LLM call over the numbered source summaries, instructed to cite
`[n]` and to say when the record doesn't answer the question. Synchronous:
the route returns the finished answer (no jobs table, no polling) — a
question over ~12 summaries is a single fast call.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from pipeline import db
from pipeline.roundup import strip_image_refs
from pipeline.summarizer import (
    HAIKU,
    call_llm,
    capture_usage,
    clean_output,
    load_model_config,
    load_prompt,
    make_client,
    totals_from_usage_log,
)

from ..auth import current_user
from ..services.search import retrieve_for_question

log = logging.getLogger("poolside.ask")

router = APIRouter(prefix="/api/ask", tags=["ask"])

PROMPT_SLUG = "ask_prompt"

# Retrieval breadth and per-source ceiling. Sources beyond ~12 add latency
# and prompt cost faster than answer quality; 6k chars keeps a long briefing
# from crowding out the other sources.
_MAX_SOURCES = 12
_MAX_SOURCE_CHARS = 6000

_NO_RESULTS_ANSWER = (
    "I couldn't find anything in the meeting summaries matching that "
    "question. Try different terms, or check whether the relevant meeting "
    "has been summarized yet."
)


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    type_short: str | None = None
    from_date: date | None = None
    to_date: date | None = None


def _source_label(n: int, hit: dict) -> str:
    """One-line provenance header the model sees above each source body."""
    bits = [f"[{n}] {hit.get('type_short', '?')} meeting {hit.get('meeting_date', '?')}"]
    if hit.get("entity_type") == "agenda_item":
        item = hit.get("item_id") or "?"
        title = hit.get("item_title") or "Untitled item"
        bits.append(f"agenda {item}: {title}")
        if hit.get("presenter"):
            org = hit.get("organization")
            bits.append(f"presented by {hit['presenter']}"
                        + (f" ({org})" if org else ""))
    else:
        bits.append("meeting briefing")
    return " — ".join(bits)


def _source_body(hit: dict) -> str:
    summ = db.get_current_summary(hit["entity_type"], hit["entity_id"]) or {}
    body = (summ.get("detailed") or summ.get("one_line") or "").strip()
    body = strip_image_refs(body)
    if len(body) > _MAX_SOURCE_CHARS:
        body = body[:_MAX_SOURCE_CHARS].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated)"
    return body or "(No summary text.)"


def build_ask_prompt(question: str, hits: list[dict]) -> str:
    """Template + numbered sources + the question. Raises ValueError when
    the template is missing — callers surface that instead of free-styling."""
    template = load_prompt(PROMPT_SLUG)
    if not template:
        raise ValueError(f"Prompt template '{PROMPT_SLUG}' not found")

    blocks = []
    for n, hit in enumerate(hits, start=1):
        blocks.append(f"=== SOURCE {_source_label(n, hit)} ===\n\n{_source_body(hit)}")
    sources_block = "\n\n".join(blocks)

    prompt = template.replace("[QUESTION]", question)
    if "[SOURCES]" in prompt:
        prompt = prompt.replace("[SOURCES]", sources_block)
    else:
        prompt = prompt + "\n\n" + sources_block

    general_context = load_prompt("general_context_prompt")
    if general_context:
        prompt = general_context + "\n\n" + prompt
    return prompt


def _serialize_source(n: int, hit: dict) -> dict[str, Any]:
    return {
        "n": n,
        "entity_type": hit.get("entity_type"),
        "entity_id": hit.get("entity_id"),
        "meeting_id": hit.get("meeting_id"),
        "meeting_title": hit.get("meeting_title"),
        "meeting_date": hit.get("meeting_date"),
        "venue": hit.get("venue"),
        "type_short": hit.get("type_short"),
        "item_id": hit.get("item_id"),
        "item_title": hit.get("item_title"),
        "snippet": hit.get("snippet"),
    }


@router.post("")
def ask(
    body: AskBody = Body(...),
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    question = body.question.strip()
    filters: dict[str, Any] = {}
    if body.type_short:
        filters["type_short"] = body.type_short
    if body.from_date:
        filters["from_date"] = body.from_date
    if body.to_date:
        filters["to_date"] = body.to_date

    hits = retrieve_for_question(question, limit=_MAX_SOURCES, **filters)

    if not hits:
        return {
            "question": question,
            "answer_md": _NO_RESULTS_ANSWER,
            "sources": [],
            "model_id": None,
            "cost_usd": None,
        }

    prompt = build_ask_prompt(question, hits)

    cfg = load_model_config()
    model = (cfg.get("ask_model")
             or cfg.get("item_model", HAIKU))
    max_tokens = int(cfg.get("ask_max_tokens") or 4096)

    client = make_client()
    log.info("ask: %d source(s), model %s: %r", len(hits), model, question[:80])
    with capture_usage() as usage_log:
        answer = call_llm(client, model, prompt, max_tokens=max_tokens,
                          label=f"ask: {question[:40]}")
    totals = totals_from_usage_log(usage_log)

    return {
        "question": question,
        "answer_md": clean_output(answer),
        "sources": [_serialize_source(n, h) for n, h in enumerate(hits, start=1)],
        "model_id": model,
        "cost_usd": float(totals.get("cost_usd", 0.0)) or None,
    }
