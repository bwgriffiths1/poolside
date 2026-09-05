"""Repeated-ID agendas (ISO-NE multi-day joint meetings).

ISO-NE labels every session of a joint-meeting initiative with the same ID
("4.0*" on all 15 CAR-SA rows of the Sept 2026 MC) and opens each day's table
with a banner row carrying that ID. The fixtures are the real posted agendas
for the Sept 8-10 and Aug 11-13 2026 Markets Committee meetings.

Regression for prod meeting 161 (Sept 2026 MC): regex lettered the rows
4.a-4.o *including* the banners (so letters were off by one from the ISO's
a04a_*, a04b_* material names), the LLM numbered them 4.1-4.13, and the
id-keyed union merge kept both sets — 28 children under a "(no title)" parent.
"""
from pathlib import Path

import pytest

from pipeline.agenda_parser import parse_agenda_from_docx
from pipeline.llm_agenda_parser import _merge_results, reconcile_results

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sept():
    return parse_agenda_from_docx((FIXTURES / "mc_2026_09_08_agenda.docx").read_bytes())


@pytest.fixture(scope="module")
def aug():
    return parse_agenda_from_docx((FIXTURES / "mc_2026_08_11_agenda.docx").read_bytes())


# ── regex parser ─────────────────────────────────────────────────────────

def test_sept_banner_becomes_bare_parent(sept):
    by_id = {it["item_id"]: it for it in sept}
    parent = by_id["4"]
    assert parent["title"] == "CAPACITY AUCTION REFORMS – SEASONAL / ACCREDITATION"
    assert parent["prefix"] == "a04"
    assert parent["wmpp_id"] == "185"
    assert parent["vote_status"] == "Future Vote"
    assert parent.get("auto_sub") is False


def test_sept_second_day_banner_dropped(sept):
    titles = [it["title"] for it in sept if it["item_id"].startswith("4")]
    assert titles.count("CAPACITY AUCTION REFORMS – SEASONAL / ACCREDITATION") == 1
    assert not any(t.upper().startswith("JOINT MEETING") for t in titles)


def test_sept_sessions_lettered_to_match_iso_material_names(sept):
    children = [it for it in sept if it["item_id"].startswith("4.")]
    assert [it["item_id"] for it in children] == [
        f"4.{c}" for c in "abcdefghijklm"
    ]
    # Spot-check against the ISO's own file prefixes for this meeting
    by_id = {it["item_id"]: it["title"] for it in children}
    assert by_id["4.a"] == "CAR-SA Summary: Design Updates"        # a04a_*
    assert by_id["4.b"] == "Transition Mechanism"                  # a04b_* (", cont" stripped)
    assert by_id["4.e"] == "Vistra Conceptual Amendment"           # a04e_*
    assert by_id["4.h"] == "Constellation Conceptual Amendment"    # a04h_*
    assert by_id["4.m"] == "Tariff Review"                         # a04m_*
    assert all(it["prefix"] == "a04." + it["item_id"][-1] for it in children)


def test_sept_whole_agenda_shape(sept):
    assert [it["item_id"] for it in sept] == (
        ["1", "1.A", "2", "3", "4"] + [f"4.{c}" for c in "abcdefghijklm"] + ["5", "6"]
    )


def test_aug_same_pattern(aug):
    ids = [it["item_id"] for it in aug]
    assert ids == ["1", "1.A", "2"] + [f"2.{c}" for c in "abcdefghijkl"] + ["3", "4"]
    by_id = {it["item_id"]: it["title"] for it in aug}
    assert by_id["2"] == "CAPACITY AUCTION REFORMS – SEASONAL / ACCREDITATION"
    assert by_id["2.a"] == "Stakeholder Process Memo"
    # "Tariff Review, cont" on day 3 folds into 2.l rather than becoming 2.m
    assert by_id["2.l"] == "Tariff Review"


# ── merge with an LLM that numbered instead of lettered ──────────────────

def _llm_numbered(regex_items: list[dict]) -> list[dict]:
    """Simulate the Sept 2026 LLM output: parent as '4', sessions as 4.1…4.13
    with title-cased titles and richer metadata."""
    out = []
    n = 0
    for it in regex_items:
        if it["item_id"] == "4":
            out.append({"item_id": "4", "title": "Capacity Auction Reforms – Seasonal / Accreditation",
                        "wmpp_id": "185", "vote_status": "Future Vote",
                        "initiative_codes": ["CAR-SA"]})
        elif it["item_id"].startswith("4."):
            n += 1
            out.append({"item_id": f"4.{n}", "title": it["title"].title(),
                        "presenter": it.get("presenter"), "org": it.get("org"),
                        "initiative_codes": ["CAR-SA"]})
        else:
            out.append({"item_id": it["item_id"], "title": it["title"].title()})
    return out


def test_reconcile_pairs_renumbered_rows_by_title(sept):
    llm = _llm_numbered(sept)
    audit = reconcile_results(sept, llm)
    assert audit["regex_only"] == []
    assert audit["llm_only"] == []
    assert len(audit["matched"]) == len(sept)
    # No structural disagreement → llm_verify must not escalate
    assert len(audit["regex_only"]) + len(audit["llm_only"]) <= 2


def test_merge_does_not_duplicate_renumbered_rows(sept):
    llm = _llm_numbered(sept)
    merged = _merge_results(sept, llm, reconcile_results(sept, llm))
    assert [it["item_id"] for it in merged] == [it["item_id"] for it in sept]
    by_id = {it["item_id"]: it for it in merged}
    # regex ids/prefixes win, LLM metadata enriches
    assert by_id["4.e"]["prefix"] == "a04.e"
    assert by_id["4.e"]["title"] == "Vistra Conceptual Amendment"
    assert by_id["4.e"]["initiative_codes"] == ["CAR-SA"]
    assert by_id["4"]["title"] == "Capacity Auction Reforms – Seasonal / Accreditation"


def test_merge_still_adds_genuinely_new_llm_items(sept):
    llm = _llm_numbered(sept) + [{"item_id": "7", "title": "Executive Session"}]
    merged = _merge_results(sept, llm, reconcile_results(sept, llm))
    assert merged[-1]["item_id"] == "7"
    assert merged[-1]["prefix"] == "a07"
    assert len(merged) == len(sept) + 1


def test_merge_by_id_still_works_when_llm_letters(sept):
    llm = [{"item_id": it["item_id"].upper() if it["item_id"] == "4.a" else it["item_id"],
            "title": it["title"].title()} for it in sept]
    merged = _merge_results(sept, llm, reconcile_results(sept, llm))
    assert len(merged) == len(sept)
