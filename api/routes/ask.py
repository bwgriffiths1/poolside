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
_RETRIEVAL: dict[str, dict[str, int]] = {
    "brief":    {"sources": 12, "source_chars": 6000,  "doc_sources": 6, "passage_chars": 3200},
    "standard": {"sources": 16, "source_chars": 8000,  "doc_sources": 6, "passage_chars": 3200},
    "deep":     {"sources": 40, "source_chars": 12000, "doc_sources": 8, "passage_chars": 4000},
}
# A scoped docket's state of play is the frame for the whole answer, so it
# is exempt from the per-source cap at every level; this is a safety
# ceiling only.
_MAX_STATE_OF_PLAY_CHARS = 16000
# Total prompt budget (chars) for the sources block — a deep roster over a
# very busy docket trims its tail rather than blowing the request up.
_MAX_SOURCES_BLOCK_CHARS = 220_000
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
                 caps: dict[str, int] | None = None) -> str:
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
             else caps["source_chars"])
    if len(body) > limit:
        body = body[:limit].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated)"
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
                     scope: dict[str, Any] | None = None,
                     detail: str = DEFAULT_DETAIL) -> str:
    """Template + detail directive + numbered sources + the question.
    Raises ValueError when the template or the level's directive is
    missing — callers surface that instead of free-styling."""
    template = load_prompt(PROMPT_SLUG)
    if not template:
        raise ValueError(f"Prompt template '{PROMPT_SLUG}' not found")
    directive_slug = f"ask_detail_{detail}"
    directive = load_prompt(directive_slug)
    if not directive:
        raise ValueError(f"Prompt template '{directive_slug}' not found")
    caps = _RETRIEVAL.get(detail, _RETRIEVAL[DEFAULT_DETAIL])

    retrieval_q = (scope or {}).get("retrieval_question") or question
    blocks = []
    used = 0
    for n, hit in enumerate(hits, start=1):
        block = (f"=== SOURCE {_source_label(n, hit)} ===\n\n"
                 f"{_source_body(hit, retrieval_q, caps)}")
        # Budget guard: a deep roster on a very busy docket trims its tail
        # (the oldest / lowest-ranked sources) instead of failing.
        if used + len(block) > _MAX_SOURCES_BLOCK_CHARS and blocks:
            del hits[n - 1:]
            break
        blocks.append(block)
        used += len(block)
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
        })
    except Exception:  # noqa: BLE001 — logging must not break answering
        log.exception("ask_log write failed")
        return {}
    created = row.get("created_at")
    return {"id": row.get("id"),
            "created_at": created.isoformat() if hasattr(created, "isoformat") else created}


@router.post("")
def ask(
    body: AskBody = Body(...),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    started = time.monotonic()
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
    scope = resolve_scope(body)
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
            "cost_usd": None,
        }
        payload.update(_record(user, payload, {}, started))
        return payload

    prompt = build_ask_prompt(question, hits, scope, detail=detail)

    cfg = load_model_config()
    effort = body.effort or (_DEEP_EFFORT_FLOOR if detail == "deep"
                             else DEFAULT_EFFORT)
    max_tokens = ask_max_tokens(effort, cfg)

    client = make_client()
    log.info("ask: %d source(s) [%s], model %s @ %s, %s detail: %r", len(hits),
             scope_line(scope), model, effort, detail, question[:80])
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
        "cost_usd": float(totals.get("cost_usd", 0.0)) or None,
    }
    payload.update(_record(user, payload, totals, started))
    return payload


def _serialize_log_row(row: dict) -> dict[str, Any]:
    """An ask_log row in the same shape as a live answer, plus id/author."""
    created = row.get("created_at")
    cost = row.get("cost_usd")
    return {
        "id": row.get("id"),
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
