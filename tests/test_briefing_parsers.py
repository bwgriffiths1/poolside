"""Golden-fixture sync tests for the two briefing-markdown parsers.

api/briefing_parser.py (web/typed AST) and pipeline/briefing.py's
_v2_parse_briefing_md (docx export) parse the same stored markdown and must
agree on content. This suite is the enforcement for that obligation — it has
already caught two real divergences (docx dropping Key Takeaways; the web
parser missing both real-world **Next Steps:** spellings).
"""
from pathlib import Path

import pytest

from api.briefing_parser import parse_briefing_markdown
from pipeline.briefing import _v2_parse_briefing_md, generate_docx_bytes

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def new_format():
    md = (FIXTURES / "briefing_2026_format.md").read_text()
    return md, parse_briefing_markdown(md, {"title": "Markets Committee"}), _v2_parse_briefing_md(md)


@pytest.fixture(scope="module")
def legacy_format():
    md = (FIXTURES / "briefing_legacy_format.md").read_text()
    return md, parse_briefing_markdown(md, {"title": "Reliability Committee"}), _v2_parse_briefing_md(md)


# ── 2026 format (## Key Takeaways / ## n — Title / ### n.x — Title) ──────


def test_takeaways_agree(new_format):
    _, web, docx = new_format
    assert len(web.tldr) == 3
    assert web.tldr == docx["takeaways"]


def test_sections_agree_on_ids_depths_titles(new_format):
    _, web, docx = new_format
    web_items = [(s.item_id, s.depth, s.title) for s in web.sections]
    docx_items = [(it["number"], it["depth"], it["title"]) for it in docx["items"]]
    assert web_items == docx_items
    assert [d for _, d, _ in web_items] == [0, 1, 1, 0]


def test_exec_summary_present_in_both(new_format):
    _, web, docx = new_format
    assert web.executive_summary, "web parser lost the executive summary"
    assert docx["exec_summary"], "docx parser lost the executive summary"


def test_next_steps_agree_in_both_spellings(new_format):
    """Bullet form ('**Next Steps:**' + '- ...') and inline form
    ('**Next Steps:** a; b') must both parse, identically, in both parsers."""
    _, web, docx = new_format
    web_ns = {s.item_id: s.next_steps for s in web.sections if s.next_steps}
    docx_ns = {it["number"]: it["next_steps"] for it in docx["items"] if it["next_steps"]}
    assert web_ns == docx_ns
    assert set(web_ns) == {"3.a", "7"}


def test_next_steps_marker_not_left_in_web_body(new_format):
    _, web, _ = new_format
    for s in web.sections:
        for blk in s.body:
            assert "Next Steps" not in getattr(blk, "text", "")


def test_web_parses_data_table(new_format):
    _, web, _ = new_format
    sec = next(s for s in web.sections if s.item_id == "3.a")
    tables = [b for b in sec.body if b.kind == "data"]
    assert tables and tables[0].rows[0] == ["Technology", "Floor ($/kW-mo)"]


def test_docx_bytes_contain_all_sections(new_format):
    md, _, _ = new_format
    import io
    import zipfile

    blob = generate_docx_bytes(md, "Markets Committee", ["2025-11-04"])
    xml = zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode()
    for needle in (
        "KEY TAKEAWAYS",
        "CAR-SA vote delayed",
        "EXECUTIVE SUMMARY",
        "ORTP Floor Proposal",
        "Accreditation Transition Schedule",
        "Demand Response Aggregation",
        "NEXT STEPS",
    ):
        assert needle in xml, f"docx lost: {needle}"


# ── Legacy format (### Item N: Title, no takeaways section) ──────────────


def test_legacy_section_counts_agree(legacy_format):
    _, web, docx = legacy_format
    assert len(web.sections) == len(docx["items"]) == 3
    # Titles agree even though the two parsers normalize numbering
    # differently ('5' vs 'Item 5') — silent section DROPS are the bug class
    # this guards against.
    assert [s.title for s in web.sections] == [it["title"] for it in docx["items"]]


def test_legacy_has_no_takeaways(legacy_format):
    _, web, docx = legacy_format
    assert docx["takeaways"] == []
    assert web.tldr == []  # prose-only exec summary → no bullet fallback


def test_legacy_web_depths_stay_flat(legacy_format):
    """Backward-compat promise: '### Item N' briefings render depth 0 on web."""
    _, web, _ = legacy_format
    assert [s.depth for s in web.sections] == [0, 0, 0]
