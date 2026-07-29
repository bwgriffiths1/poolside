"""pipeline/pjm_scraper.py — pure parsing pinned against a live capture.

The fixture is a trimmed copy of https://www.pjm.com/committees-and-groups/cifp-rbp
captured 2026-07-29. It deliberately preserves two markup quirks the parser
must survive: the nested duplicate anchor inside each material row, and the
materials section sitting under an unclosed <link> tag.
"""
from datetime import date
from pathlib import Path

import pytest

from pipeline.pjm_scraper import (
    filename_from_media_url,
    map_pjm_docs_to_agenda_items,
    parse_committee_page,
    pjm_external_id,
    pjm_item_number_from_filename,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pjm_cifp_rbp.html"
CIFP_URL = "https://www.pjm.com/committees-and-groups/cifp-rbp"


@pytest.fixture(scope="module")
def parsed():
    return parse_committee_page(FIXTURE.read_text(), CIFP_URL)


# ── committee page: meetings ────────────────────────────────────────────

def test_all_meeting_entries_parsed(parsed):
    dates = [m["date"] for m in parsed["meetings"]]
    assert len(dates) == 8
    assert date(2026, 4, 16) in dates
    assert date(2026, 4, 17) in dates  # separate entry, never merged
    assert date(2026, 6, 30) in dates


def test_meeting_titles(parsed):
    by_date = {m["date"]: m for m in parsed["meetings"]}
    assert (
        by_date[date(2026, 4, 16)]["title"]
        == "Critical Issue Fast Path - Reliability Backstop Procurement"
    )
    # Later meetings carry the expanded scope title
    assert "Connect and Manage" in by_date[date(2026, 6, 30)]["title"]


def test_416_documents_complete(parsed):
    m = next(x for x in parsed["meetings"] if x["date"] == date(2026, 4, 16))
    docs = m["documents"]
    assert len(docs) == 9  # agenda pdf + agenda docx + 7 item files
    filenames = {d["filename"] for d in docs}
    assert "20260416-agenda.pdf" in filenames
    assert "20260416-agenda-doc.docx" in filenames
    assert "20260416-item-06---next-steps.pdf" in filenames
    # the .xls matrix is stored even though the summarizer skips .xls text
    assert any(f.endswith(".xls") for f in filenames)


def test_nested_duplicate_anchor_deduped(parsed):
    """Each row nests a badge <a> duplicating the title href — one doc per row."""
    m = next(x for x in parsed["meetings"] if x["date"] == date(2026, 4, 16))
    urls = [d["url"] for d in m["documents"]]
    assert len(urls) == len(set(urls))


def test_document_fields(parsed):
    m = next(x for x in parsed["meetings"] if x["date"] == date(2026, 4, 16))
    agenda = next(d for d in m["documents"] if d["filename"] == "20260416-agenda.pdf")
    assert agenda["url"].startswith("https://www.pjm.com/-/media/")
    assert agenda["title"] == "Agenda"
    assert agenda["posted_date"] == date(2026, 4, 10)
    assert agenda["media_id"]  # stable Sitecore GUID
    assert agenda["ext"] == ".pdf"
    work_plan = next(d for d in m["documents"] if "item-03" in d["filename"])
    assert work_plan["title"] == "Item 03 - CIFP - RBP Draft Work Plan"


# ── committee page: upcoming ────────────────────────────────────────────

def test_upcoming_meetings(parsed):
    assert len(parsed["upcoming"]) == 1
    up = parsed["upcoming"][0]
    assert up["date"] == date(2026, 7, 31)
    assert up["title"] == "Post CIFP Workshop - Technical Overview"
    assert up["location"] == "Webex"


# ── url / id helpers ────────────────────────────────────────────────────

def test_filename_from_media_url():
    assert (
        filename_from_media_url(
            "https://www.pjm.com/-/media/DotCom/committees-groups/cifp-rbp/2026/"
            "20260416/20260416-agenda.pdf"
        )
        == "20260416-agenda.pdf"
    )
    # query strings and percent-escapes stripped/decoded, case folded
    assert filename_from_media_url("/x/Some%20File.PDF?la=en") == "some file.pdf"


def test_pjm_external_id():
    assert pjm_external_id("cifp-rbp", date(2026, 4, 16)) == "pjm-cifp-rbp-20260416"


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("20260416-item-03---cifp---rbp-draft-work-plan.pdf", "3"),
        ("20260416-item-05---pjm-presentation.pdf", "5"),
        ("20260416-item-10---something.pdf", "10"),
        ("item-04-issue-charge.pdf", "4"),
        ("20260416-agenda.pdf", None),
        ("20260416-agenda-doc.docx", None),
        ("consent-agenda-item-listing.pdf", None),  # "item-" with no digits
    ],
)
def test_pjm_item_number_from_filename(filename, expected):
    assert pjm_item_number_from_filename(filename) == expected


# ── deterministic doc→item mapping ──────────────────────────────────────

ITEMS = [
    {"id": 11, "item_id": "1", "prefix": "a01", "title": "Administration"},
    {"id": 13, "item_id": "3", "prefix": "a03", "title": "Work Plan"},
    {"id": 14, "item_id": "4", "prefix": "a04", "title": "Problem Statement"},
    {"id": 15, "item_id": "5", "prefix": None, "title": "PJM Proposal"},  # prefix derived
]


def test_map_pjm_docs_bucket_contract():
    docs = [
        {"filename": "20260416-item-03---cifp---rbp-draft-work-plan.pdf"},
        {"filename": "20260416-item-04---cifp---rbp-issue-charge.pdf"},
        {"filename": "20260416-item-04---cifp---rbp-problem-statement.pdf"},
        {"filename": "20260416-item-05---pjm-proposal---paper.pdf"},
        {"filename": "20260416-agenda.pdf"},
        {"filename": "20260416-item-09---no-such-item.pdf"},
    ]
    buckets = map_pjm_docs_to_agenda_items(docs, ITEMS)
    assert [d["filename"] for d in buckets["a03"]] == [docs[0]["filename"]]
    assert len(buckets["a04"]) == 2
    # item 5 has no stored prefix — derived via item_id_to_prefix
    assert [d["filename"] for d in buckets["a05"]] == [docs[3]["filename"]]
    # agenda + unknown item number stay in "other" for the LLM fallback pass
    other = {d["filename"] for d in buckets["other"]}
    assert other == {"20260416-agenda.pdf", "20260416-item-09---no-such-item.pdf"}


def test_map_pjm_docs_empty_items():
    buckets = map_pjm_docs_to_agenda_items(
        [{"filename": "20260416-item-03---x.pdf"}], []
    )
    assert buckets == {"other": [{"filename": "20260416-item-03---x.pdf"}]}


# ── prompt contract guard (the silent-fallback trap) ────────────────────

PROMPTS = Path(__file__).parent.parent / "prompts"


def test_pjm_prompt_files_exist():
    """Missing pjm_* prompt files silently fall back to isone_mc (NEPOOL
    framing) in summarizer._get_committee_prompts — pin their existence."""
    assert (PROMPTS / "pjm_cifp-rbp_briefing_prompt.md").exists()
    assert (PROMPTS / "pjm_cifp-rbp_agenda_item_prompt.md").exists()


def test_pjm_briefing_prompt_structural_contract():
    text = (PROMPTS / "pjm_cifp-rbp_briefing_prompt.md").read_text()
    assert "[AGENDA ITEMS]" in text
    assert "## Agenda Item Summaries" in text
    assert "## Key Takeaways" in text
    assert "## Executive Summary" in text


def test_pjm_agenda_item_prompt_placeholders():
    text = (PROMPTS / "pjm_cifp-rbp_agenda_item_prompt.md").read_text()
    for placeholder in ("{item_id}", "{title}", "{doc_summaries}"):
        assert placeholder in text
