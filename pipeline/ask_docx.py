"""Ask Poolside answer → .docx (editorial grammar shared with the docket
and roundup exports): question as the masthead, scope/model provenance,
the answer body, then a numbered Sources list with eLibrary / meeting
references so the `[n]` markers in the text resolve on paper."""
from __future__ import annotations

import io
import re
from datetime import date, datetime

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

import pipeline.brand as brand
import pipeline.db as db
from pipeline.briefing import (
    _CYAN,
    _CYAN_HEX,
    _GRAY_MID,
    _GRAY_MID_HEX,
    _GRAY_TEXT,
    _INK,
    _INK_SOFT,
    _LABEL,
    _eyebrow,
    _inline_runs,
    _v2_link,
    _v2_page_number,
    _v2_pborder,
    _v2_right_tab,
    _v2_run,
    _v2_spacing,
)
from pipeline.docket_docx import _md_block, _split_h2
from pipeline.ferc_client import docinfo_url

_DETAIL_LABEL = {"brief": "Brief", "standard": "Standard", "deep": "Deep memo"}


def _scope_text(scope: dict | None) -> str:
    scope = scope or {}
    bits: list[str] = []
    dockets = scope.get("dockets") or []
    if dockets:
        bits.append("FERC docket" + ("s " if len(dockets) > 1 else " ")
                    + ", ".join(d.get("docket_number", "?") for d in dockets))
    elif scope.get("corpus") == "dockets":
        bits.append("All tracked FERC dockets")
    elif scope.get("corpus") == "meetings":
        bits.append("Stakeholder meetings")
    else:
        bits.append("Meetings and tracked FERC dockets")
    if scope.get("depth") == "documents":
        bits.append("summaries + documents")
    return " · ".join(bits)


def _source_line(src: dict) -> tuple[str, str | None]:
    """(label, url) for one serialized source."""
    et = src.get("entity_type")
    if src.get("docket_number"):
        head = f"FERC docket {src['docket_number']}"
        if et == "docket":
            return f"{head} — state of play", None
        bits = [src.get("accession_number") or "filing"]
        if src.get("document_class"):
            bits.append(src["document_class"])
        if src.get("filed_date"):
            bits.append(f"filed {src['filed_date']}")
        if src.get("authors"):
            bits.append(f"by {src['authors']}")
        if src.get("tier") == "document" and src.get("filename"):
            pages = src.get("pages") or []
            pg = f", p. {', '.join(str(p) for p in pages)}" if pages else ""
            bits.append(f"excerpt: {src['filename']}{pg}")
        elif src.get("description"):
            bits.append(str(src["description"])[:140])
        url = docinfo_url(src["accession_number"]) if src.get("accession_number") else None
        return f"{head} — " + " · ".join(bits), url
    head = f"{src.get('type_short') or '?'} meeting {src.get('meeting_date') or ''}".strip()
    if src.get("tier") == "document":
        item = f"agenda {src['item_id']} · " if src.get("item_id") else ""
        return f"{head} — {item}excerpt: {src.get('filename') or 'document'}", None
    if src.get("item_id"):
        return f"{head} — agenda {src['item_id']}: {src.get('item_title') or 'Untitled item'}", None
    return f"{head} — meeting briefing", None


def generate_ask_docx_bytes(ask_id: int) -> tuple[bytes, str]:
    """Render one ask_log exchange; returns (bytes, suggested_filename).
    Raises ValueError when the exchange is missing."""
    row = db.get_ask_log(ask_id)
    if not row:
        raise ValueError(f"Ask #{ask_id} not found")
    question = (row.get("question") or "").strip()
    answer_md = (row.get("answer_md") or "").strip()
    sources = row.get("sources") or []
    scope = row.get("scope") or {}
    created = row.get("created_at")
    when = (created.strftime("%B %-d, %Y") if isinstance(created, (date, datetime))
            else date.today().strftime("%B %-d, %Y"))
    scope_txt = _scope_text(scope)
    prov = [_DETAIL_LABEL.get(row.get("detail") or "", "")]
    if row.get("model_id"):
        prov.append(str(row["model_id"]))
    if row.get("effort"):
        prov.append(f"{row['effort']} effort")
    prov_txt = " · ".join(p for p in prov if p)

    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Inches(8.5); sec.page_height = Inches(11)
    sec.top_margin = brand.MARGIN_TOPBOT; sec.bottom_margin = brand.MARGIN_TOPBOT
    sec.left_margin = brand.MARGIN_SIDE; sec.right_margin = brand.MARGIN_SIDE
    content_w = (sec.page_width - sec.left_margin - sec.right_margin) // 635

    style = doc.styles["Normal"]
    style.font.name = brand.BODY_FONT; style.font.size = brand.SZ_BODY
    style.font.color.rgb = _INK_SOFT
    style.paragraph_format.line_spacing = brand.LINE_SPACING

    sec.different_first_page_header_footer = True
    for part in (sec.first_page_header, sec.first_page_footer, sec.header):
        if part.paragraphs:
            part.paragraphs[0].clear()
    fp = sec.footer.paragraphs[0] if sec.footer.paragraphs else sec.footer.add_paragraph()
    fp.clear()
    pPr = fp._p.get_or_add_pPr()
    tabs = OxmlElement("w:tabs")
    for val, pos in (("center", content_w // 2), ("right", content_w)):
        t = OxmlElement("w:tab"); t.set(qn("w:val"), val); t.set(qn("w:pos"), str(pos))
        tabs.append(t)
    pPr.append(tabs)
    _v2_run(fp, "Ask Poolside", size=brand.SZ_FOOTER, color=_GRAY_TEXT, font=_LABEL)
    fp.add_run("\t")
    _v2_run(fp, "Page ", size=brand.SZ_FOOTER, color=_GRAY_TEXT, font=_LABEL)
    pr = fp.add_run(); pr.font.name = _LABEL; pr.font.size = brand.SZ_FOOTER
    pr.font.color.rgb = _GRAY_TEXT
    _v2_page_number(pr)
    fp.add_run("\t")
    _v2_run(fp, scope_txt, size=brand.SZ_FOOTER, color=_GRAY_TEXT, font=_LABEL)
    _v2_pborder(fp, "top", 6, _CYAN_HEX, space=4)

    if doc.paragraphs:
        doc.paragraphs[0]._p.getparent().remove(doc.paragraphs[0]._p)

    # ── Cover header ────────────────────────────────────────────────────
    p = doc.add_paragraph(); _v2_spacing(p, before=Pt(0), after=Pt(0))
    _v2_pborder(p, "top", 30, _CYAN_HEX)
    p = doc.add_paragraph(); _v2_spacing(p, before=Pt(10), after=Pt(2))
    _v2_run(p, "POOLSIDE RESEARCH DESK", size=brand.SZ_LABEL, bold=True,
            color=_CYAN, font=_LABEL, track=40)
    p = doc.add_paragraph(); _v2_spacing(p, before=Pt(0), after=Pt(6))
    p.paragraph_format.keep_with_next = True
    _v2_run(p, question, size=brand.SZ_HEADLINE, bold=True, color=_INK)
    p = doc.add_paragraph(); _v2_spacing(p, before=Pt(0), after=Pt(6))
    _v2_right_tab(p, pos=content_w)
    _v2_run(p, scope_txt, size=brand.SZ_LINK, color=_CYAN, italic=True)
    p.add_run("\t")
    _v2_run(p, when, size=brand.SZ_LINK, color=_GRAY_TEXT)
    p = doc.add_paragraph(); _v2_spacing(p, before=Pt(0), after=Pt(0))
    _v2_pborder(p, "bottom", 4, _GRAY_MID_HEX)
    if prov_txt:
        p = doc.add_paragraph(); _v2_spacing(p, before=Pt(6), after=Pt(0))
        _v2_run(p, f"{prov_txt} · {len(sources)} source"
                   f"{'s' if len(sources) != 1 else ''} · citations [n] resolve "
                   "in the Sources list at the end",
                size=brand.SZ_LINK, color=_GRAY_TEXT, font=_LABEL)

    # ── Answer ──────────────────────────────────────────────────────────
    preamble, sections = _split_h2(answer_md)
    if preamble:
        _md_block(doc, preamble.splitlines())
    for heading, body in sections:
        _eyebrow(doc, heading.upper())
        _md_block(doc, body)

    # ── Sources ─────────────────────────────────────────────────────────
    if sources:
        _eyebrow(doc, "SOURCES")
        for src in sources:
            label, url = _source_line(src)
            p = doc.add_paragraph()
            _v2_spacing(p, before=Pt(0), after=Pt(3), line=brand.LINE_SPACING)
            p.paragraph_format.left_indent = Pt(22)
            p.paragraph_format.first_line_indent = Pt(-22)
            _v2_run(p, f"[{src.get('n', '?')}]  ", size=brand.SZ_LINK, bold=True,
                    color=_CYAN, font=_LABEL)
            _inline_runs(p, label, size=brand.SZ_LINK, color=_INK_SOFT)
            if url:
                p.add_run("  ")
                _v2_link(p, url, "eLibrary", size=brand.SZ_LINK, color=_CYAN)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", question)[:48].strip("-") or "answer"
    return buf.read(), f"Ask_{ask_id}_{slug}.docx"
