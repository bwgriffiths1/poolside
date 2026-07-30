"""Venue-qualified committee labels in the Word export.

The cover masthead reads "PJM Operating Committee" (two ISOs both have an
OC), while the running footer takes a compact `footer_label` ("PJM OC") —
it's a one-line tabbed layout that a CIFP-length committee name overflows.
Omitting footer_label keeps the old behavior (committee in the footer).
"""
import io
import zipfile
from pathlib import Path

import pytest

from api.briefing_parser import parse_briefing_markdown
from pipeline.briefing import render_briefing_docx

FIXTURES = Path(__file__).parent / "fixtures"

LONG_NAME = "PJM Critical Issue Fast Path - Reliability Backstop Procurement"


@pytest.fixture(scope="module")
def briefing():
    md = (FIXTURES / "briefing_2026_format.md").read_text()
    return parse_briefing_markdown(md, {"title": LONG_NAME})


def _parts(blob: bytes) -> tuple[str, str]:
    """(document.xml, all footer XML concatenated)."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    doc = z.read("word/document.xml").decode()
    footers = "".join(
        z.read(n).decode() for n in z.namelist()
        if n.startswith("word/footer")
    )
    return doc, footers


def test_footer_uses_compact_label(briefing):
    blob = render_briefing_docx(
        briefing, LONG_NAME, ["2026-04-16"], footer_label="PJM CIFP-RBP",
    )
    doc, footers = _parts(blob)
    assert LONG_NAME in doc                 # cover masthead, venue-qualified
    assert "PJM CIFP-RBP" in footers        # compact footer
    assert LONG_NAME not in footers         # the overflow case is gone


def test_footer_falls_back_to_committee(briefing):
    blob = render_briefing_docx(briefing, "ISO-NE Markets Committee",
                                ["2026-04-16"])
    _, footers = _parts(blob)
    assert "ISO-NE Markets Committee" in footers
