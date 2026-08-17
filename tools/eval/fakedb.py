"""In-memory stand-in for pipeline.db, grown from tests/test_l2_own_docs'
FakeDB to cover both the ISO meeting runner and the FERC per-filing path.

The runner swaps this in for `summarizer.db` / `docket_ingest.db`, so eval
runs never touch Postgres: reads come from a frozen golden payload, writes
accumulate in `created` (and become visible to `get_current_summary`, which
the L2 rollup and L3 briefing depend on mid-run).
"""
from __future__ import annotations


class EvalDB:
    def __init__(
        self,
        meeting: dict | None = None,
        items: list[dict] | None = None,
        docs_by_item: dict | None = None,
        filing_files: list[dict] | None = None,
        prior_briefings: list[dict] | None = None,
    ):
        self.meeting = meeting
        self.items = list(items or [])
        # JSON round-trips dict keys to strings — coerce back to int ids.
        self.docs_by_item = {int(k): list(v) for k, v in (docs_by_item or {}).items()}
        self.filing_files = list(filing_files or [])
        self.prior_briefings = list(prior_briefings or [])
        self.created: list[dict] = []
        self._current: dict[tuple[str, int], dict] = {}

    # ── ISO reads ──────────────────────────────────────────────────────
    def get_agenda_items(self, meeting_id):
        return list(self.items)

    def get_documents_for_item(self, item_id):
        return list(self.docs_by_item.get(int(item_id), []))

    def get_current_summary(self, entity_type, entity_id):
        return self._current.get((entity_type, entity_id))

    def get_meeting(self, meeting_id):
        return self.meeting

    def get_prior_meeting_briefings(self, meeting_id, within_days=60, limit=3):
        return list(self.prior_briefings)

    def get_images_by_ids(self, image_ids):
        return []

    def set_meeting_status(self, meeting_id, status):
        pass

    # ── FERC reads ─────────────────────────────────────────────────────
    def list_filing_files(self, filing_id, with_content=False):
        # Rows carry raw_content, so docket_ingest._extract_file_text
        # short-circuits on the cache and never touches the FercClient.
        return list(self.filing_files)

    # ── writes ─────────────────────────────────────────────────────────
    def create_summary_version(self, entity_type, entity_id, one_line=None,
                               detailed=None, model_id=None, is_manual=False,
                               status="stub", created_by="system"):
        prior = self._current.get((entity_type, entity_id))
        row = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "version": (prior["version"] + 1) if prior else 1,
            "one_line": one_line,
            "detailed": detailed,
            "model_id": model_id,
            "is_manual": is_manual,
            "status": status,
            "created_by": created_by,
        }
        self.created.append(row)
        self._current[(entity_type, entity_id)] = row
        return row
