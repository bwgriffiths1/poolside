"""discover_all_venues() venue coverage — the daily cron's contract.

PJM used to be deliberately excluded here (button-driven from /pjm while the
venue was demo-phase), so new PJM meetings never appeared on their own. Now
that PJM is a first-class venue in the main UI, the cron has to cover it.

Pin: both venues land in the per-venue count, and a PJM-side failure cannot
discard the ISO-NE results gathered before it (they share one return value).
"""
import api.services.discovery as discovery


def _patch_isone(monkeypatch, new_meetings: int = 2) -> None:
    """One active committee whose calendar yields `new_meetings` new events."""
    monkeypatch.setattr(
        discovery, "_load_config",
        lambda: {"committees": [{"short": "MC", "name": "Markets", "active": True}],
                 "lookahead_days": 60},
    )
    monkeypatch.setattr(
        discovery.pl_scraper, "scrape_calendar",
        lambda committee, lookahead_days=60: [{"n": i} for i in range(new_meetings)],
    )
    monkeypatch.setattr(discovery, "_reconcile_isone_event", lambda c, ev: True)
    monkeypatch.setattr(discovery, "_stamp_venue_scrape", lambda venue: None)


def test_both_venues_reported(monkeypatch):
    _patch_isone(monkeypatch)
    monkeypatch.setattr(
        discovery, "discover_pjm", lambda: {"discovered": {"PJM": 3}, "meetings": []},
    )

    res = discovery.discover_all_venues()

    assert res["discovered"] == {"ISO-NE": 2, "PJM": 3}


def test_pjm_failure_preserves_isone_results(monkeypatch):
    _patch_isone(monkeypatch)

    def boom() -> dict:
        raise RuntimeError("PJM markup changed")

    monkeypatch.setattr(discovery, "discover_pjm", boom)

    res = discovery.discover_all_venues()

    # ISO-NE's count survives; PJM reports zero rather than sinking the run.
    assert res["discovered"]["ISO-NE"] == 2
    assert res["discovered"]["PJM"] == 0
