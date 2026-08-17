"""Golden-case registry for the summarization eval harness.

Each case pins PRODUCTION row ids as of 2026-08-17; `fetch_golden.py` pulls
the underlying extracted text into `tools/eval/golden/<case_id>.json` and
freezes it (content-hashed) so baselines stay replayable even if prod
re-extracts or deletes rows. Selection rationale: coverage of the shapes the
pipeline actually sees — multi-doc vote packages, redline-heavy tariff work,
thin admin items, a second committee, one full meeting (the June 2026 MC,
matching the existing one-shot A/B artifacts), and the four FERC treatment
tiers (initial / order / full comment / brief motion).

Note: prod has ZERO .pptx leaf documents — ISO decks circulate as PDF
exports — so there is no separate "deck" case; the PDF-slide reality is
what these cases exercise.
"""

CASES: dict[str, dict] = {
    # ── ISO agenda items (single-item worlds; L1 only) ──────────────────
    "iso_item_admin_remarks": {
        "kind": "iso_item",
        "item_id": 865,
        "description": "PAC Chair's Opening Remarks — thin admin item, 1 doc, ~2k chars",
    },
    "iso_item_car_reforms": {
        "kind": "iso_item",
        "item_id": 169,
        "description": "MC 2025-11-12 Capacity Auction Reforms — 3 docx design memos, ~397k chars",
    },
    "iso_item_tariff_redline": {
        "kind": "iso_item",
        "item_id": 994,
        "description": "MC 2026-08-11 Tariff Review — 4 docx redlines, ~590k chars",
    },
    "iso_item_pfp_multidoc": {
        "kind": "iso_item",
        "item_id": 529,
        "description": "MC 2026-05-12 Pay-for-Performance Revisions (Balancing) — 11 docs, ~547k chars",
    },
    "iso_item_tc_asset": {
        "kind": "iso_item",
        "item_id": 692,
        "description": "TC 2026-06-24 Asset Condition Reviewer — 3 docs, ~378k chars (non-MC committee)",
    },
    # ── ISO full meeting (L1+L2+L3 pipeline; the expensive case) ────────
    "iso_meeting_june_mc": {
        "kind": "iso_meeting",
        "meeting_id": 134,
        "description": "MC 2026-06-09 full meeting — 18 items, ~1.03M chars; matches the June MC blind A/B set",
    },
    # ── FERC filings (per-filing call; one per treatment tier) ──────────
    "ferc_initial_er26_3047": {
        "kind": "ferc_filing",
        "filing_id": 27,
        "description": "ER26-3047 initial application — 3 files, ~155k chars (opus tier)",
    },
    "ferc_order_er26_925": {
        "kind": "ferc_filing",
        "filing_id": 37,
        "description": "ER26-925 order — 1 file, ~37k chars (opus tier)",
    },
    "ferc_comment_el25_49": {
        "kind": "ferc_filing",
        "filing_id": 192,
        "description": "EL25-49 comments/protest — ~227k chars (full treatment, sonnet tier)",
    },
    "ferc_motion_er24_99": {
        "kind": "ferc_filing",
        "filing_id": 592,
        "description": "ER24-99 pleading/motion — ~273k chars (brief treatment tier)",
    },
}
