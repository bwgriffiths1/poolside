"""Duplicate-meeting prevention and cleanup.

Reproduces the July 7–9 2026 MC incident: ISO-NE posts one calendar event
per day of a multi-day meeting (160098/160099/160100, same materials), and
the daily discover cron minted a fresh meeting row from the not-yet-past
tail each morning (rows 7–9, 8–9, 9).
"""
from datetime import date, datetime, timezone

from pipeline.dedupe import (
    find_duplicate_clusters,
    materials_match,
    pick_canonical,
)
from pipeline.scraper import group_calendar_rows

COMMITTEE = {"name": "NEPOOL Markets Committee", "short": "MC"}


def _row(event_id: str, d: date, title: str = "Markets Committee Meeting") -> dict:
    return {
        "event_id": event_id,
        "title": title,
        "norm_title": " ".join(title.split()).lower(),
        "detail_url": f"https://www.iso-ne.com/event-details?eventId={event_id}",
        "date": d,
        "location": "Cape Cod",
    }


# ---------------------------------------------------------------------------
# Scraper grouping: stable primary_event_id through the life of a meeting
# ---------------------------------------------------------------------------

class TestGroupCalendarRows:
    def test_multi_day_groups_to_one_meeting(self):
        rows = [_row("160098", date(2026, 7, 7)),
                _row("160099", date(2026, 7, 8)),
                _row("160100", date(2026, 7, 9))]
        meetings = group_calendar_rows(rows, COMMITTEE, today=date(2026, 7, 1))
        assert len(meetings) == 1
        assert meetings[0]["primary_event_id"] == "160098"
        assert meetings[0]["all_event_ids"] == ["160098", "160099", "160100"]
        assert meetings[0]["dates"] == [date(2026, 7, 7), date(2026, 7, 8), date(2026, 7, 9)]

    def test_in_progress_meeting_keeps_full_group(self):
        # The regression: scraped ON July 8, day 1 already past. The group
        # must still include July 7 so primary_event_id stays 160098.
        rows = [_row("160098", date(2026, 7, 7)),
                _row("160099", date(2026, 7, 8)),
                _row("160100", date(2026, 7, 9))]
        meetings = group_calendar_rows(rows, COMMITTEE, today=date(2026, 7, 8))
        assert len(meetings) == 1
        assert meetings[0]["primary_event_id"] == "160098"
        assert meetings[0]["dates"][0] == date(2026, 7, 7)

    def test_last_day_still_counts(self):
        rows = [_row("160098", date(2026, 7, 7)),
                _row("160099", date(2026, 7, 8)),
                _row("160100", date(2026, 7, 9))]
        meetings = group_calendar_rows(rows, COMMITTEE, today=date(2026, 7, 9))
        assert len(meetings) == 1
        assert meetings[0]["primary_event_id"] == "160098"

    def test_ended_meeting_dropped(self):
        rows = [_row("160098", date(2026, 7, 7)),
                _row("160099", date(2026, 7, 8))]
        assert group_calendar_rows(rows, COMMITTEE, today=date(2026, 7, 9)) == []

    def test_different_titles_stay_separate(self):
        rows = [_row("1", date(2026, 7, 7)),
                _row("2", date(2026, 7, 8), title="MC Reliability Subcommittee")]
        meetings = group_calendar_rows(rows, COMMITTEE, today=date(2026, 7, 1))
        assert len(meetings) == 2

    def test_far_apart_same_title_stay_separate(self):
        rows = [_row("1", date(2026, 7, 7)),
                _row("2", date(2026, 7, 21))]
        meetings = group_calendar_rows(rows, COMMITTEE, today=date(2026, 7, 1))
        assert len(meetings) == 2


# ---------------------------------------------------------------------------
# Materials matching
# ---------------------------------------------------------------------------

DOCS = {f"https://iso-ne.com/static-assets/documents/doc{i}.pdf" for i in range(32)}


class TestMaterialsMatch:
    def test_identical_sets_match(self):
        assert materials_match(DOCS, set(DOCS))

    def test_subset_matches(self):
        # Late-posted docs landed on one row only.
        assert materials_match(DOCS, set(list(DOCS)[:20]))

    def test_disjoint_do_not_match(self):
        other = {f"https://iso-ne.com/static-assets/documents/other{i}.pdf" for i in range(5)}
        assert not materials_match(DOCS, other)

    def test_empty_never_matches(self):
        assert not materials_match(set(), DOCS)
        assert not materials_match(DOCS, set())
        assert not materials_match(set(), set())

    def test_single_shared_boilerplate_below_ratio(self):
        # One shared remote-access PDF between two 10-doc meetings ≠ same meeting.
        shared = "https://iso-ne.com/static-assets/documents/remote_info.pdf"
        a = {shared, *(f"https://x/a{i}.pdf" for i in range(9))}
        b = {shared, *(f"https://x/b{i}.pdf" for i in range(9))}
        assert not materials_match(a, b)


# ---------------------------------------------------------------------------
# Clustering + canonical selection (prod shapes from 2026-07-29)
# ---------------------------------------------------------------------------

def _meeting(mid, type_id, start, end=None, status="materials_posted",
             created=None, external_id=None, type_short="MC"):
    return {
        "id": mid,
        "meeting_type_id": type_id,
        "meeting_date": start,
        "end_date": end,
        "lifecycle_status": status,
        "created_at": created or datetime(2026, 5, 29, tzinfo=timezone.utc),
        "external_id": external_id or str(160000 + mid),
        "external_ids": [external_id or str(160000 + mid)],
        "type_short": type_short,
        "venue_short": "ISO-NE",
    }


class TestClustering:
    def test_july_mc_trio_clusters(self):
        m141 = _meeting(141, 1, date(2026, 7, 7), date(2026, 7, 9), "summarized")
        m159 = _meeting(159, 1, date(2026, 7, 8), date(2026, 7, 9))
        m160 = _meeting(160, 1, date(2026, 7, 9))
        urls = {141: DOCS, 159: DOCS, 160: DOCS}
        clusters = find_duplicate_clusters([m141, m159, m160], urls)
        assert len(clusters) == 1
        assert [m["id"] for m in clusters[0]] == [141, 159, 160]

    def test_overlap_without_materials_is_not_a_cluster(self):
        # Same committee, same day, genuinely different sessions.
        a = _meeting(1, 1, date(2026, 7, 7))
        b = _meeting(2, 1, date(2026, 7, 7))
        urls = {1: {"https://x/a.pdf"}, 2: {"https://x/b.pdf"}}
        assert find_duplicate_clusters([a, b], urls) == []

    def test_different_committees_never_cluster(self):
        # Joint meetings share materials across committees on purpose.
        a = _meeting(1, 1, date(2026, 7, 16))
        b = _meeting(2, 2, date(2026, 7, 16), type_short="RC")
        urls = {1: DOCS, 2: DOCS}
        assert find_duplicate_clusters([a, b], urls) == []

    def test_disjoint_spans_never_cluster(self):
        a = _meeting(1, 1, date(2026, 6, 9), date(2026, 6, 11))
        b = _meeting(2, 1, date(2026, 7, 7), date(2026, 7, 9))
        urls = {1: DOCS, 2: DOCS}
        assert find_duplicate_clusters([a, b], urls) == []

    def test_transitive_chain_is_one_cluster(self):
        # 7–9 overlaps 8–9 overlaps 9; all shared materials → one cluster.
        a = _meeting(1, 1, date(2026, 6, 16), date(2026, 6, 18))
        b = _meeting(2, 1, date(2026, 6, 17), date(2026, 6, 18))
        c = _meeting(3, 1, date(2026, 6, 18))
        urls = {1: DOCS, 2: DOCS, 3: DOCS}
        clusters = find_duplicate_clusters([a, b, c], urls)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3


class TestPickCanonical:
    def test_summarized_beats_materials_posted(self):
        # July MC: the long row also holds the briefing.
        m141 = _meeting(141, 1, date(2026, 7, 7), date(2026, 7, 9), "summarized")
        m159 = _meeting(159, 1, date(2026, 7, 8), date(2026, 7, 9))
        m160 = _meeting(160, 1, date(2026, 7, 9))
        canon = pick_canonical([m141, m159, m160], {141: True, 159: False, 160: False})
        assert canon["id"] == 141

    def test_content_beats_span(self):
        # June NPC: the single-day DUP carries the only briefing — it must
        # survive (its span gets widened to the union by the merge).
        m135 = _meeting(135, 3, date(2026, 6, 16), date(2026, 6, 18), type_short="NPC")
        m152 = _meeting(152, 3, date(2026, 6, 17), date(2026, 6, 18), type_short="NPC")
        m153 = _meeting(153, 3, date(2026, 6, 18), None, "summarized", type_short="NPC")
        canon = pick_canonical([m135, m152, m153], {135: False, 152: False, 153: True})
        assert canon["id"] == 153

    def test_equal_content_longest_span_wins(self):
        a = _meeting(1, 1, date(2026, 7, 7), date(2026, 7, 9))
        b = _meeting(2, 1, date(2026, 7, 8), date(2026, 7, 9))
        canon = pick_canonical([a, b], {})
        assert canon["id"] == 1

    def test_all_equal_oldest_row_wins(self):
        a = _meeting(1, 1, date(2026, 7, 7), date(2026, 7, 9),
                     created=datetime(2026, 5, 29, tzinfo=timezone.utc))
        b = _meeting(2, 1, date(2026, 7, 7), date(2026, 7, 9),
                     created=datetime(2026, 7, 8, tzinfo=timezone.utc))
        canon = pick_canonical([a, b], {})
        assert canon["id"] == 1
