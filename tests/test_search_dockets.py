"""Docket search: metadata + full-text merge, dedupe, snippet safety.

The SQL runs against Postgres in dev/prod; here the two result sets are
faked at the cursor layer to pin the merge rules:

  * metadata hits (docket number / title substring) sort first;
  * a docket surfaced by metadata isn't duplicated by its state-of-play
    text hit, but filing hits keep their own rows;
  * snippets are HTML-escaped with only the ts_headline markers becoming
    real <b> tags (both consumers render with dangerouslySetInnerHTML).
"""
from __future__ import annotations

import api.services.search as search_svc


class FakeCursor:
    """Returns one canned result set per execute(), in order."""

    def __init__(self, result_sets):
        self._sets = list(result_sets)
        self._current = None

    def execute(self, sql, params=None):
        self._current = self._sets.pop(0)

    def fetchall(self):
        return self._current

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_results(monkeypatch, meta_rows, text_rows):
    cursor = FakeCursor([meta_rows, text_rows])
    monkeypatch.setattr(search_svc.db, "_conn", lambda: FakeConn())
    monkeypatch.setattr(search_svc.db, "_cursor", lambda conn: cursor)


def test_meta_hits_first_and_sop_deduped(monkeypatch):
    meta = [{"docket_id": 1, "docket_number": "EL25-106",
             "title": "PfP penalty structure complaint", "party_label": "EPSA"}]
    text = [
        {"entity_type": "docket", "entity_id": 1, "rank": 0.9,
         "snippet": "the @@HLS@@PfP@@HLE@@ penalty cap",
         "docket_id": 1, "docket_number": "EL25-106",
         "title": "PfP penalty structure complaint", "party_label": "EPSA",
         "accession_number": None, "document_class": None},
        {"entity_type": "docket_filing", "entity_id": 55, "rank": 0.5,
         "snippet": "comments on @@HLS@@PfP@@HLE@@ exposure",
         "docket_id": 2, "docket_number": "ER26-3213",
         "title": "Pay-for-Performance revisions", "party_label": None,
         "accession_number": "20260701-5001", "document_class": "Comments/Protest"},
    ]
    _patch_results(monkeypatch, meta, text)

    hits = search_svc.search_docket_hits("PfP")
    assert [h["entity_type"] for h in hits] == ["docket_meta", "docket_filing"]
    # The docket-1 SOP text hit was folded into its metadata row.
    assert hits[0]["docket_id"] == 1
    assert hits[1]["docket_id"] == 2
    assert hits[1]["snippet"] == "comments on <b>PfP</b> exposure"


def test_snippets_escape_html(monkeypatch):
    text = [
        {"entity_type": "docket", "entity_id": 3, "rank": 0.4,
         "snippet": "<script>alert(1)</script> @@HLS@@tariff@@HLE@@",
         "docket_id": 3, "docket_number": "ER26-1", "title": None,
         "party_label": None, "accession_number": None,
         "document_class": None},
    ]
    _patch_results(monkeypatch, [], text)
    hits = search_svc.search_docket_hits("tariff")
    assert hits[0]["snippet"] == (
        "&lt;script&gt;alert(1)&lt;/script&gt; <b>tariff</b>"
    )


def test_empty_query_short_circuits(monkeypatch):
    # No DB access at all on a blank query.
    def boom():
        raise AssertionError("should not connect")

    monkeypatch.setattr(search_svc.db, "_conn", boom)
    assert search_svc.search_docket_hits("   ") == []
