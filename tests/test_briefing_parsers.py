"""Golden-fixture tests for briefing parsing and Word rendering.

There is now exactly ONE briefing-markdown parser
(api/briefing_parser.parse_briefing_markdown); the docx exporter walks its
AST (pipeline/briefing.render_briefing_docx). These tests pin the parse on
two fixtures (current format + a legacy briefing taken from a real stored
one) and assert the rendered .docx carries every piece of parsed content —
the property whose absence previously let the Word export silently drop
Key Takeaways.
"""
import io
import zipfile
from pathlib import Path

import pytest

from api.briefing_parser import parse_briefing_markdown
from pipeline.briefing import render_briefing_docx

FIXTURES = Path(__file__).parent / "fixtures"


def _docx_xml(briefing) -> str:
    blob = render_briefing_docx(briefing, "Markets Committee", ["2025-11-04"])
    return zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode()


@pytest.fixture(scope="module")
def new_format():
    md = (FIXTURES / "briefing_2026_format.md").read_text()
    b = parse_briefing_markdown(md, {"title": "Markets Committee"})
    return b, _docx_xml(b)


@pytest.fixture(scope="module")
def legacy_format():
    md = (FIXTURES / "briefing_legacy_format.md").read_text()
    b = parse_briefing_markdown(md, {"title": "Reliability Committee"})
    return b, _docx_xml(b)


# ── 2026 format (## Key Takeaways / ## n — Title / ### n.x — Title) ──────


def test_takeaways_parsed(new_format):
    b, _ = new_format
    assert len(b.tldr) == 3
    assert b.tldr[0].startswith("**CAR-SA vote delayed**")


def test_section_ids_depths_titles(new_format):
    b, _ = new_format
    got = [(s.item_id, s.depth) for s in b.sections]
    assert got == [("3", 0), ("3.a", 1), ("3.b", 1), ("7", 0)]


def test_next_steps_both_spellings(new_format):
    """Bullet form ('**Next Steps:**' + '- ...') and inline form
    ('**Next Steps:** a; b') both populate section.next_steps."""
    b, _ = new_format
    ns = {s.item_id: s.next_steps for s in b.sections if s.next_steps}
    assert ns == {
        "3.a": ["Revised proposal returns in December",
                "Written comments due November 21"],
        "7": ["File with FERC in Q1 2026", "implementation guide to follow"],
    }
    for s in b.sections:
        for blk in s.body:
            assert "Next Steps" not in getattr(blk, "text", "")


def test_typed_blocks_parsed(new_format):
    b, _ = new_format
    sec = next(s for s in b.sections if s.item_id == "3.a")
    kinds = [blk.kind for blk in sec.body]
    assert "data" in kinds and "h" in kinds
    table = next(blk for blk in sec.body if blk.kind == "data")
    assert table.rows[0] == ["Technology", "Floor ($/kW-mo)"]
    assert b.executive_summary, "exec summary blocks missing"


def test_docx_carries_every_parsed_element(new_format):
    """The renderer consumes the AST — everything parsed must reach the XML."""
    b, xml = new_format
    for tk in b.tldr:
        probe = tk.replace("**", "").split(" to ")[0].split(";")[0][:30]
        assert probe in xml, f"takeaway lost: {probe}"
    for s in b.sections:
        assert s.title[:30] in xml, f"section lost: {s.title}"
        for step in s.next_steps or []:
            assert step[:30] in xml, f"next step lost: {step}"
    for needle in (
        "KEY TAKEAWAYS", "EXECUTIVE SUMMARY", "AGENDA ITEM SUMMARIES",
        "NEXT STEPS",
        "Solar PV",                      # table cell
        "Study assumptions",             # h block
        "WATCH",                         # callout label, uppercased
        "capacity auction reform",       # exec prose
    ):
        assert needle in xml, f"docx lost: {needle}"


# ── Legacy format (### Item N: Title, no takeaways section) ──────────────


def test_legacy_sections_parse_and_render(legacy_format):
    b, xml = legacy_format
    assert [s.item_id for s in b.sections] == ["2", "3", "5"]
    assert [s.depth for s in b.sections] == [0, 0, 0]  # stays flat on web
    for s in b.sections:
        assert s.title[:30] in xml, f"legacy section lost from docx: {s.title}"


def test_legacy_has_no_takeaways(legacy_format):
    b, xml = legacy_format
    assert b.tldr == []  # prose-only exec summary → no bullet fallback
    assert "KEY TAKEAWAYS" not in xml  # section omitted entirely, not empty


def test_dot_numbered_sections_parse():
    """Stored briefings from 2025 emit '### 1. TITLE' (dot separator).
    These parsed to ZERO sections before _SECTION_HEAD_DOT — the web reader
    showed only the exec summary and the docx lost the bodies."""
    md = (
        "## Executive Summary\n\nIntro paragraph.\n\n"
        "## Agenda Item Summaries\n\n"
        "### 1. CHAIR'S OPENING REMARKS\n\nBody A.\n\n"
        "### 2. LOAD FORECAST REVISIONS & DRIVERS\n\nBody B.\n"
    )
    b = parse_briefing_markdown(md, {"title": "X"})
    assert [(s.item_id, s.title) for s in b.sections] == [
        ("1", "CHAIR'S OPENING REMARKS"),
        ("2", "LOAD FORECAST REVISIONS & DRIVERS"),
    ]
    xml = _docx_xml(b)
    assert "CHAIR'S OPENING REMARKS" in xml and "Body B." in xml
