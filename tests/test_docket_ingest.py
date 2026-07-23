"""pipeline/docket_ingest.py — classification rules and pure helpers.

DB-touching flows (sync_docket) are exercised in the live E2E; these tests
pin the pure classification logic that decides what gets LLM spend.
"""
from datetime import date

import pytest

from pipeline.docket_ingest import (
    DEFAULT_TREATMENT_MAP,
    _docket_pool,
    _parse_date,
    _split_tldr,
    _worker_count,
    author_orgs,
    classify_treatment,
    compute_roles,
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


# ── anchor-document roles ───────────────────────────────────────────────

def _f(id, cls, filed=None, issued=None):
    return {"id": id, "document_class": cls,
            "filed_date": filed, "issued_date": issued}


def test_roles_initial_is_earliest_application():
    filings = [
        _f(1, "Application/Petition/Request", filed="2026-02-01"),  # amendment
        _f(2, "Application/Petition/Request", filed="2025-12-30"),  # the opener
        _f(3, "Comments/Protest", filed="2026-01-20"),
        _f(4, "Order/Opinion", issued="2026-03-30"),
        _f(5, "ALJ Issuance", issued="2026-04-02"),
        _f(6, "Intervention", filed="2026-01-07"),
    ]
    roles = compute_roles(filings)
    assert roles[2] == "initial"
    assert roles[1] is None          # later application is NOT the anchor
    assert roles[4] == "order"
    assert roles[5] == "order"       # ALJ issuances are decisions too
    assert roles[3] is None and roles[6] is None


def test_roles_without_any_application():
    roles = compute_roles([_f(1, "Comments/Protest", filed="2026-01-01"),
                           _f(2, "Order/Opinion", issued="2026-02-01")])
    assert roles[1] is None
    assert roles[2] == "order"


def test_roles_date_tie_breaks_by_id():
    roles = compute_roles([
        _f(9, "Application/Petition/Request", filed="2026-01-01"),
        _f(4, "Application/Petition/Request", filed="2026-01-01"),
    ])
    assert roles[4] == "initial"
    assert roles[9] is None


def test_roles_empty():
    assert compute_roles([]) == {}


# ── sync fan-out pool ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, 4), ("garbage", 4), (0, 1), (3, 3), (99, 8),
])
def test_worker_count_clamps(raw, expected):
    assert _worker_count({"parallel_workers": raw}) == expected


def test_pool_runs_all_items_and_captures_errors():
    def work(n):
        if n == 3:
            raise RuntimeError("boom")
        return n * 10

    lines: list[str] = []
    results = _docket_pool([1, 2, 3, 4], work, 4, str, "Stage", lines.append)
    assert len(results) == 4
    by_label = {lab: (res, exc) for lab, res, exc in results}
    assert by_label["1"] == (10, None)
    assert by_label["3"][0] is None
    assert isinstance(by_label["3"][1], RuntimeError)
    # one line per completion (+ the pool banner)
    assert sum("Stage:" in ln for ln in lines) >= 4


def test_pool_cancel_from_progress_drops_pending():
    """The progress callback is the cancellation channel (job runner
    raises after writing the row) — a raise must abort the batch."""
    class Cancel(Exception):
        pass

    calls: list[int] = []

    def progress(msg):
        if "1/" in msg or "2/" in msg:
            raise Cancel()

    with pytest.raises(Cancel):
        _docket_pool([1, 2, 3, 4, 5, 6, 7, 8], calls.append, 2, str,
                     "Stage", progress)
    # pending items were dropped — not everything ran
    assert len(calls) < 8


def test_pool_serial_path_matches():
    results = _docket_pool([5, 6], lambda n: n + 1, 1, str, "S",
                           lambda m: None)
    assert [(lab, res) for lab, res, _ in results] == [("5", 6), ("6", 7)]
