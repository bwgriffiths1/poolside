"""pipeline/docket_ingest.py — classification rules and pure helpers.

DB-touching flows (sync_docket) are exercised in the live E2E; these tests
pin the pure classification logic that decides what gets LLM spend.
"""
from datetime import date

import pytest

from pipeline.docket_ingest import (
    DEFAULT_TREATMENT_MAP,
    _parse_date,
    _split_tldr,
    author_orgs,
    classify_treatment,
    extract_parties,
    file_included,
    is_docless,
    load_ferc_config,
    normalize_docket_number,
)

CFG = {"treatment_map": DEFAULT_TREATMENT_MAP}


# ── docket number normalization ─────────────────────────────────────────

def test_normalize_docket_number():
    assert normalize_docket_number(" er26-925 ") == "ER26-925"
    assert normalize_docket_number("RM24-3-001") == "RM24-3-001"


@pytest.mark.parametrize("bad", ["", "not a docket", "ER26925", "26-925"])
def test_normalize_rejects_garbage(bad):
    with pytest.raises(ValueError):
        normalize_docket_number(bad)


# ── treatment map ───────────────────────────────────────────────────────

@pytest.mark.parametrize("cls,tier", [
    ("Application/Petition/Request", "full"),
    ("Comments/Protest", "full"),
    ("Order/Opinion", "full"),
    ("ALJ Issuance", "full"),
    ("Pleading/Motion", "brief"),
    ("Report/Form", "brief"),
    ("Notice", "skip"),
    ("Transcript", "skip"),
])
def test_default_treatment_map(cls, tier):
    assert classify_treatment(cls, CFG) == tier


def test_docless_forces_skip_for_any_class():
    assert classify_treatment("Intervention", CFG, is_docless=True) == "skip"
    assert classify_treatment("Comments/Protest", CFG, is_docless=True) == "skip"


def test_docful_intervention_is_substantive():
    """A motion to intervene PAIRED with a protest/comments (real PDF —
    e.g. FirstLight in ER26-3047) must be summarized, not roster-only."""
    assert classify_treatment(
        "Intervention", CFG,
        description="Motion to Intervene and Protest of FirstLight under ER26-3047.",
    ) == "full"


def test_unknown_class_falls_back_to_brief():
    """New FERC classes must never be silently dropped."""
    assert classify_treatment("Some Future Class", CFG) == "brief"
    assert classify_treatment(None, CFG) == "brief"


@pytest.mark.parametrize("desc", [
    "Avangrid Networks, Inc. et al. submit request to update the service list in ER26-925.",
    "Notice of Substitution of Counsel of Avangrid Networks under ER26-925.",
    "SEIA submits request for removal from official service lists.",
    "Notice of Appearance of Jane Q. Counsel in ER26-925.",
    "Notice of Withdrawal of Appearance under ER26-925.",
    "Combined Notice of Filings #1, December 31, 2025.",
])
def test_administrative_descriptions_force_skip(desc):
    """Counsel/service-list housekeeping must not burn LLM tokens or clutter
    the timeline, whatever class FERC filed it under."""
    assert classify_treatment("Pleading/Motion", CFG, description=desc) == "skip"


def test_substantive_motion_stays_brief():
    assert classify_treatment(
        "Pleading/Motion", CFG,
        description="Motion for Extension of Time to file comments of NEPGA.",
    ) == "brief"


def test_config_override_wins():
    cfg = {"treatment_map": {**DEFAULT_TREATMENT_MAP,
                             "skip": ["Report/Form"], "brief": []}}
    assert classify_treatment("Report/Form", cfg) == "skip"


def test_load_ferc_config_merges_defaults():
    cfg = load_ferc_config()
    assert "full" in cfg["treatment_map"]
    assert "Notice" in cfg["treatment_map"]["skip"]
    # Intervention is deliberately in NO static tier: doc-less → skip,
    # doc-ful (motion + protest) → full, decided per filing.
    assert not any("Intervention" in (cfg["treatment_map"][t] or [])
                   for t in ("full", "brief", "skip"))


# ── doc-less detection ──────────────────────────────────────────────────

def test_docless_by_description_prefix():
    assert is_docless({"description": "(doc-less) Motion to Intervene of X.",
                       "transmittals": [{"fileType": "TXT"}]})


def test_docless_by_txt_only_transmittals():
    assert is_docless({"description": "Motion to Intervene of X.",
                       "transmittals": [{"fileType": "TXT"}]})


def test_not_docless_with_pdf():
    """Motion to Intervene AND Comments (with a real PDF) must be kept."""
    assert not is_docless({
        "description": "Motion to Intervene and Comments of MMWEC.",
        "transmittals": [{"fileType": "PDF"}]})


def test_not_docless_without_transmittals():
    assert not is_docless({"description": "Order.", "transmittals": []})


# ── per-file include/exclude ────────────────────────────────────────────

@pytest.mark.parametrize("desc,expected", [
    ("Transmittal Letter", True),
    ("Attachment A:  Geissler Testimony", True),
    ("Attachment G:  Governors List", True),
    ("Attachment E - Marked Tariff", False),
    ("Attachment F:  Clean Tariff", False),
    ("FERC GENERATED TARIFF FILING", False),
    ("Redline of Proposed Revisions", False),
    (None, True),
])
def test_file_included(desc, expected):
    assert file_included(desc) is expected


# ── party extraction ────────────────────────────────────────────────────

def test_extract_parties_from_docinfo_keeps_authors_and_agents():
    docinfo = {"eLcAffiliation": [
        {"Correspondent_Type": "AUTHOR",
         "Affiliation_Organization": "CPV Towantic, LLC"},
        {"Correspondent_Type": "AGENT",
         "Affiliation_Organization": "Competitive Power Ventures, Inc."},
        {"Correspondent_Type": "RECIPIENT",
         "Affiliation_Organization": "Office of the Secretary, FERC"},
        {"Correspondent_Type": "AUTHOR",   # duplicate org — second author person
         "Affiliation_Organization": "CPV Towantic, LLC"},
    ]}
    parties = extract_parties(docinfo)
    assert parties == [
        {"type": "AUTHOR", "org": "CPV Towantic, LLC"},
        {"type": "AGENT", "org": "Competitive Power Ventures, Inc."},
    ]
    assert author_orgs(parties) == ["CPV Towantic, LLC"]


def test_extract_parties_falls_back_to_search_hit():
    hit = {"affiliations": [
        {"afType": "AUTHOR", "affiliation": "NESCOE"},
        {"afType": "RECIPIENT", "affiliation": "FERC"},
    ]}
    assert extract_parties(None, hit) == [{"type": "AUTHOR", "org": "NESCOE"}]


# ── date parsing ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("01/07/2026", date(2026, 1, 7)),
    ("2026-07-20T07:44:02.25", date(2026, 7, 20)),
    ("0001-01-01T00:00:00", None),   # FERC's null sentinel
    (None, None),
    ("", None),
    ("garbage", None),
])
def test_parse_date(raw, expected):
    assert _parse_date(raw) == expected


# ── TLDR split ──────────────────────────────────────────────────────────

def test_split_tldr():
    one, rest = _split_tldr("TLDR: NESCOE supports the filing.\n\n## Body\ntext")
    assert one == "NESCOE supports the filing."
    assert rest.startswith("## Body")


def test_split_tldr_bold_variant():
    one, rest = _split_tldr("**TLDR:** Order accepts the tariff.\n\nBody.")
    assert one == "Order accepts the tariff."
    assert rest == "Body."


def test_split_tldr_absent():
    one, rest = _split_tldr("## Straight into the summary\nbody")
    assert one is None
    assert rest.startswith("## Straight")
