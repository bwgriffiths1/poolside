"""Ask Poolside — cited Q&A over the summary corpus, and optionally the
underlying documents.

Retrieval is the shared full-text layer (api/services/search.py) with an
OR-relaxed fallback for natural-language questions; generation is one
text-only LLM call over the numbered sources, instructed to cite `[n]`
and to say when the record doesn't answer the question. Synchronous: the
route returns the finished answer (no jobs table, no polling).

Two axes beyond the original meeting-summary search:

* SCOPE — `corpus` all | meetings | dockets, narrowed further by docket
  mentions: `@ER26-925` anywhere in the question (or `docket_numbers` in
  the body) restricts retrieval to those tracked dockets, and each scoped
  docket's state of play is always source [1..k] so a vague question
  ("where does this stand?") still gets the right context.
* DEPTH — `depth` summaries | documents. The documents tier adds verbatim
  passages from the underlying text (documents.raw_content, FERC filing
  files) via migration 022's full-text index, labelled as excerpts so the
  model can quote figures and language the summaries compressed away.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Callable, Literal

from fastapi import APIRouter, Body, Depends, HTTPException
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
from ..services import ask_jobs as jobs_service
from ..services import search as search_svc
from ..services.search import (
    extract_passages,
    retrieve_docket_summaries,
    retrieve_document_hits,
    retrieve_for_question,
)

log = logging.getLogger("poolside.ask")

router = APIRouter(prefix="/api/ask", tags=["ask"])

PROMPT_SLUG = "ask_prompt"

Detail = Literal["brief", "standard", "deep"]
DETAIL_LEVELS: list[str] = ["brief", "standard", "deep"]
DEFAULT_DETAIL = "standard"

# Retrieval breadth and per-source ceilings, by detail level. `brief` is
# the original tuning (≈12 summaries × 6k chars); `deep` trades prompt
# cost for coverage — a "positions by party" question on a busy docket
# needs every filing in front of the model, not the 12 best keyword
# matches. Document passages stay supplements to the summaries.
#   sources        — cap on summary sources (state of play + ranked hits,
#                    or the full docket roster at deep)
#   source_chars   — head-truncation of one summary body
#   doc_sources    — verbatim passages at documents depth
#   passage_chars  — length of one passage
#   budget_chars   — total budget for the sources block; per-source bodies
#                    shrink toward `min_source_chars` as the count grows
#   roster         — deep only: include every summarized filing of a scoped
#                    docket (no fixed count — the budget is the limit)
_RETRIEVAL: dict[str, dict[str, int]] = {
    "brief":    {"sources": 12, "source_chars": 6000,  "min_source_chars": 6000,
                 "doc_sources": 6, "passage_chars": 3200, "budget_chars": 120_000},
    "standard": {"sources": 16, "source_chars": 8000,  "min_source_chars": 8000,
                 "doc_sources": 6, "passage_chars": 3200, "budget_chars": 160_000},
    # ~100k tokens of sources: enough for ~60 full filing summaries, or the
    # whole roster of a monster docket at the floor length with the tail
    # named as omitted. Input cost ≈ $0.30 Sonnet / $0.50 Opus / $1 Fable.
    "deep":     {"sources": 400, "source_chars": 12000, "min_source_chars": 2200,
                 "doc_sources": 8, "passage_chars": 4000, "budget_chars": 400_000},
}
# A scoped docket's state of play is the frame for the whole answer, so it
# is exempt from the per-source cap at every level; this is a safety
# ceiling only.
_MAX_STATE_OF_PLAY_CHARS = 16000
# A deep memo needs some deliberation; applied only when the request left
# effort unset.
_DEEP_EFFORT_FLOOR = "medium"
# Output budget. On the Claude 5 models adaptive thinking counts against
# max_tokens, so a small cap doesn't save money — it forces the
# summarizer's truncate-and-double retry loop (three multi-minute calls
# for one answer; that's what stranded Ben's first deep ER26-3515 run).
# Start high enough that a memo finishes first time; the higher efforts
# think longer, so they get more.
_ASK_MAX_TOKENS = 32768
_ASK_MAX_TOKENS_HIGH_EFFORT = 65536


def ask_max_tokens(effort: str, cfg: dict | None = None) -> int:
    """Output cap for one answer; `ask_max_tokens` in model_config is a
    floor an admin can raise, never a way to reintroduce the retry loop."""
    base = (_ASK_MAX_TOKENS_HIGH_EFFORT if effort in ("xhigh", "max")
            else _ASK_MAX_TOKENS)
    configured = int((cfg or {}).get("ask_max_tokens") or 0)
    return max(base, configured)

_NO_RESULTS_ANSWER = (
    "I couldn't find anything in the {corpus} matching that question. "
    "Try different terms, or check whether the relevant {unit} has been "
    "summarized yet."
)

# "@ER26-925", "@er26-925-000", "@EL25-12" — FERC's prefix set is open
# (see docket_ingest.normalize_docket_number), so match the shape, not a
# list. Sub-docket suffixes (-000, -001) map to the tracked family.
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z]{1,3}\d{2}-\d+(?:-\d{1,3})?)\b")

Corpus = Literal["all", "meetings", "dockets"]
Depth = Literal["summaries", "documents"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Models the Ask UI may pick from. An allowlist, not free text: the model
# id goes straight into the API call and the pricing table. `effort` marks
# whether the model takes output_config.effort (Haiku 4.5 rejects it with a
# 400 — summarizer._effort_kwargs already drops it, the flag just lets the
# UI grey the control out).
ASK_MODELS: list[dict[str, Any]] = [
    {"id": "claude-sonnet-5", "label": "Sonnet 5",
     "note": "Fast; the default", "effort": True},
    {"id": "claude-opus-5", "label": "Opus 5",
     "note": "Deeper reasoning, ~2.5× the cost", "effort": True},
    {"id": "claude-fable-5-1", "label": "Fable 5.1",
     "note": "Most capable, ~5× the cost, slower", "effort": True},
    {"id": "claude-haiku-4-5", "label": "Haiku 4.5",
     "note": "Cheapest; no effort control", "effort": False},
]
_ASK_MODEL_IDS = {m["id"] for m in ASK_MODELS}
EFFORT_LEVELS: list[str] = ["low", "medium", "high", "xhigh", "max"]
# Ask is interactive and reads already-summarized text, so it opts out of
# the model family's default (max/high) effort — deep thinking here mostly
# adds latency. The dropdown lets the analyst raise it per question.
DEFAULT_EFFORT = "low"


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    corpus: Corpus = "all"
    depth: Depth = "summaries"
    docket_numbers: list[str] = Field(default_factory=list, max_length=10)
    model: str | None = None
    effort: Effort | None = None
    detail: Detail = DEFAULT_DETAIL
    # Follow-up: re-use this earlier exchange's sources (same [n] numbers)
    # and carry its Q&A into the prompt instead of retrieving afresh.
    parent_id: int | None = None
    type_short: str | None = None
    from_date: date | None = None
    to_date: date | None = None


def default_ask_model() -> str:
    cfg = load_model_config()
    return cfg.get("ask_model") or cfg.get("item_model", HAIKU)


def resolve_model(requested: str | None) -> str:
    """The model to call: the request's pick (allowlisted) or the config
    default. A model outside the allowlist is a 422, not a silent fallback."""
    if requested is None or requested == "":
        return default_ask_model()
    if requested not in _ASK_MODEL_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"model must be one of {sorted(_ASK_MODEL_IDS)}")
    return requested


# ── Scope ────────────────────────────────────────────────────────────────

def normalize_mention(raw: str) -> str:
    """'er26-925-000' → 'ER26-925': uppercase, sub-docket suffix dropped."""
    num = (raw or "").strip().lstrip("@").upper()
    m = re.fullmatch(r"([A-Z]{1,3}\d{2}-\d+)(?:-\d{1,3})?", num)
    return m.group(1) if m else num


def parse_mentions(question: str) -> tuple[str, list[str]]:
    """Split `@docket` mentions out of a question. Returns the question
    with the mentions removed (whitespace tidied) and the normalized docket
    numbers in first-seen order, de-duplicated."""
    numbers: list[str] = []
    for m in _MENTION_RE.finditer(question or ""):
        num = normalize_mention(m.group(1))
        if num not in numbers:
            numbers.append(num)
    cleaned = _MENTION_RE.sub(" ", question or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:")
    return cleaned, numbers


def resolve_scope(body: AskBody) -> dict[str, Any]:
    """Turn the body + inline mentions into a concrete retrieval scope:
    which corpus, which docket ids (resolved against tracked dockets),
    which mentions couldn't be resolved, and the question with mentions
    stripped for retrieval."""
    cleaned, mentioned = parse_mentions(body.question)
    numbers: list[str] = []
    for raw in list(body.docket_numbers) + mentioned:
        num = normalize_mention(raw)
        if num and num not in numbers:
            numbers.append(num)

    dockets: list[dict] = []
    unknown: list[str] = []
    for num in numbers:
        row = db.get_docket_by_number(num)
        if row:
            dockets.append({"id": row["id"], "docket_number": row["docket_number"],
                            "title": row.get("title")})
        else:
            unknown.append(num)

    corpus: str = body.corpus
    if dockets:
        corpus = "dockets"   # a named docket is the scope, whatever the toggle says
    return {
        "corpus": corpus,
        "depth": body.depth,
        "dockets": dockets,
        "docket_ids": [d["id"] for d in dockets],
        "unknown_dockets": unknown,
        "retrieval_question": cleaned or body.question.strip(),
    }


# ── Source gathering ─────────────────────────────────────────────────────

def _seed_state_of_play(docket: dict) -> dict | None:
    """A scoped docket's current state of play as a hit, match or not."""
    summ = db.get_current_summary("docket", docket["id"])
    if not summ or not (summ.get("detailed") or summ.get("one_line")):
        return None
    lead = (summ.get("one_line") or summ.get("detailed") or "").strip()
    return {
        "entity_type": "docket",
        "entity_id": docket["id"],
        "docket_id": docket["id"],
        "docket_number": docket["docket_number"],
        "docket_title": docket.get("title"),
        "summary_date": summ.get("created_at"),
        "snippet": search_svc._escape_snippet(lead[:200]),
        "tier": "summary",
        "rank": None,
    }


def _roster_hits(docket_ids: list[int]) -> list[dict]:
    """Every substantive, summarized filing on the scoped dockets as
    summary hits, newest first (deep detail only)."""
    out = []
    for row in db.list_docket_filing_summaries(docket_ids):
        out.append({
            "entity_type": "docket_filing",
            "entity_id": row["filing_id"],
            "docket_id": row["docket_id"],
            "docket_number": row.get("docket_number"),
            "docket_title": row.get("docket_title"),
            "filing_id": row["filing_id"],
            "accession_number": row.get("accession_number"),
            "document_class": row.get("document_class"),
            "document_type": row.get("document_type"),
            "description": row.get("description"),
            "filed_date": row.get("filed_date"),
            "filing_parties": row.get("filing_parties"),
            "snippet": search_svc._escape_snippet(
                (row.get("description") or "")[:200]),
            "tier": "summary",
            "rank": None,
        })
    return out


def gather_sources(scope: dict[str, Any], detail: str = DEFAULT_DETAIL,
                   **filters) -> list[dict]:
    """Ranked, de-duplicated sources for a resolved scope: seeded
    state-of-play for scoped dockets, then — at deep detail — every
    summarized filing on those dockets, then summary hits from the chosen
    corpus (meeting and docket summaries interleaved by rank when both
    apply), then — at documents depth — verbatim passages."""
    caps = _RETRIEVAL.get(detail, _RETRIEVAL[DEFAULT_DETAIL])
    max_sources = caps["sources"]
    q = scope["retrieval_question"]
    corpus = scope["corpus"]
    docket_ids = scope["docket_ids"] or None

    hits: list[dict] = []
    seen: set[tuple] = set()

    def add(h: dict, cap: int) -> None:
        key = (h["entity_type"], h["entity_id"])
        if key in seen or len(hits) >= cap:
            return
        seen.add(key)
        hits.append(h)

    for d in scope["dockets"]:
        seeded = _seed_state_of_play(d)
        if seeded:
            add(seeded, max_sources)

    if detail == "deep" and docket_ids:
        for h in _roster_hits(docket_ids):
            add(h, max_sources)

    summary_hits: list[dict] = []
    if corpus in ("all", "meetings"):
        summary_hits += retrieve_for_question(q, limit=max_sources, **filters)
    if corpus in ("all", "dockets"):
        summary_hits += retrieve_docket_summaries(q, limit=max_sources,
                                                  docket_ids=docket_ids)
    if corpus == "all":
        summary_hits.sort(key=lambda h: float(h.get("rank") or 0.0), reverse=True)
    for h in summary_hits:
        h.setdefault("tier", "summary")
        add(h, max_sources)

    if scope["depth"] == "documents":
        doc_hits = retrieve_document_hits(
            q, limit=caps["doc_sources"], corpus=corpus, docket_ids=docket_ids,
            passage_chars=caps["passage_chars"], **filters,
        )
        for h in doc_hits:
            add(h, max_sources + caps["doc_sources"])
    return hits


# ── Prompt assembly ──────────────────────────────────────────────────────

def _authors(hit: dict) -> str:
    if hit.get("authors"):
        return str(hit["authors"])
    parties = hit.get("filing_parties") or []
    orgs = [p.get("org") for p in parties
            if isinstance(p, dict) and p.get("type") == "AUTHOR" and p.get("org")]
    if not orgs:
        orgs = [p.get("org") for p in parties if isinstance(p, dict) and p.get("org")]
    orgs = list(dict.fromkeys(orgs))
    if len(orgs) > 3:
        return "; ".join(orgs[:3]) + f" (+{len(orgs) - 3})"
    return "; ".join(orgs)


def _docket_bits(hit: dict, with_title: bool) -> str:
    title = hit.get("docket_title") if with_title else None
    return (f"FERC docket {hit.get('docket_number', '?')}"
            + (f" ({title})" if title else ""))


_NO_DESC = {"", "no description given", "no description"}


def _file_name(hit: dict) -> str:
    desc = (hit.get("file_desc") or "").strip()
    if desc.lower() in _NO_DESC:
        desc = ""
    return desc or hit.get("orig_file_name") or hit.get("filename") or "file"


def _filing_bits(hit: dict) -> list[str]:
    bits = [f"filing {hit.get('accession_number') or '?'}"]
    if hit.get("document_class"):
        bits.append(hit["document_class"])
    if hit.get("filed_date"):
        bits.append(f"filed {hit['filed_date']}")
    authors = _authors(hit)
    if authors:
        bits.append(f"by {authors}")
    if hit.get("description"):
        bits.append(str(hit["description"])[:160])
    return bits


def _source_label(n: int, hit: dict) -> str:
    """One-line provenance header the model sees above each source body."""
    etype = hit.get("entity_type")
    if etype in ("docket", "docket_filing", "docket_filing_file"):
        # The title rides on the state-of-play source only; repeating it on
        # every filing of a scoped docket just pads the prompt.
        bits = [f"[{n}] {_docket_bits(hit, with_title=(etype == 'docket'))}"]
        if etype == "docket":
            asof = hit.get("summary_date")
            asof = f" as of {asof.date()}" if hasattr(asof, "date") else ""
            bits.append(f"state of play{asof}")
        else:
            bits.extend(_filing_bits(hit))
            if etype == "docket_filing_file":
                bits.append(f"DOCUMENT EXCERPT: {_file_name(hit)}")
        return " — ".join(bits)

    bits = [f"[{n}] {hit.get('type_short', '?')} meeting {hit.get('meeting_date', '?')}"]
    if etype == "document":
        if hit.get("item_id"):
            bits.append(f"agenda {hit['item_id']}: {hit.get('item_title') or 'Untitled item'}")
        bits.append(f"DOCUMENT EXCERPT: {hit.get('filename') or 'file'}")
    elif etype == "agenda_item":
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


def _source_body(hit: dict, question: str = "",
                 caps: dict[str, int] | None = None,
                 source_chars: int | None = None) -> str:
    caps = caps or _RETRIEVAL[DEFAULT_DETAIL]
    if hit.get("tier") == "document":
        passage = hit.get("passage")
        if passage is None:
            passage = extract_passages(hit.get("raw_content"), question,
                                       max_chars=caps["passage_chars"])
        passage = (passage or "").strip()
        if not passage:
            return "(No extractable text.)"
        return ("(Verbatim excerpt from the underlying document — not a "
                "summary. Quote figures and language from it directly.)\n\n"
                + passage)
    summ = db.get_current_summary(hit["entity_type"], hit["entity_id"]) or {}
    body = (summ.get("detailed") or summ.get("one_line") or "").strip()
    body = strip_image_refs(body)
    # The state of play is the frame for a docket answer — never cut it to
    # the per-source cap (its tail is the current posture and next dates).
    limit = (_MAX_STATE_OF_PLAY_CHARS if hit.get("entity_type") == "docket"
             else (source_chars or caps["source_chars"]))
    if len(body) > limit:
        body = body[:limit].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated)"
    return body or "(No summary text.)"


# ── Follow-ups ───────────────────────────────────────────────────────────

_MAX_PRIOR_ANSWER_CHARS = 14000


def hits_from_logged_sources(sources: list[dict], question: str,
                             passage_chars: int) -> list[dict]:
    """Turn a logged exchange's serialized sources back into hits, in the
    same order (so [n] numbering matches the prior answer). Summary
    sources fetch their body by entity id as usual; document excerpts
    re-cut passages from the stored text for the NEW question."""
    hits: list[dict] = []
    for src in sources:
        hit = dict(src)
        hit["tier"] = src.get("tier") or "summary"
        hit.pop("n", None)
        if hit["tier"] == "document":
            raw = None
            if src.get("file_row_id"):
                row = db.get_docket_filing_file(int(src["file_row_id"]))
                raw = (row or {}).get("raw_content")
            elif src.get("document_id"):
                row = db.get_document(int(src["document_id"]))
                raw = (row or {}).get("raw_content")
            d = search_svc.score_passages_detailed(raw, question, max_chars=passage_chars)
            hit["passage"], hit["passage_pages"] = d["passage"], d["pages"]
            hit["file_desc"] = src.get("filename")
        hits.append(hit)
    return hits


def prior_exchange_block(parent: dict) -> str:
    answer = (parent.get("answer_md") or "").strip()
    if len(answer) > _MAX_PRIOR_ANSWER_CHARS:
        answer = answer[:_MAX_PRIOR_ANSWER_CHARS].rsplit("\n", 1)[0] + "\n\n…(truncated)"
    return ("=== PRIOR EXCHANGE (this question follows up on it; the numbered "
            "sources below are the SAME sources, same numbers) ===\n\n"
            f"Earlier question: {parent.get('question', '').strip()}\n\n"
            f"Earlier answer:\n{answer}")


def scope_line(scope: dict[str, Any]) -> str:
    """Human-readable scope, shared by the prompt and the no-results copy."""
    if scope.get("dockets"):
        names = ", ".join(
            d["docket_number"] + (f" ({d['title']})" if d.get("title") else "")
            for d in scope["dockets"])
        base = f"FERC docket{'s' if len(scope['dockets']) > 1 else ''} {names}"
    elif scope.get("corpus") == "dockets":
        base = "all tracked FERC dockets"
    elif scope.get("corpus") == "meetings":
        base = "stakeholder meeting summaries"
    else:
        base = "meeting summaries and tracked FERC dockets"
    if scope.get("depth") == "documents":
        base += ", including the underlying documents"
    return base


def _omitted_label(hit: dict) -> str:
    """Short name for a source that didn't fit the budget."""
    if hit.get("docket_number"):
        who = _authors(hit) or hit.get("document_class") or "filing"
        when = hit.get("filed_date")
        return f"{who} ({when})" if when else who
    return _source_label(0, hit).split("] ", 1)[-1]


def source_char_budget(n_sources: int, caps: dict[str, int],
                       reserved: int = 0) -> int:
    """Per-source body length for `n_sources` under the level's budget:
    the level's cap when everything fits, shrinking toward the floor as
    the count grows. `reserved` is what the exempt state of play uses."""
    if n_sources <= 0:
        return caps["source_chars"]
    per = (caps["budget_chars"] - reserved) // n_sources
    return max(caps["min_source_chars"], min(caps["source_chars"], per))


def build_ask_prompt(question: str, hits: list[dict],
                     scope: dict[str, Any] | None = None,
                     detail: str = DEFAULT_DETAIL,
                     meta: dict[str, Any] | None = None,
                     parent: dict | None = None) -> str:
    """Template + detail directive + numbered sources + the question.
    Raises ValueError when the template or the level's directive is
    missing — callers surface that instead of free-styling.

    Sources are cut to a per-source length derived from the level's total
    budget (see source_char_budget); whatever still doesn't fit is dropped
    from the tail of `hits` (mutated in place so the response's source list
    matches the prompt) and named to the model in an OMITTED block, and in
    `meta["omitted"]` for the UI."""
    template = load_prompt(PROMPT_SLUG)
    if not template:
        raise ValueError(f"Prompt template '{PROMPT_SLUG}' not found")
    directive_slug = f"ask_detail_{detail}"
    directive = load_prompt(directive_slug)
    if not directive:
        raise ValueError(f"Prompt template '{directive_slug}' not found")
    caps = _RETRIEVAL.get(detail, _RETRIEVAL[DEFAULT_DETAIL])

    retrieval_q = (scope or {}).get("retrieval_question") or question
    n_exempt = sum(1 for h in hits if h.get("entity_type") == "docket")
    per_source = source_char_budget(len(hits) - n_exempt, caps,
                                    reserved=n_exempt * _MAX_STATE_OF_PLAY_CHARS)
    blocks = []
    used = 0
    omitted: list[str] = []
    for n, hit in enumerate(hits, start=1):
        if omitted:
            omitted.append(_omitted_label(hit))
            continue
        block = (f"=== SOURCE {_source_label(n, hit)} ===\n\n"
                 f"{_source_body(hit, retrieval_q, caps, per_source)}")
        # Budget guard: whatever the per-source cut couldn't absorb drops
        # off the tail (the roster is tier- then date-ordered, so that's
        # the least substantive, oldest material).
        if used + len(block) > caps["budget_chars"] and blocks:
            omitted.append(_omitted_label(hit))
            continue
        blocks.append(block)
        used += len(block)
    if omitted:
        del hits[len(blocks):]
        blocks.append(
            "=== OMITTED — did not fit the prompt budget ===\n\n"
            f"{len(omitted)} further source(s) exist in the record but are not "
            "shown. If the question turns on them, say so and name them: "
            + "; ".join(omitted))
    if meta is not None:
        meta["omitted"] = omitted
        meta["per_source_chars"] = per_source
    if parent:
        blocks.insert(0, prior_exchange_block(parent))
    sources_block = "\n\n".join(blocks)

    q_text = question
    if scope and (scope.get("dockets") or scope.get("corpus") != "all"
                  or scope.get("depth") == "documents"):
        q_text = f"{question}\n\n(Scope: {scope_line(scope)}.)"

    prompt = template.replace("[QUESTION]", q_text)
    if "[SOURCES]" in prompt:
        prompt = prompt.replace("[SOURCES]", sources_block)
    else:
        prompt = prompt + "\n\n" + sources_block
    # The directive goes where the template asks; a prompt_overrides copy
    # that predates the placeholder gets it appended after the sources.
    if "[DETAIL]" in prompt:
        prompt = prompt.replace("[DETAIL]", directive.strip())
    else:
        prompt = prompt + "\n\n" + directive.strip()

    general_context = load_prompt("general_context_prompt")
    if general_context:
        prompt = general_context + "\n\n" + prompt
    return prompt


def _serialize_source(n: int, hit: dict) -> dict[str, Any]:
    filed = hit.get("filed_date")
    return {
        "n": n,
        "tier": hit.get("tier", "summary"),
        "entity_type": hit.get("entity_type"),
        "entity_id": hit.get("entity_id"),
        # Meeting-side provenance (None for docket sources).
        "meeting_id": hit.get("meeting_id"),
        "meeting_title": hit.get("meeting_title"),
        "meeting_date": hit.get("meeting_date"),
        "venue": hit.get("venue"),
        "type_short": hit.get("type_short"),
        "item_id": hit.get("item_id"),
        "item_title": hit.get("item_title"),
        # Docket-side provenance (None for meeting sources).
        "docket_id": hit.get("docket_id"),
        "docket_number": hit.get("docket_number"),
        "docket_title": hit.get("docket_title"),
        "filing_id": hit.get("filing_id"),
        "accession_number": hit.get("accession_number"),
        "document_class": hit.get("document_class"),
        "filed_date": filed.isoformat() if hasattr(filed, "isoformat") else filed,
        "description": hit.get("description"),
        "authors": _authors(hit) or None,
        # Document-tier provenance.
        "document_id": hit.get("document_id"),
        "filename": _file_name(hit) if hit.get("tier") == "document" else None,
        "file_row_id": hit.get("file_row_id"),
        "pages": hit.get("passage_pages") or [],
        "snippet": hit.get("snippet"),
    }


def _serialize_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "corpus": scope["corpus"],
        "depth": scope["depth"],
        "dockets": scope["dockets"],
        "unknown_dockets": scope["unknown_dockets"],
        "omitted_sources": scope.get("omitted_sources", []),
    }


def _record(user: dict, payload: dict[str, Any], totals: dict,
            started: float) -> dict[str, Any]:
    """Best-effort ask_log write — never fails the answer. Returns the
    {"id", "created_at"} to merge into the response, or {}."""
    try:
        row = db.record_ask({
            "user_id": user.get("id"),
            "user_email": user.get("email"),
            "question": payload["question"],
            "scope": payload["scope"],
            "model_id": payload["model_id"],
            "effort": payload["effort"],
            "detail": payload["detail"],
            "sources": payload["sources"],
            "answer_md": payload["answer_md"],
            "input_tokens": totals.get("input_tokens"),
            "output_tokens": totals.get("output_tokens"),
            "cost_usd": payload["cost_usd"],
            "duration_ms": int((time.monotonic() - started) * 1000),
            "parent_id": payload.get("parent_id"),
        })
    except Exception:  # noqa: BLE001 — logging must not break answering
        log.exception("ask_log write failed")
        return {}
    created = row.get("created_at")
    return {"id": row.get("id"),
            "created_at": created.isoformat() if hasattr(created, "isoformat") else created}


def run_ask(body: AskBody, user: dict,
            progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The whole Ask pipeline for one request: scope → sources → prompt →
    model → ask_log. Used by the synchronous route and by ask_jobs.
    `progress` (optional) receives short status strings; raising from it
    aborts the run (that's how a job cancel lands)."""
    started = time.monotonic()
    tick = progress or (lambda _t: None)
    question = body.question.strip()
    detail = body.detail
    filters: dict[str, Any] = {}
    if body.type_short:
        filters["type_short"] = body.type_short
    if body.from_date:
        filters["from_date"] = body.from_date
    if body.to_date:
        filters["to_date"] = body.to_date

    model = resolve_model(body.model)
    parent: dict | None = None
    if body.parent_id is not None:
        parent = db.get_ask_log(body.parent_id)
        if not parent or (parent.get("user_email") != user.get("email")
                          and user.get("role") != "admin"):
            raise HTTPException(status_code=404, detail="Earlier exchange not found")
    scope = resolve_scope(body)
    caps = _RETRIEVAL.get(detail, _RETRIEVAL[DEFAULT_DETAIL])
    if parent:
        # Same sources, same numbers: the follow-up reads the prior
        # exchange and answers over what it already had.
        tick("Re-reading the earlier sources…")
        scope = {**scope, **{k: parent["scope"].get(k, scope[k])
                             for k in ("corpus", "depth", "dockets", "docket_ids")
                             if isinstance(parent.get("scope"), dict) and k in parent["scope"]}}
        scope["docket_ids"] = [d["id"] for d in scope.get("dockets") or []]
        hits = hits_from_logged_sources(parent.get("sources") or [], question,
                                        caps["passage_chars"])
    else:
        tick("Gathering sources…")
        hits = gather_sources(scope, detail=detail, **filters)

    if not hits:
        unit = "docket" if scope["corpus"] == "dockets" else "meeting"
        answer = _NO_RESULTS_ANSWER.format(corpus=scope_line(scope), unit=unit)
        if scope["unknown_dockets"]:
            answer += (" Not tracked here: "
                       + ", ".join(scope["unknown_dockets"])
                       + " — add it on the eLibrary page first.")
        payload = {
            "question": question,
            "answer_md": answer,
            "sources": [],
            "scope": _serialize_scope(scope),
            "model_id": None,
            "effort": None,
            "detail": detail,
            "parent_id": body.parent_id,
            "cost_usd": None,
        }
        payload.update(_record(user, payload, {}, started))
        return payload

    meta: dict[str, Any] = {}
    prompt = build_ask_prompt(question, hits, scope, detail=detail, meta=meta,
                              parent=parent)
    scope["omitted_sources"] = meta.get("omitted", [])

    cfg = load_model_config()
    effort = body.effort or (_DEEP_EFFORT_FLOOR if detail == "deep"
                             else DEFAULT_EFFORT)
    max_tokens = ask_max_tokens(effort, cfg)

    client = make_client()
    log.info("ask: %d source(s) [%s], model %s @ %s, %s detail%s: %r", len(hits),
             scope_line(scope), model, effort, detail,
             f", follow-up of #{body.parent_id}" if parent else "", question[:80])
    tick(f"Composing over {len(hits)} sources with {model}…")
    with capture_usage() as usage_log:
        answer = call_llm(client, model, prompt, max_tokens=max_tokens,
                          label=f"ask: {question[:40]}", effort=effort)
    totals = totals_from_usage_log(usage_log)

    payload = {
        "question": question,
        "answer_md": clean_output(answer),
        "sources": [_serialize_source(n, h) for n, h in enumerate(hits, start=1)],
        "scope": _serialize_scope(scope),
        "model_id": model,
        "effort": effort,
        "detail": detail,
        "parent_id": body.parent_id,
        "cost_usd": float(totals.get("cost_usd", 0.0)) or None,
    }
    payload.update(_record(user, payload, totals, started))
    return payload


@router.post("")
def ask(
    body: AskBody = Body(...),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Synchronous Ask — fine for brief/standard; the page uses jobs."""
    return run_ask(body, user)


# ── Jobs ─────────────────────────────────────────────────────────────────

def _serialize_ask_job(row: dict | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = {
        "id": row["id"],
        "status": row.get("status"),
        "progress_text": row.get("progress_text") or "",
        "error": row.get("error"),
        "request": row.get("request") or {},
        "ask_log_id": row.get("ask_log_id"),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "result": None,
    }
    if row.get("status") == "complete" and row.get("ask_log_id"):
        logged = db.get_ask_log(int(row["ask_log_id"]))
        if logged:
            out["result"] = _serialize_log_row(logged)
    return out


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.post("/jobs", status_code=202)
def start_ask_job(
    body: AskBody = Body(...),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Run Ask in the background; poll GET /api/ask/jobs/{id}."""
    resolve_model(body.model)  # fail fast on a bad pick, before claiming a row
    return jobs_service.start_ask_job(body, user, runner=run_ask)


@router.get("/jobs/active")
def active_ask_jobs(user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    return [_serialize_ask_job(r) for r in jobs_service.active_jobs(user.get("email") or "")]


@router.get("/jobs/{job_id}")
def get_ask_job(job_id: int, user: dict = Depends(current_user)) -> dict[str, Any]:
    row = jobs_service.get_job(job_id)
    if not row or (row.get("user_email") != user.get("email")
                   and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_ask_job(row)


@router.post("/jobs/{job_id}/cancel")
def cancel_ask_job(job_id: int, user: dict = Depends(current_user)) -> dict[str, Any]:
    row = jobs_service.get_job(job_id)
    if not row or (row.get("user_email") != user.get("email")
                   and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "cancelling": jobs_service.request_cancel(job_id)}


def _serialize_log_row(row: dict) -> dict[str, Any]:
    """An ask_log row in the same shape as a live answer, plus id/author."""
    created = row.get("created_at")
    cost = row.get("cost_usd")
    return {
        "id": row.get("id"),
        "parent_id": row.get("parent_id"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
        "user_email": row.get("user_email"),
        "question": row.get("question"),
        "answer_md": row.get("answer_md"),
        "sources": row.get("sources") or [],
        "scope": row.get("scope") or {},
        "model_id": row.get("model_id"),
        "effort": row.get("effort"),
        "detail": row.get("detail"),
        "cost_usd": float(cost) if cost is not None else None,
        "duration_ms": row.get("duration_ms"),
    }


@router.get("/history")
def ask_history(
    limit: int = 20,
    before_id: int | None = None,
    all: bool = False,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """The caller's past questions, newest first; admins may pass all=true
    for everyone's. Keyset paging via before_id."""
    limit = max(1, min(int(limit), 100))
    email = None if (all and user.get("role") == "admin") else user.get("email")
    rows = db.list_ask_log(limit=limit, before_id=before_id, user_email=email)
    items = [_serialize_log_row(r) for r in rows]
    return {
        "items": items,
        "next_before_id": items[-1]["id"] if len(items) == limit else None,
    }


@router.get("/options")
def ask_options(_: dict = Depends(current_user)) -> dict[str, Any]:
    """Model and effort choices for the Ask UI, plus the defaults."""
    return {
        "models": ASK_MODELS,
        "efforts": EFFORT_LEVELS,
        "details": DETAIL_LEVELS,
        "default_model": default_ask_model(),
        "default_effort": DEFAULT_EFFORT,
        "default_detail": DEFAULT_DETAIL,
    }
