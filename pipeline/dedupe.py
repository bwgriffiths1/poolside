"""
pipeline/dedupe.py — Detect and merge duplicate meeting rows.

ISO-NE posts one calendar event per DAY of a multi-day meeting, each day
with its own eventId, and every day-event answers the documents API with
the SAME material set. Before discovery learned to match on any day's
event ID, each mid-meeting scrape minted a fresh meeting row from the
not-yet-past tail of the group (July 7–9 2026 MC → rows 7–9, 8–9 and 9).

This module finds such duplicates — same committee, overlapping date
spans, matching materials — and merges each cluster into one canonical
row whose span is the union of all spans: the longest span is the true
span.

Canonical selection favors content: lifecycle rank (approved >
summarized > the rest), then a real (non-stub) briefing version, then
longest span, then earliest created_at, then lowest id. Duplicates that
carry human work (manual or approved summary versions, or an approved
lifecycle) block their cluster from auto-merge; the cluster is reported
for manual review instead.

Usage:
    python -m pipeline.dedupe             # dry-run report
    python -m pipeline.dedupe --apply     # execute merges
"""
import logging
from datetime import date

import pipeline.db as db

logger = logging.getLogger(__name__)

# Overlap must cover at least this fraction of the SMALLER document set.
# Same-meeting day-events answer with identical sets (ratio 1.0); the
# margin tolerates a late-posted doc landing on only one row. Well below
# it, shared boilerplate (a remote-access PDF attached to two genuinely
# different meetings) can't trigger a merge.
MATCH_RATIO = 0.5

_LIFECYCLE_RANK = {"approved": 3, "summarized": 2}


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without a database)
# ---------------------------------------------------------------------------

def materials_match(urls_a: set[str], urls_b: set[str]) -> bool:
    """True when two document-URL sets clearly describe the same materials:
    both non-empty and the overlap covers ≥ MATCH_RATIO of the smaller set."""
    if not urls_a or not urls_b:
        return False
    overlap = len(urls_a & urls_b)
    return overlap > 0 and overlap >= MATCH_RATIO * min(len(urls_a), len(urls_b))


def _span(m: dict) -> tuple[date, date]:
    start = m["meeting_date"]
    return start, m.get("end_date") or start


def _spans_overlap(a: dict, b: dict) -> bool:
    a1, a2 = _span(a)
    b1, b2 = _span(b)
    return a1 <= b2 and b1 <= a2


def find_duplicate_clusters(
    meetings: list[dict],
    doc_urls: dict[int, set[str]],
) -> list[list[dict]]:
    """Cluster meetings that are the same real-world meeting.

    An edge exists between two meetings when they share a meeting_type,
    their date spans overlap, and their materials match. Returns the
    connected components with ≥ 2 members, each sorted by (start, id).
    """
    by_id = {m["id"]: m for m in meetings}
    adj: dict[int, set[int]] = {m["id"]: set() for m in meetings}

    by_type: dict[int, list[dict]] = {}
    for m in meetings:
        by_type.setdefault(m["meeting_type_id"], []).append(m)

    for group in by_type.values():
        group.sort(key=lambda m: (_span(m)[0], m["id"]))
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if not _spans_overlap(a, b):
                    continue
                if materials_match(doc_urls.get(a["id"], set()),
                                   doc_urls.get(b["id"], set())):
                    adj[a["id"]].add(b["id"])
                    adj[b["id"]].add(a["id"])

    clusters: list[list[dict]] = []
    seen: set[int] = set()
    for mid in sorted(adj):
        if mid in seen or not adj[mid]:
            continue
        component: set[int] = set()
        stack = [mid]
        while stack:
            cur = stack.pop()
            if cur in component:
                continue
            component.add(cur)
            stack.extend(adj[cur] - component)
        seen |= component
        clusters.append(sorted((by_id[i] for i in component),
                               key=lambda m: (_span(m)[0], m["id"])))
    return clusters


def pick_canonical(cluster: list[dict], has_briefing: dict[int, bool]) -> dict:
    """The row that keeps living: most content value, then longest span,
    then oldest row."""
    def key(m: dict):
        start, end = _span(m)
        created = m.get("created_at")
        return (
            _LIFECYCLE_RANK.get(m.get("lifecycle_status") or "", 0),
            1 if has_briefing.get(m["id"]) else 0,
            (end - start).days,
            -(created.timestamp() if created else 0.0),
            -m["id"],
        )
    return max(cluster, key=key)


# ---------------------------------------------------------------------------
# DB-backed detection
# ---------------------------------------------------------------------------

def _load_meetings(venue_short: str | None = None) -> list[dict]:
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            sql = """
                SELECT m.*, mt.short_name AS type_short, v.short_name AS venue_short
                FROM meetings m
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v         ON v.id  = mt.venue_id
            """
            params: list = []
            if venue_short:
                sql += " WHERE v.short_name = %s"
                params.append(venue_short)
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def _doc_urls_for(meeting_ids: list[int]) -> dict[int, set[str]]:
    if not meeting_ids:
        return {}
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("""
                SELECT meeting_id, source_url FROM documents
                WHERE meeting_id = ANY(%s)
                  AND source_url IS NOT NULL
                  AND source_url NOT LIKE '%%#zip:%%'
            """, (meeting_ids,))
            out: dict[int, set[str]] = {}
            for r in cur.fetchall():
                out.setdefault(r["meeting_id"], set()).add(r["source_url"])
            return out


def _meetings_with_briefing(meeting_ids: list[int]) -> set[int]:
    """Meeting ids that have a real (non-stub, non-empty) briefing version."""
    if not meeting_ids:
        return set()
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("""
                SELECT DISTINCT entity_id FROM summary_versions
                WHERE entity_type = 'meeting'
                  AND entity_id = ANY(%s)
                  AND status IN ('draft', 'approved')
                  AND COALESCE(detailed, one_line, '') <> ''
            """, (meeting_ids,))
            return {r["entity_id"] for r in cur.fetchall()}


def _human_content_meetings(meeting_ids: list[int]) -> set[int]:
    """Meeting ids carrying human work that a merge must not destroy:
    manual or approved summary versions on the meeting, its agenda items,
    or its documents — or an approved lifecycle."""
    if not meeting_ids:
        return set()
    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute("""
                SELECT DISTINCT m.id FROM meetings m
                WHERE m.id = ANY(%(ids)s) AND (
                    m.lifecycle_status = 'approved'
                    OR EXISTS (
                        SELECT 1 FROM summary_versions sv
                        WHERE (sv.is_manual OR sv.status = 'approved')
                          AND (
                            (sv.entity_type = 'meeting' AND sv.entity_id = m.id)
                            OR (sv.entity_type = 'agenda_item' AND sv.entity_id IN
                                (SELECT id FROM agenda_items WHERE meeting_id = m.id))
                            OR (sv.entity_type = 'document' AND sv.entity_id IN
                                (SELECT id FROM documents WHERE meeting_id = m.id))
                          )
                    )
                )
            """, {"ids": meeting_ids})
            return {r["id"] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Merge execution
# ---------------------------------------------------------------------------

def _merge_cluster(canonical: dict, dups: list[dict], all_ids: list[str],
                   span: tuple[date, date]) -> dict[int, int]:
    """Merge dups into canonical inside one transaction.

    Documents the canonical lacks move over (keeping their summaries);
    user-facing references re-point; polymorphic rows that would orphan
    (summary_versions, entity_tags) are deleted with their entities.
    Returns {dup_id: docs_moved}.
    """
    docs_moved: dict[int, int] = {}
    canon_id = canonical["id"]
    start, end = span

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            for dup in dups:
                dup_id = dup["id"]

                # Materials the canonical doesn't have move over — a
                # late-posted doc may have landed only on the dup. Their
                # document-entity summaries follow for free (id unchanged).
                cur.execute("""
                    UPDATE documents SET meeting_id = %(canon)s
                    WHERE meeting_id = %(dup)s
                      AND filename NOT IN
                          (SELECT filename FROM documents WHERE meeting_id = %(canon)s)
                """, {"canon": canon_id, "dup": dup_id})
                docs_moved[dup_id] = cur.rowcount or 0

                # Re-point user-facing references; PK/unique-safe variants
                # first, plain updates for the rest.
                cur.execute("""
                    UPDATE roundup_meetings SET meeting_id = %(canon)s
                    WHERE meeting_id = %(dup)s
                      AND NOT EXISTS (SELECT 1 FROM roundup_meetings r2
                                      WHERE r2.roundup_id = roundup_meetings.roundup_id
                                        AND r2.meeting_id = %(canon)s)
                """, {"canon": canon_id, "dup": dup_id})
                cur.execute("""
                    UPDATE meeting_watches SET meeting_id = %(canon)s
                    WHERE meeting_id = %(dup)s
                      AND NOT EXISTS (SELECT 1 FROM meeting_watches w2
                                      WHERE w2.user_id = meeting_watches.user_id
                                        AND w2.meeting_id = %(canon)s)
                """, {"canon": canon_id, "dup": dup_id})
                cur.execute("UPDATE notifications SET meeting_id = %s WHERE meeting_id = %s",
                            (canon_id, dup_id))
                cur.execute("UPDATE meeting_attachments SET meeting_id = %s WHERE meeting_id = %s",
                            (canon_id, dup_id))
                cur.execute("UPDATE editor_images SET meeting_id = %s WHERE meeting_id = %s",
                            (canon_id, dup_id))

                # summary_versions / entity_tags have no FK — delete rows for
                # the entities about to cascade away, or they orphan.
                for tbl in ("summary_versions", "entity_tags"):
                    cur.execute(f"""
                        DELETE FROM {tbl}
                        WHERE (entity_type = 'meeting' AND entity_id = %(dup)s)
                           OR (entity_type = 'agenda_item' AND entity_id IN
                               (SELECT id FROM agenda_items WHERE meeting_id = %(dup)s))
                           OR (entity_type = 'document' AND entity_id IN
                               (SELECT id FROM documents WHERE meeting_id = %(dup)s))
                    """, {"dup": dup_id})

                # Everything else on the dup cascades with the row
                # (agenda_items, leftover documents + images, item_documents,
                # summarize_jobs, approvals, leftover junction rows).
                cur.execute("DELETE FROM meetings WHERE id = %s", (dup_id,))

            # Canonical takes the union span and the full event-ID set.
            cur.execute("""
                UPDATE meetings SET
                    meeting_date = %(start)s,
                    end_date     = %(end)s,
                    external_ids = ARRAY(SELECT DISTINCT e
                                         FROM unnest(external_ids || %(ids)s::text[]) AS e
                                         ORDER BY e)
                WHERE id = %(id)s
            """, {"start": start, "end": end if end > start else None,
                  "ids": all_ids, "id": canon_id})

    return docs_moved


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def dedupe_meetings(dry_run: bool = True, venue_short: str | None = None) -> dict:
    """Find duplicate-meeting clusters and (unless dry_run) merge them.

    Returns {"dry_run": bool, "clusters": [plan, ...], "merged": n_clusters_merged}.
    Each plan: canonical/duplicates labels, the union span, and — when the
    cluster can't be auto-merged — blocked=True with a reason.
    """
    meetings = _load_meetings(venue_short)
    ids = [m["id"] for m in meetings]
    doc_urls = _doc_urls_for(ids)
    clusters = find_duplicate_clusters(meetings, doc_urls)

    plans: list[dict] = []
    merged = 0

    for cluster in clusters:
        cluster_ids = [m["id"] for m in cluster]
        has_briefing = {i: i in _meetings_with_briefing(cluster_ids) for i in cluster_ids}
        canonical = pick_canonical(cluster, has_briefing)
        dups = [m for m in cluster if m["id"] != canonical["id"]]

        start = min(_span(m)[0] for m in cluster)
        end = max(_span(m)[1] for m in cluster)
        all_ids = sorted({
            str(i)
            for m in cluster
            for i in [m.get("external_id"), *(m.get("external_ids") or [])]
            if i
        })

        human = _human_content_meetings([d["id"] for d in dups])
        plan = {
            "committee": cluster[0].get("type_short"),
            "venue": cluster[0].get("venue_short"),
            "canonical_id": canonical["id"],
            "canonical_status": canonical.get("lifecycle_status"),
            "duplicate_ids": [d["id"] for d in dups],
            "span": [str(start), str(end)],
            "external_ids": all_ids,
            "blocked": bool(human),
            "blocked_reason": (
                f"duplicate row(s) {sorted(human)} carry manual/approved content"
                if human else None
            ),
            "docs_moved": None,
        }

        if not plan["blocked"] and not dry_run:
            plan["docs_moved"] = _merge_cluster(canonical, dups, all_ids, (start, end))
            merged += 1
            logger.info("dedupe: merged %s into meeting %s (span %s → %s)",
                        plan["duplicate_ids"], canonical["id"], start, end)

        plans.append(plan)

    return {"dry_run": dry_run, "clusters": plans, "merged": merged}


def _main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Merge duplicate meeting rows")
    parser.add_argument("--apply", action="store_true",
                        help="execute merges (default: dry-run report)")
    parser.add_argument("--venue", default=None, help="limit to one venue short name")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = dedupe_meetings(dry_run=not args.apply, venue_short=args.venue)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _main()
