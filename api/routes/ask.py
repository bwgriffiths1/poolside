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
from datetime import date
from typing import Any, Literal

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

# Retrieval breadth and per-source ceilings. Summaries beyond ~12 add
# latency and prompt cost faster than answer quality; 6k chars keeps a long
# briefing from crowding out the other sources. Document passages are
# fewer and shorter — they're supplements to the summaries, not the corpus.
_MAX_SOURCES = 12
_MAX_SOURCE_CHARS = 6000
_MAX_DOC_SOURCES = 6
_MAX_PASSAGE_CHARS = 3200

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


def gather_sources(scope: dict[str, Any], **filters) -> list[dict]:
    """Ranked, de-duplicated sources for a resolved scope: seeded
    state-of-play for scoped dockets, then summary hits from the chosen
    corpus (meeting and docket summaries interleaved by rank when both
    apply), then — at documents depth — verbatim passages."""
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
            add(seeded, _MAX_SOURCES)

    summary_hits: list[dict] = []
    if corpus in ("all", "meetings"):
        summary_hits += retrieve_for_question(q, limit=_MAX_SOURCES, **filters)
    if corpus in ("all", "dockets"):
        summary_hits += retrieve_docket_summaries(q, limit=_MAX_SOURCES,
                                                  docket_ids=docket_ids)
    if corpus == "all":
        summary_hits.sort(key=lambda h: float(h.get("rank") or 0.0), reverse=True)
    for h in summary_hits:
        h.setdefault("tier", "summary")
        add(h, _MAX_SOURCES)

    if scope["depth"] == "documents":
        doc_hits = retrieve_document_hits(
            q, limit=_MAX_DOC_SOURCES, corpus=corpus, docket_ids=docket_ids,
            **filters,
        )
        for h in doc_hits:
            add(h, _MAX_SOURCES + _MAX_DOC_SOURCES)
    return hits


# ── Prompt assembly ──────────────────────────────────────────────────────

def _authors(hit: dict) -> str:
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


def _source_body(hit: dict, question: str = "") -> str:
    if hit.get("tier") == "document":
        passage = hit.get("passage")
        if passage is None:
            passage = extract_passages(hit.get("raw_content"), question,
                                       max_chars=_MAX_PASSAGE_CHARS)
        passage = (passage or "").strip()
        if not passage:
            return "(No extractable text.)"
        return ("(Verbatim excerpt from the underlying document — not a "
                "summary. Quote figures and language from it directly.)\n\n"
                + passage)
    summ = db.get_current_summary(hit["entity_type"], hit["entity_id"]) or {}
    body = (summ.get("detailed") or summ.get("one_line") or "").strip()
    body = strip_image_refs(body)
    if len(body) > _MAX_SOURCE_CHARS:
        body = body[:_MAX_SOURCE_CHARS].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated)"
    return body or "(No summary text.)"


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


def build_ask_prompt(question: str, hits: list[dict],
                     scope: dict[str, Any] | None = None) -> str:
    """Template + numbered sources + the question. Raises ValueError when
    the template is missing — callers surface that instead of free-styling."""
    template = load_prompt(PROMPT_SLUG)
    if not template:
        raise ValueError(f"Prompt template '{PROMPT_SLUG}' not found")

    retrieval_q = (scope or {}).get("retrieval_question") or question
    blocks = []
    for n, hit in enumerate(hits, start=1):
        blocks.append(f"=== SOURCE {_source_label(n, hit)} ===\n\n"
                      f"{_source_body(hit, retrieval_q)}")
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
        # Document-tier provenance.
        "document_id": hit.get("document_id"),
        "filename": _file_name(hit) if hit.get("tier") == "document" else None,
        "file_row_id": hit.get("file_row_id"),
        "snippet": hit.get("snippet"),
    }


def _serialize_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "corpus": scope["corpus"],
        "depth": scope["depth"],
        "dockets": scope["dockets"],
        "unknown_dockets": scope["unknown_dockets"],
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

    model = resolve_model(body.model)
    scope = resolve_scope(body)
    hits = gather_sources(scope, **filters)

    if not hits:
        unit = "docket" if scope["corpus"] == "dockets" else "meeting"
        answer = _NO_RESULTS_ANSWER.format(corpus=scope_line(scope), unit=unit)
        if scope["unknown_dockets"]:
            answer += (" Not tracked here: "
                       + ", ".join(scope["unknown_dockets"])
                       + " — add it on the eLibrary page first.")
        return {
            "question": question,
            "answer_md": answer,
            "sources": [],
            "scope": _serialize_scope(scope),
            "model_id": None,
            "effort": None,
            "cost_usd": None,
        }

    prompt = build_ask_prompt(question, hits, scope)

    cfg = load_model_config()
    max_tokens = int(cfg.get("ask_max_tokens") or 8192)
    effort = body.effort or DEFAULT_EFFORT

    client = make_client()
    log.info("ask: %d source(s) [%s], model %s @ %s: %r", len(hits),
             scope_line(scope), model, effort, question[:80])
    with capture_usage() as usage_log:
        answer = call_llm(client, model, prompt, max_tokens=max_tokens,
                          label=f"ask: {question[:40]}", effort=effort)
    totals = totals_from_usage_log(usage_log)

    return {
        "question": question,
        "answer_md": clean_output(answer),
        "sources": [_serialize_source(n, h) for n, h in enumerate(hits, start=1)],
        "scope": _serialize_scope(scope),
        "model_id": model,
        "effort": effort,
        "cost_usd": float(totals.get("cost_usd", 0.0)) or None,
    }


@router.get("/options")
def ask_options(_: dict = Depends(current_user)) -> dict[str, Any]:
    """Model and effort choices for the Ask UI, plus the defaults."""
    return {
        "models": ASK_MODELS,
        "efforts": EFFORT_LEVELS,
        "default_model": default_ask_model(),
        "default_effort": DEFAULT_EFFORT,
    }
