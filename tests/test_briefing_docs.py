"""Distribution of a meeting's documents across briefing headings.

adapters.attach_briefing_docs decides which heading each file is listed
under. The rule that matters: a sub-item's materials belong to the sub-item's
own heading (7.a's deck under "7.a — …"), not piled onto the parent section.
DB access is stubbed so these stay unit tests.
"""
import pytest

from api import adapters, briefing_parser


def _briefing(md: str):
    return briefing_parser.parse_briefing_markdown(md, {"title": "MC"})


@pytest.fixture
def fake_db(monkeypatch):
    """Install an agenda + documents fixture behind adapters' db lookups."""
    def install(agenda: list[tuple[str, str, list[str]]]):
        items = [
            {"id": i, "item_id": item_id, "title": title}
            for i, (item_id, title, _) in enumerate(agenda, 1)
        ]
        docs = {
            i: [
                {"id": 100 * i + n, "filename": f, "source_url": f"https://x/{f}"}
                for n, f in enumerate(files)
            ]
            for i, (_, _, files) in enumerate(agenda, 1)
        }
        from pipeline import db

        monkeypatch.setattr(db, "get_agenda_items", lambda _mid: items)
        monkeypatch.setattr(db, "get_documents_for_item", lambda iid: docs[iid])

    return install


SUBITEM_MD = """## Agenda Item Summaries

### Item 7 — Capacity Auction Reforms

Intro prose for the omnibus item.

#### 7.a — Resource Qualification

Body A.

#### 7.b — Activity Schedule

Body B.
"""


def _subheads(briefing):
    return {
        b.item_id: b
        for s in briefing.sections
        for b in s.body
        if getattr(b, "kind", "") == "h" and b.item_id
    }


def test_subitem_docs_land_on_their_own_heading(fake_db):
    fake_db([
        ("7", "Capacity Auction Reforms", ["a07_overview.pdf"]),
        ("7.a", "Resource Qualification", ["a07a_qual.pdf"]),
        ("7.b", "Activity Schedule", ["a07b_schedule.pdf"]),
    ])
    b = _briefing(SUBITEM_MD)
    adapters.attach_briefing_docs(b, 1)

    section = b.sections[0]
    subs = _subheads(b)
    assert [d.filename for d in section.docs] == ["a07_overview.pdf"]
    assert [d.filename for d in subs["7.a"].docs] == ["a07a_qual.pdf"]
    assert [d.filename for d in subs["7.b"].docs] == ["a07b_schedule.pdf"]
    assert b.other_docs == []


def test_deeper_items_roll_up_to_nearest_written_heading(fake_db):
    """7.a.ii has no heading of its own — it belongs to 7.a, not 7."""
    fake_db([
        ("7.a", "Resource Qualification", ["a07a_qual.pdf"]),
        ("7.a.ii", "Sub-sub item", ["a07aii_appendix.pdf"]),
        ("7.z", "Unwritten sub-item", ["a07z_late.pdf"]),
    ])
    b = _briefing(SUBITEM_MD)
    adapters.attach_briefing_docs(b, 1)

    subs = _subheads(b)
    assert [d.filename for d in subs["7.a"].docs] == [
        "a07a_qual.pdf",
        "a07aii_appendix.pdf",
    ]
    # 7.z has no heading either, so it rolls up to section 7.
    assert [d.filename for d in b.sections[0].docs] == ["a07z_late.pdf"]


def test_case_differences_still_match(fake_db):
    """The agenda writes 1.A; briefings write 1.a."""
    fake_db([("7.A", "Resource Qualification", ["a07a_qual.pdf"])])
    b = _briefing(SUBITEM_MD)
    adapters.attach_briefing_docs(b, 1)
    assert [d.filename for d in _subheads(b)["7.a"].docs] == ["a07a_qual.pdf"]


def test_unmatched_docs_fall_through_to_other_docs(fake_db):
    """Meeting-level files and items the briefing skipped are still listed."""
    fake_db([
        ("", "General / Meeting-level Documents", ["agenda.docx"]),
        ("1", "Approval of Minutes", ["a01_minutes.docx"]),
        ("7.a", "Resource Qualification", ["a07a_qual.pdf"]),
    ])
    b = _briefing(SUBITEM_MD)
    adapters.attach_briefing_docs(b, 1)

    assert [d.filename for d in b.other_docs] == ["agenda.docx", "a01_minutes.docx"]
    assert [d.filename for d in _subheads(b)["7.a"].docs] == ["a07a_qual.pdf"]


def test_no_document_is_dropped(fake_db):
    """Every file reaches exactly one place — the old reader capped at 24."""
    agenda = [(f"7.{chr(97 + n)}", f"Sub {n}", [f"doc{n}.pdf"]) for n in range(30)]
    fake_db(agenda)
    b = _briefing(SUBITEM_MD)
    adapters.attach_briefing_docs(b, 1)

    placed = (
        [d.filename for s in b.sections for d in s.docs]
        + [d.filename for blk in _subheads(b).values() for d in blk.docs]
        + [d.filename for d in b.other_docs]
    )
    assert sorted(placed) == sorted(f"doc{n}.pdf" for n in range(30))


def test_prose_subheadings_get_no_docs(fake_db):
    """A bold sub-head like "Key Developments" isn't an item anchor."""
    fake_db([("7", "Capacity Auction Reforms", ["a07_overview.pdf"])])
    b = _briefing(
        "## Agenda Item Summaries\n\n"
        "### Item 7 — Capacity Auction Reforms\n\n"
        "#### Background\n\nProse.\n"
    )
    adapters.attach_briefing_docs(b, 1)

    heads = [b_ for s in b.sections for b_ in s.body if getattr(b_, "kind", "") == "h"]
    assert heads and all(h.item_id == "" and h.docs == [] for h in heads)
    assert [d.filename for d in b.sections[0].docs] == ["a07_overview.pdf"]


RANGE_MD = """## Agenda Item Summaries

### 3.1.a–c — CAR-PD Tariff Revisions

Body A.

### Items 8–9 — Reports

Body B.
"""


def test_range_headings_claim_every_item_they_cover(fake_db):
    """One heading can cover consecutive sub-items. Without expanding the
    range none of those ids match and the group's documents all orphan."""
    fake_db([
        ("3.1.a", "Tariff revisions", ["a.pdf"]),
        ("3.1.b", "Counsel assessment", ["b.pdf"]),
        ("3.1.c", "Final design", ["c.pdf"]),
        ("8", "Litigation report", ["eight.pdf"]),
        ("9", "Committee reports", ["nine.pdf"]),
    ])
    b = _briefing(RANGE_MD)
    adapters.attach_briefing_docs(b, 1)

    by_id = {s.item_id: s for s in b.sections}
    assert [d.filename for d in by_id["3.1.a–c"].docs] == ["a.pdf", "b.pdf", "c.pdf"]
    assert [d.filename for d in by_id["8–9"].docs] == ["eight.pdf", "nine.pdf"]
    assert b.other_docs == []


def test_malformed_ranges_stay_literal(fake_db):
    """A mixed digit/letter span isn't a range — treat the id as written."""
    assert adapters._covered_item_ids("7-a") == ["7-a"]
    assert adapters._covered_item_ids("1.A") == ["1.A"]
    assert adapters._covered_item_ids("9–8") == ["9–8"]  # reversed: no expansion
