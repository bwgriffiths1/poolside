"""Ranked full-text retrieval over summary bodies.

The one implementation of "find summaries matching a query", shared by the
/api/search endpoints (command palette + Search screen) and /api/ask
(retrieval for cited Q&A). Queries the tsvector index from migration 004.

Each hit resolves to a meeting briefing or an agenda item; snippets come
back HTML-safe (escaped, with <b> highlight tags) because both consumers
render them with dangerouslySetInnerHTML.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

from pipeline import db

# Words that carry no retrieval signal for the OR-relaxed fallback query.
_STOPWORDS = frozenset(
    "the a an of for on in to and or is are was were be been what when where"
    " who whom how why which does do did has have had latest status stand"
    " stands standing about with between current recent update updates news"
    " tell me show".split()
)


def or_query(question: str) -> str:
    """Relax a natural-language question into `term or term or ...`.

    websearch_to_tsquery ANDs plain terms, so a full question usually
    matches nothing; OR-ing the substantive terms is the recall fallback.
    """
    terms = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]+", question)
             if t.lower() not in _STOPWORDS]
    # De-dup preserving order.
    seen: set[str] = set()
    keep = []
    for t in terms:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            keep.append(t)
    return " or ".join(keep)


def search_summary_hits(
    q: str,
    limit: int = 15,
    from_date: date | None = None,
    to_date: date | None = None,
    type_short: str | None = None,
    tag: str | None = None,
    presenter: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Ranked summary hits for a websearch-syntax query. See module docstring."""
    q = (q or "").strip()
    if not q:
        return []

    if status == "approved":
        status_clause = "status = 'approved'"
    elif status == "draft":
        status_clause = "status = 'draft'"
    else:
        status_clause = "status IN ('draft', 'approved')"

    params: dict[str, Any] = {"q": q, "limit": limit}
    extra_where: list[str] = []

    if from_date is not None:
        extra_where.append("m.meeting_date >= %(from_date)s")
        params["from_date"] = from_date
    if to_date is not None:
        extra_where.append("m.meeting_date <= %(to_date)s")
        params["to_date"] = to_date
    if type_short:
        extra_where.append("mt.short_name = %(type_short)s")
        params["type_short"] = type_short
    if presenter:
        extra_where.append("ai.presenter ILIKE %(presenter_pat)s")
        params["presenter_pat"] = f"%{presenter}%"
    if tag:
        # Match any tag-typed entity_tag on either the meeting or the agenda
        # item the hit belongs to.
        extra_where.append(
            "EXISTS ("
            "  SELECT 1 FROM entity_tags et JOIN tags t ON t.id = et.tag_id "
            "   WHERE t.name = %(tag)s "
            "     AND ((et.entity_type='agenda_item' AND et.entity_id = ai.id) "
            "       OR (et.entity_type='meeting'     AND et.entity_id = m.id))"
            ")"
        )
        params["tag"] = tag

    where_extra_sql = ("AND " + " AND ".join(extra_where)) if extra_where else ""

    sql = f"""
        WITH current_versions AS (
            SELECT DISTINCT ON (entity_type, entity_id)
                id, entity_type, entity_id, detailed, one_line, detailed_tsv
            FROM summary_versions
            WHERE {status_clause}
              AND detailed_tsv @@ websearch_to_tsquery('english', %(q)s)
            ORDER BY entity_type, entity_id,
                CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                version DESC
        )
        SELECT
            cv.entity_type,
            cv.entity_id,
            ts_rank_cd(cv.detailed_tsv, websearch_to_tsquery('english', %(q)s)) AS rank,
            ts_headline(
                'english',
                COALESCE(cv.detailed, cv.one_line, ''),
                websearch_to_tsquery('english', %(q)s),
                -- Highlight with inert markers, NOT <b> tags: the source text
                -- is scraped/user-edited markdown, and the frontend renders
                -- this snippet as HTML. We escape it in Python below and only
                -- then turn the markers into real <b> tags.
                'StartSel=@@HLS@@, StopSel=@@HLE@@, MaxFragments=1, MaxWords=22, MinWords=10, ShortWord=2'
            ) AS snippet,
            m.id              AS meeting_id,
            m.title           AS meeting_title,
            m.meeting_date    AS meeting_date,
            v.short_name      AS venue,
            mt.short_name     AS type_short,
            ai.item_id        AS item_id,
            ai.title          AS item_title,
            ai.presenter      AS presenter,
            ai.org            AS organization
        FROM current_versions cv
        LEFT JOIN agenda_items ai
               ON cv.entity_type = 'agenda_item' AND ai.id = cv.entity_id
        JOIN meetings m
          ON m.id = CASE
                       WHEN cv.entity_type = 'meeting' THEN cv.entity_id
                       ELSE ai.meeting_id
                    END
        JOIN meeting_types mt ON mt.id = m.meeting_type_id
        JOIN venues v         ON v.id  = mt.venue_id
        WHERE TRUE
          {where_extra_sql}
        ORDER BY rank DESC, m.meeting_date DESC
        LIMIT %(limit)s
    """

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]

    # Normalize dates to ISO strings, and make snippets safe to render as
    # HTML: escape everything, then convert the highlight markers to <b>.
    for r in rows:
        d = r.get("meeting_date")
        if d is not None and hasattr(d, "isoformat"):
            r["meeting_date"] = d.isoformat()
        snippet = html.escape(r.get("snippet") or "", quote=False)
        r["snippet"] = snippet.replace("@@HLS@@", "<b>").replace("@@HLE@@", "</b>")

    return rows


def retrieve_for_question(question: str, limit: int = 12,
                          **filters: Any) -> list[dict[str, Any]]:
    """Retrieval for Q&A: strict websearch first, OR-relaxed fallback when
    the AND semantics leave fewer than 3 hits. De-duped, rank order kept."""
    hits = search_summary_hits(question, limit=limit, **filters)
    if len(hits) < 3:
        relaxed = or_query(question)
        if relaxed and relaxed.lower() != question.strip().lower():
            seen = {(h["entity_type"], h["entity_id"]) for h in hits}
            for h in search_summary_hits(relaxed, limit=limit, **filters):
                key = (h["entity_type"], h["entity_id"])
                if key not in seen and len(hits) < limit:
                    seen.add(key)
                    hits.append(h)
    return hits


# ── FERC dockets ─────────────────────────────────────────────────────────


def _escape_snippet(text: str) -> str:
    return html.escape(text or "", quote=False)


def search_docket_hits(q: str, limit: int = 10) -> list[dict[str, Any]]:
    """Ranked hits across TRACKED FERC dockets, two sources merged:

    1. Metadata: case-insensitive substring on docket number / title /
       party label / notes — so "PfP" hits a docket whose title carries the
       acronym, and "ER26-925" always finds its docket, summaries or not.
    2. Full text: the same tsvector the meeting search uses, over docket
       state-of-play and filing summaries (current versions only), each hit
       resolved back to its docket with an HTML-safe highlighted snippet.

    Metadata hits sort first (an identifier match is what you meant); text
    hits follow by rank. De-duped so a docket whose title AND state of play
    match appears once, keeping the metadata row.
    """
    q = (q or "").strip()
    if not q:
        return []

    out: list[dict[str, Any]] = []
    seen_dockets: set[int] = set()

    meta_sql = """
        SELECT d.id AS docket_id, d.docket_number, d.title, d.party_label
          FROM dockets d
         WHERE d.docket_number ILIKE %(pat)s
            OR COALESCE(d.title, '') ILIKE %(pat)s
            OR COALESCE(d.party_label, '') ILIKE %(pat)s
            OR COALESCE(d.notes, '') ILIKE %(pat)s
         ORDER BY d.docket_number
         LIMIT %(limit)s
    """
    text_sql = """
        WITH current_versions AS (
            SELECT DISTINCT ON (entity_type, entity_id)
                entity_type, entity_id, detailed, one_line, detailed_tsv
            FROM summary_versions
            WHERE entity_type IN ('docket', 'docket_filing')
              AND status IN ('draft', 'approved')
              AND detailed_tsv @@ websearch_to_tsquery('english', %(q)s)
            ORDER BY entity_type, entity_id,
                CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                version DESC
        )
        SELECT
            cv.entity_type,
            cv.entity_id,
            ts_rank_cd(cv.detailed_tsv, websearch_to_tsquery('english', %(q)s)) AS rank,
            ts_headline(
                'english',
                COALESCE(cv.detailed, cv.one_line, ''),
                websearch_to_tsquery('english', %(q)s),
                'StartSel=@@HLS@@, StopSel=@@HLE@@, MaxFragments=1, MaxWords=22, MinWords=10, ShortWord=2'
            ) AS snippet,
            d.id              AS docket_id,
            d.docket_number   AS docket_number,
            d.title           AS title,
            d.party_label     AS party_label,
            f.accession_number AS accession_number,
            f.document_class  AS document_class
        FROM current_versions cv
        LEFT JOIN docket_filings f
               ON cv.entity_type = 'docket_filing' AND f.id = cv.entity_id
        JOIN dockets d
          ON d.id = CASE
                       WHEN cv.entity_type = 'docket' THEN cv.entity_id
                       ELSE f.docket_id
                    END
        ORDER BY rank DESC
        LIMIT %(limit)s
    """

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(meta_sql, {"pat": f"%{q}%", "limit": limit})
            meta_rows = [dict(r) for r in cur.fetchall()]
            cur.execute(text_sql, {"q": q, "limit": limit})
            text_rows = [dict(r) for r in cur.fetchall()]

    for r in meta_rows:
        seen_dockets.add(r["docket_id"])
        out.append({
            "entity_type": "docket_meta",
            "entity_id": r["docket_id"],
            "docket_id": r["docket_id"],
            "docket_number": r["docket_number"],
            "title": r.get("title"),
            "party_label": r.get("party_label"),
            "accession_number": None,
            "document_class": None,
            "snippet": _escape_snippet(r.get("title") or r.get("party_label") or ""),
        })

    for r in text_rows:
        # A docket already surfaced by metadata keeps that row; filing hits
        # for other dockets each stand on their own.
        if r["entity_type"] == "docket" and r["docket_id"] in seen_dockets:
            continue
        snippet = _escape_snippet(r.get("snippet") or "")
        r["snippet"] = snippet.replace("@@HLS@@", "<b>").replace("@@HLE@@", "</b>")
        r.pop("rank", None)
        out.append(r)

    return out[:limit]


# ── Ask Poolside: docket-scoped summaries + underlying-document tier ─────
#
# Ask's retrieval has two axes the Search screen doesn't: a SCOPE (all /
# meetings / FERC dockets / specific dockets) and a DEPTH (summaries only,
# or summaries plus verbatim passages from the underlying documents —
# migration 022's raw_tsv over documents.raw_content and
# docket_filing_files.raw_content). Everything below returns hit dicts in
# the same shape search_summary_hits does, extended with docket / filing /
# document provenance, so api/routes/ask.py can label and cite them
# uniformly.

_HEADLINE_OPTS = (
    "StartSel=@@HLS@@, StopSel=@@HLE@@, MaxFragments=1, MaxWords=22, "
    "MinWords=10, ShortWord=2"
)


def _finish_snippet(raw: str | None) -> str:
    return (_escape_snippet(raw or "")
            .replace("@@HLS@@", "<b>").replace("@@HLE@@", "</b>"))


def search_docket_summary_hits(
    q: str,
    limit: int = 15,
    docket_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Ranked hits over docket state-of-play + filing summaries (current
    versions only), optionally restricted to specific dockets. Unlike
    search_docket_hits (the Search screen's docket list) this keeps one
    row per SUMMARY, because Ask cites each summary as its own source."""
    q = (q or "").strip()
    if not q:
        return []
    params: dict[str, Any] = {"q": q, "limit": limit}
    scope_sql = ""
    if docket_ids:
        scope_sql = "AND d.id = ANY(%(docket_ids)s)"
        params["docket_ids"] = list(docket_ids)

    sql = f"""
        WITH current_versions AS (
            SELECT DISTINCT ON (entity_type, entity_id)
                entity_type, entity_id, detailed, one_line, detailed_tsv,
                created_at
            FROM summary_versions
            WHERE entity_type IN ('docket', 'docket_filing')
              AND status IN ('draft', 'approved')
              AND detailed_tsv @@ websearch_to_tsquery('english', %(q)s)
            ORDER BY entity_type, entity_id,
                CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                version DESC
        )
        SELECT
            cv.entity_type,
            cv.entity_id,
            cv.created_at      AS summary_date,
            ts_rank_cd(cv.detailed_tsv, websearch_to_tsquery('english', %(q)s)) AS rank,
            ts_headline('english', COALESCE(cv.detailed, cv.one_line, ''),
                        websearch_to_tsquery('english', %(q)s),
                        '{_HEADLINE_OPTS}') AS snippet,
            d.id               AS docket_id,
            d.docket_number    AS docket_number,
            d.title            AS docket_title,
            f.id               AS filing_id,
            f.accession_number AS accession_number,
            f.document_class   AS document_class,
            f.document_type    AS document_type,
            f.description      AS description,
            COALESCE(f.filed_date, f.issued_date) AS filed_date,
            f.filing_parties   AS filing_parties
        FROM current_versions cv
        LEFT JOIN docket_filings f
               ON cv.entity_type = 'docket_filing' AND f.id = cv.entity_id
        JOIN dockets d
          ON d.id = CASE WHEN cv.entity_type = 'docket' THEN cv.entity_id
                         ELSE f.docket_id END
        WHERE TRUE {scope_sql}
        ORDER BY rank DESC
        LIMIT %(limit)s
    """
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["snippet"] = _finish_snippet(r.pop("snippet", None))
        r["tier"] = "summary"
    return rows


def search_document_hits(
    q: str,
    limit: int = 6,
    corpus: str = "all",
    docket_ids: list[int] | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    type_short: str | None = None,
    passage_chars: int = 3200,
) -> list[dict[str, Any]]:
    """Ranked hits over the UNDERLYING text (migration 022): meeting
    materials and/or FERC filing files, per `corpus`. Each hit carries the
    full raw_content so the caller can cut passages (extract_passages) —
    the SQL only computes a short highlighted snippet for the UI, and only
    for the rows that survive the LIMIT (headline on a 500k-char document
    is not free, hence the subquery)."""
    q = (q or "").strip()
    if not q:
        return []
    want_meetings = corpus in ("all", "meetings") and not docket_ids
    want_dockets = corpus in ("all", "dockets") or bool(docket_ids)
    fetch = limit * 3
    out: list[dict[str, Any]] = []

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            if want_meetings:
                params: dict[str, Any] = {"q": q, "limit": fetch}
                extra: list[str] = []
                if from_date is not None:
                    extra.append("m.meeting_date >= %(from_date)s")
                    params["from_date"] = from_date
                if to_date is not None:
                    extra.append("m.meeting_date <= %(to_date)s")
                    params["to_date"] = to_date
                if type_short:
                    extra.append("mt.short_name = %(type_short)s")
                    params["type_short"] = type_short
                extra_sql = ("AND " + " AND ".join(extra)) if extra else ""
                cur.execute(f"""
                    SELECT t.*,
                           ts_headline('english', left(t.raw_content, 200000),
                                       websearch_to_tsquery('english', %(q)s),
                                       '{_HEADLINE_OPTS}') AS snippet
                    FROM (
                        SELECT
                            'document'::text  AS entity_type,
                            d.id              AS entity_id,
                            d.id              AS document_id,
                            d.filename        AS filename,
                            d.file_type       AS file_type,
                            d.raw_content     AS raw_content,
                            ts_rank_cd(d.raw_tsv,
                                       websearch_to_tsquery('english', %(q)s)) AS rank,
                            m.id              AS meeting_id,
                            m.title           AS meeting_title,
                            m.meeting_date    AS meeting_date,
                            v.short_name      AS venue,
                            mt.short_name     AS type_short,
                            ai.item_id        AS item_id,
                            ai.title          AS item_title
                        FROM documents d
                        JOIN meetings m       ON m.id = d.meeting_id
                        JOIN meeting_types mt ON mt.id = m.meeting_type_id
                        JOIN venues v         ON v.id = mt.venue_id
                        LEFT JOIN LATERAL (
                            SELECT ai.item_id, ai.title
                              FROM item_documents idoc
                              JOIN agenda_items ai ON ai.id = idoc.item_id
                             WHERE idoc.document_id = d.id
                             ORDER BY ai.id
                             LIMIT 1
                        ) ai ON TRUE
                        WHERE d.raw_tsv @@ websearch_to_tsquery('english', %(q)s)
                          AND NOT d.ceii_skipped
                          {extra_sql}
                        ORDER BY rank DESC, m.meeting_date DESC
                        LIMIT %(limit)s
                    ) t
                """, params)
                out.extend(dict(r) for r in cur.fetchall())

            if want_dockets:
                params = {"q": q, "limit": fetch}
                scope_sql = ""
                if docket_ids:
                    scope_sql = "AND d.id = ANY(%(docket_ids)s)"
                    params["docket_ids"] = list(docket_ids)
                cur.execute(f"""
                    SELECT t.*,
                           ts_headline('english', left(t.raw_content, 200000),
                                       websearch_to_tsquery('english', %(q)s),
                                       '{_HEADLINE_OPTS}') AS snippet
                    FROM (
                        SELECT
                            'docket_filing_file'::text AS entity_type,
                            dff.id             AS entity_id,
                            dff.id             AS file_row_id,
                            dff.file_desc      AS file_desc,
                            dff.orig_file_name AS orig_file_name,
                            dff.file_type      AS file_type,
                            dff.raw_content    AS raw_content,
                            ts_rank_cd(dff.raw_tsv,
                                       websearch_to_tsquery('english', %(q)s)) AS rank,
                            f.id               AS filing_id,
                            f.accession_number AS accession_number,
                            f.document_class   AS document_class,
                            f.document_type    AS document_type,
                            f.description      AS description,
                            COALESCE(f.filed_date, f.issued_date) AS filed_date,
                            f.filing_parties   AS filing_parties,
                            d.id               AS docket_id,
                            d.docket_number    AS docket_number,
                            d.title            AS docket_title
                        FROM docket_filing_files dff
                        JOIN docket_filings f ON f.id = dff.filing_id
                        JOIN dockets d        ON d.id = f.docket_id
                        WHERE dff.raw_tsv @@ websearch_to_tsquery('english', %(q)s)
                          AND dff.included
                          {scope_sql}
                        ORDER BY rank DESC, filed_date DESC NULLS LAST
                        LIMIT %(limit)s
                    ) t
                """, params)
                out.extend(dict(r) for r in cur.fetchall())

    # tsvector positions stop at word 16,383, so a long filing matched only
    # in its back half ranks 0 in SQL. Over-fetch, then re-rank by passage
    # score (which reads the whole text) before cutting to `limit`.
    for r in out:
        d = score_passages_detailed(r.get("raw_content"), q, max_chars=passage_chars)
        r["passage_score"], r["passage"], r["passage_pages"] = (
            d["score"], d["passage"], d["pages"])
    out.sort(key=lambda r: (r["passage_score"], float(r.get("rank") or 0.0)),
             reverse=True)
    # Meeting materials often ship as clean / redline / incremental copies
    # of one document; keep only the best-scoring variant per meeting.
    seen_variants: set[tuple] = set()
    deduped = []
    for r in out:
        key = (r.get("meeting_id"), _variant_key(r.get("filename")))
        if r["entity_type"] == "document":
            if key in seen_variants:
                continue
            seen_variants.add(key)
        deduped.append(r)
    out = deduped[:limit]
    for r in out:
        r["snippet"] = _finish_snippet(r.pop("snippet", None))
        r["tier"] = "document"
    return out


_VARIANT_RE = re.compile(r"\(.*?\)|\b(clean|redline|incremental|draft|final|v\d+(\.\d+)*)\b|[\s_\-.]+", re.I)


def _variant_key(filename: str | None) -> str:
    return _VARIANT_RE.sub("", (filename or "").lower())


def _with_or_fallback(fn, question: str, limit: int, **kw) -> list[dict]:
    """Strict websearch pass, then the OR-relaxed pass when the strict one
    is thin — the same recall rule retrieve_for_question applies."""
    hits = fn(question, limit=limit, **kw)
    if len(hits) < 3:
        relaxed = or_query(question)
        if relaxed and relaxed.lower() != question.strip().lower():
            seen = {(h["entity_type"], h["entity_id"]) for h in hits}
            for h in fn(relaxed, limit=limit, **kw):
                key = (h["entity_type"], h["entity_id"])
                if key not in seen and len(hits) < limit:
                    seen.add(key)
                    hits.append(h)
    return hits


def retrieve_docket_summaries(question: str, limit: int = 12,
                              docket_ids: list[int] | None = None) -> list[dict]:
    return _with_or_fallback(search_docket_summary_hits, question, limit,
                             docket_ids=docket_ids)


def retrieve_document_hits(question: str, limit: int = 6, **scope) -> list[dict]:
    return _with_or_fallback(search_document_hits, question, limit, **scope)


# ── Passage extraction ───────────────────────────────────────────────────

_WINDOW_CHARS = 1400


def query_terms(question: str) -> list[str]:
    """Substantive lowercase terms of a question, for passage scoring."""
    return [t.lower() for t in or_query(question).split(" or ") if len(t) >= 3]


def _term_matcher(term: str) -> re.Pattern:
    # Crude stemming: match on a prefix so "auction" also finds "auctions"
    # / "auctioned"; short terms and codes (CAR-SA, ER26) stay exact.
    if len(term) <= 4 or "-" in term or any(c.isdigit() for c in term):
        return re.compile(r"\b" + re.escape(term) + r"\b", re.I)
    return re.compile(r"\b" + re.escape(term[:-1]), re.I)


_PAGE_RE = re.compile(r"\[Page (\d+)\]")


def score_passages_detailed(text: str | None, question: str,
                            max_chars: int = 3200,
                            window: int = _WINDOW_CHARS) -> dict[str, Any]:
    """{score, passage, pages}: the best few windows of `text` for
    `question`, in document order, joined with an ellipsis marker. Windows
    are paragraph-aligned chunks of ~`window` chars scored by distinct-term
    coverage (dominant) plus term frequency; the score is the best
    window's, so callers can re-rank documents by it. When nothing matches
    — e.g. the tsquery matched on stems Python can't see — the opening of
    the document stands in at score 0, so the source is never empty.

    Extracted PDFs carry `[Page N]` markers; each chosen window is prefixed
    with the page it starts on (`[p. N]`) so the model can cite pages, and
    `pages` lists them for the UI's deep links."""
    text = (text or "").strip()
    if not text:
        return {"score": 0, "passage": "", "pages": []}
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=\S)", text) if p.strip()]
    windows: list[str] = []
    pages: list[int | None] = []      # page each window starts on
    buf = ""
    buf_page: int | None = None
    page: int | None = None           # last marker seen so far
    for para in paras:
        para_page = page
        m = _PAGE_RE.search(para)
        if m:
            page = int(m.group(1))
            if para.lstrip().startswith("[Page"):
                para_page = page       # marker leads the paragraph
        if buf and len(buf) + len(para) + 2 > window:
            windows.append(buf); pages.append(buf_page)
            buf, buf_page = para, para_page
        else:
            if not buf:
                buf_page = para_page
            buf = f"{buf}\n{para}" if buf else para
        # A single giant paragraph (extracted PDFs often lack breaks):
        while len(buf) > window * 1.5:
            cut = buf.rfind(" ", 0, window)
            if cut < window // 2:
                cut = window
            windows.append(buf[:cut]); pages.append(buf_page)
            buf = buf[cut:].lstrip()
            buf_page = page
    if buf:
        windows.append(buf); pages.append(buf_page)
    if not windows:
        return {"score": 0, "passage": "", "pages": []}

    def render(idxs: list[int]) -> tuple[str, list[int]]:
        parts, seen_pages = [], []
        for i in idxs:
            w = windows[i][:max_chars]
            if pages[i] is not None:
                parts.append(f"[p. {pages[i]}] {w}")
                if pages[i] not in seen_pages:
                    seen_pages.append(pages[i])
            else:
                parts.append(w)
        return "\n\n[…]\n\n".join(parts), seen_pages

    matchers = [_term_matcher(t) for t in query_terms(question)]
    scored: list[tuple[float, int]] = []
    for i, w in enumerate(windows):
        distinct = 0
        freq = 0
        for m in matchers:
            n = len(m.findall(w))
            if n:
                distinct += 1
                freq += min(n, 5)
        score = distinct * 10 + freq
        if score:
            scored.append((score, i))
    if not scored:
        passage, pg = render([0])
        return {"score": 0, "passage": passage, "pages": pg}

    scored.sort(key=lambda s: (-s[0], s[1]))
    chosen: list[int] = []
    used = 0
    for _score, i in scored:
        if used + len(windows[i]) > max_chars and chosen:
            break
        chosen.append(i)
        used += len(windows[i])
        if used >= max_chars:
            break
    chosen.sort()
    passage, pg = render(chosen)
    return {"score": scored[0][0], "passage": passage, "pages": pg}


def score_passages(text: str | None, question: str,
                   max_chars: int = 3200,
                   window: int = _WINDOW_CHARS) -> tuple[int, str]:
    d = score_passages_detailed(text, question, max_chars=max_chars, window=window)
    return d["score"], d["passage"]


def extract_passages(text: str | None, question: str,
                     max_chars: int = 3200) -> str:
    return score_passages(text, question, max_chars=max_chars)[1]
