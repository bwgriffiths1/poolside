"""
pipeline/db.py — Connection pool and CRUD helpers for the redesigned schema.

DATABASE_URL format: postgresql://user:password@host:port/dbname
Set it in .env for local development.
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

# ThreadedConnectionPool: FastAPI serves sync handlers from a threadpool and
# summarize/scheduler jobs run on their own threads — SimpleConnectionPool's
# getconn/putconn are not safe under that concurrency.
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise EnvironmentError("DATABASE_URL is not set. Add it to .env.")
        # Max sized for: ~40 request threads (bursty, short checkouts) +
        # summarize/roundup job threads + the L1/L2 parallel workers
        # (summarization.parallel_workers, up to 8). getconn fails fast on
        # exhaustion rather than queuing, so headroom matters.
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 20, dsn=url)
    return _pool


@contextmanager
def _conn():
    """Yield a psycopg2 connection, returning it to the pool on exit."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def _cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------

def get_venues(active_only: bool = True) -> list[dict]:
    with _conn() as conn:
        with _cursor(conn) as cur:
            sql = "SELECT * FROM venues"
            if active_only:
                sql += " WHERE active = true"
            sql += " ORDER BY short_name"
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def get_venue(short_name: str) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM venues WHERE short_name = %s", (short_name,))
            row = cur.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# Meeting types
# ---------------------------------------------------------------------------

def get_meeting_types(venue_short_name: str | None = None,
                      active_only: bool = True) -> list[dict]:
    with _conn() as conn:
        with _cursor(conn) as cur:
            sql = """
                SELECT mt.*, v.short_name AS venue_short_name, v.name AS venue_name
                FROM meeting_types mt
                JOIN venues v ON v.id = mt.venue_id
                WHERE 1=1
            """
            params: list = []
            if active_only:
                sql += " AND mt.active = true"
            if venue_short_name:
                sql += " AND v.short_name = %s"
                params.append(venue_short_name)
            sql += " ORDER BY v.short_name, mt.short_name"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------

def upsert_meeting(
    meeting_type_id: int,
    meeting_date: str,           # ISO date string "YYYY-MM-DD"
    external_id: str | None = None,
    title: str | None = None,
    meeting_number: str | None = None,
    end_date: str | None = None,
    location: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Insert or update a meeting row.
    Conflict key: (meeting_type_id, external_id).
    Returns the full row as a dict.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO meetings
                    (meeting_type_id, external_id, title, meeting_date, end_date,
                     meeting_number, location, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (meeting_type_id, external_id)
                DO UPDATE SET
                    title          = EXCLUDED.title,
                    meeting_date   = EXCLUDED.meeting_date,
                    end_date       = EXCLUDED.end_date,
                    meeting_number = EXCLUDED.meeting_number,
                    location       = EXCLUDED.location,
                    notes          = EXCLUDED.notes
                RETURNING *
            """, (meeting_type_id, external_id, title, meeting_date, end_date,
                  meeting_number, location, notes))
            return dict(cur.fetchone())


def create_meeting_type(venue_id: int, name: str, short_name: str,
                        description: str | None = None) -> dict:
    """Create a new meeting type (committee) for a venue."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO meeting_types (venue_id, name, short_name, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (venue_id, short_name)
                DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description
                RETURNING *
            """, (venue_id, name, short_name, description))
            return dict(cur.fetchone())


def get_meeting(meeting_id: int) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT m.*,
                       mt.short_name  AS type_short,
                       mt.name        AS type_name,
                       v.short_name   AS venue_short,
                       v.name         AS venue_name
                FROM meetings m
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v         ON v.id  = mt.venue_id
                WHERE m.id = %s
            """, (meeting_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def list_meetings(
    venue_short: str | None = None,
    type_short: str | None = None,
    limit: int = 50,
) -> list[dict]:
    with _conn() as conn:
        with _cursor(conn) as cur:
            sql = """
                SELECT m.*,
                       mt.short_name AS type_short,
                       mt.name       AS type_name,
                       v.short_name  AS venue_short
                FROM meetings m
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v         ON v.id  = mt.venue_id
                WHERE 1=1
            """
            params: list = []
            if venue_short:
                sql += " AND v.short_name = %s"
                params.append(venue_short)
            if type_short:
                sql += " AND mt.short_name = %s"
                params.append(type_short)
            sql += " ORDER BY m.meeting_date DESC LIMIT %s"
            params.append(limit)
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def list_meetings_overview(
    venue_short: str | None = None,
    past_days: int = 90,
    future_days: int = 90,
) -> list[dict]:
    """
    Return meetings within [today - past_days, today + future_days] with
    derived status counts used to compute the single status pill.

    Each row includes: id, meeting_date, end_date, type_short, type_name,
    venue_short, title, meeting_number, location, external_id,
    meeting_status, doc_count, has_summary, has_manual.
    Ordered by meeting_date ASC.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            sql = """
                SELECT
                    m.id,
                    m.meeting_date,
                    m.end_date,
                    m.title,
                    m.meeting_number,
                    m.location,
                    m.external_id,
                    m.status                AS meeting_status,
                    COALESCE(m.lifecycle_status, 'discovered') AS lifecycle_status,
                    m.agenda_doc_hash,
                    m.agenda_parsed_at,
                    m.last_scraped_at,
                    mt.short_name           AS type_short,
                    mt.name                 AS type_name,
                    v.short_name            AS venue_short,
                    COUNT(DISTINCT d.id)    AS doc_count,
                    COUNT(DISTINCT CASE
                        WHEN d.ignored = false
                         AND NOT EXISTS (
                             SELECT 1 FROM item_documents idoc
                             WHERE idoc.document_id = d.id
                         )
                        THEN d.id END)      AS unassigned_doc_count,
                    COUNT(DISTINCT ai.id)   AS item_count,
                    COALESCE(MAX(CASE
                        WHEN sv.status IN ('draft','approved') AND sv.is_manual = false
                        THEN 1 ELSE 0 END), 0) AS has_summary,
                    COALESCE(MAX(CASE
                        WHEN sv.is_manual = true
                        THEN 1 ELSE 0 END), 0) AS has_manual
                FROM meetings m
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v         ON v.id  = mt.venue_id
                LEFT JOIN documents d
                       ON d.meeting_id = m.id
                LEFT JOIN agenda_items ai
                       ON ai.meeting_id = m.id
                LEFT JOIN summary_versions sv
                       ON sv.entity_type = 'meeting'
                      AND sv.entity_id   = m.id
                      AND sv.status     != 'superseded'
                      AND sv.created_by != 'autosave'
                WHERE m.meeting_date BETWEEN (CURRENT_DATE - %s::int) AND (CURRENT_DATE + %s::int)
            """
            params: list = [past_days, future_days]
            if venue_short:
                sql += " AND v.short_name = %s"
                params.append(venue_short)
            sql += """
                GROUP BY m.id, mt.short_name, mt.name, v.short_name,
                         m.lifecycle_status, m.agenda_doc_hash,
                         m.agenda_parsed_at, m.last_scraped_at
                ORDER BY m.meeting_date ASC
            """
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def set_meeting_status(meeting_id: int, status: str) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE meetings SET status = %s WHERE id = %s",
                (status, meeting_id),
            )


# ---------------------------------------------------------------------------
# Agenda items
# ---------------------------------------------------------------------------

def insert_agenda_item(
    meeting_id: int,
    title: str,
    seq: int,
    depth: int = 0,
    parent_id: int | None = None,
    item_id: str | None = None,
    prefix: str | None = None,
    auto_sub: bool = False,
    presenter: str | None = None,
    org: str | None = None,
    vote_status: str | None = None,
    wmpp_id: str | None = None,
    time_slot: str | None = None,
    notes: str | None = None,
) -> dict:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO agenda_items
                    (meeting_id, parent_id, item_id, prefix, title,
                     depth, seq, auto_sub, presenter, org, vote_status,
                     wmpp_id, time_slot, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (meeting_id, parent_id, item_id, prefix, title,
                  depth, seq, auto_sub, presenter, org, vote_status,
                  wmpp_id, time_slot, notes))
            return dict(cur.fetchone())


def get_agenda_items(meeting_id: int) -> list[dict]:
    """Return all agenda items for a meeting in original parse order (seq)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT * FROM agenda_items
                WHERE meeting_id = %s
                ORDER BY seq
            """, (meeting_id,))
            return [dict(r) for r in cur.fetchall()]


def clear_agenda_for_meeting(meeting_id: int, preserve_human_work: bool = True) -> None:
    """
    Delete agenda items (and their summaries/tags) for a meeting so it can
    be re-ingested. item_documents cascade automatically from agenda_items.

    By default, human work survives the re-ingest: agenda items that have an
    approved or manually-edited summary version are kept intact (row,
    versions, tags), and approved/manual meeting-level briefing versions are
    retained. Pass preserve_human_work=False for the old scorched-earth
    behavior.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            keep_ids: list[int] = []
            if preserve_human_work:
                cur.execute("""
                    SELECT DISTINCT entity_id FROM summary_versions
                    WHERE entity_type = 'agenda_item'
                      AND entity_id IN (
                          SELECT id FROM agenda_items WHERE meeting_id = %s
                      )
                      AND (status = 'approved' OR is_manual)
                """, (meeting_id,))
                keep_ids = [r["entity_id"] for r in cur.fetchall()]

            # Polymorphic rows that reference agenda_items by ID.
            # ('{}'::int[] when keep_ids is empty — matches nothing.)
            cur.execute("""
                DELETE FROM entity_tags
                WHERE entity_type = 'agenda_item'
                  AND entity_id IN (
                      SELECT id FROM agenda_items WHERE meeting_id = %s
                  )
                  AND NOT (entity_id = ANY(%s::int[]))
            """, (meeting_id, keep_ids))
            cur.execute("""
                DELETE FROM summary_versions
                WHERE entity_type = 'agenda_item'
                  AND entity_id IN (
                      SELECT id FROM agenda_items WHERE meeting_id = %s
                  )
                  AND NOT (entity_id = ANY(%s::int[]))
            """, (meeting_id, keep_ids))
            if preserve_human_work:
                cur.execute("""
                    DELETE FROM summary_versions
                    WHERE entity_type = 'meeting' AND entity_id = %s
                      AND NOT (status = 'approved' OR is_manual)
                """, (meeting_id,))
            else:
                cur.execute("""
                    DELETE FROM summary_versions
                    WHERE entity_type = 'meeting' AND entity_id = %s
                """, (meeting_id,))
            # Cascade removes item_documents
            cur.execute("""
                DELETE FROM agenda_items
                WHERE meeting_id = %s
                  AND NOT (id = ANY(%s::int[]))
            """, (meeting_id, keep_ids))


def ensure_agenda_hierarchy(meeting_id: int) -> int:
    """Normalize a meeting's agenda tree. Idempotent; safe to run after any
    ingest/parse pass or on read.

    Guarantees, for every dotted item_id ("3.a", "2.b", "1.A.i"):
      1. A parent row exists ("3", "2", "1.A") — created as a "(no title)"
         stub if the agenda never listed it explicitly.
      2. The child's parent_id points at that row, and depth matches the
         item_id's dot count.
      3. seq ordering puts each parent immediately before its first child.
         (Re-parses can append a late-discovered parent at the end of the
         agenda — e.g. "3" landing after 3.a–3.q — which broke display
         order and rollup grouping.)

    Returns the number of parent stubs created.
    """
    items = get_agenda_items(meeting_id)  # ordered by seq
    if not items:
        return 0

    def _first_by_iid(rows: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in rows:
            iid = r.get("item_id")
            if iid and iid not in out:
                out[iid] = r
        return out

    by_iid = _first_by_iid(items)

    # 1. Create missing parents (all ancestor levels), as stubs at the end —
    #    the renumbering below moves them into position.
    created = 0
    for r in list(items):
        iid = r.get("item_id") or ""
        while "." in iid:
            iid = iid.rsplit(".", 1)[0]
            if iid and iid not in by_iid:
                row = insert_agenda_item(
                    meeting_id=meeting_id,
                    title="(no title)",
                    seq=len(items) + created,
                    depth=iid.count("."),
                    item_id=iid,
                    prefix=f"a{iid.zfill(2)}_" if iid.isdigit() else None,
                    auto_sub=False,
                )
                by_iid[iid] = row
                created += 1

    if created:
        items = get_agenda_items(meeting_id)
        by_iid = _first_by_iid(items)

    # 2. Fix parent_id links and depth.
    with _conn() as conn:
        with _cursor(conn) as cur:
            for r in items:
                iid = r.get("item_id") or ""
                want_depth = iid.count(".")
                want_parent = (
                    by_iid[iid.rsplit(".", 1)[0]]["id"]
                    if "." in iid and iid.rsplit(".", 1)[0] in by_iid
                    else None
                )
                # Don't null out a hand-set parent when the item_id has no dots.
                if "." in iid and (r.get("parent_id") != want_parent or r.get("depth") != want_depth):
                    cur.execute(
                        "UPDATE agenda_items SET parent_id = %s, depth = %s WHERE id = %s",
                        (want_parent, want_depth, r["id"]),
                    )
                    r["parent_id"], r["depth"] = want_parent, want_depth

    # 3. Renumber: depth-first walk where every subtree is positioned at the
    #    earliest seq found within it, so a late-appended parent adopts its
    #    first child's slot. Children keep their relative order.
    children: dict[int | None, list[dict]] = {}
    for r in items:
        pid = r.get("parent_id")
        if pid is not None and not any(x["id"] == pid for x in items):
            pid = None  # parent row belongs to another meeting/gone — treat as root
        children.setdefault(pid, []).append(r)

    subtree_min: dict[int, int] = {}

    def _min_seq(r: dict) -> int:
        rid = r["id"]
        if rid not in subtree_min:
            m = r["seq"]
            for c in children.get(rid, []):
                m = min(m, _min_seq(c))
            subtree_min[rid] = m
        return subtree_min[rid]

    ordered: list[dict] = []

    def _walk(rows: list[dict]) -> None:
        for r in sorted(rows, key=lambda x: (_min_seq(x), x["seq"], x["id"])):
            ordered.append(r)
            _walk(children.get(r["id"], []))

    _walk(children.get(None, []))

    with _conn() as conn:
        with _cursor(conn) as cur:
            for new_seq, r in enumerate(ordered):
                if r["seq"] != new_seq:
                    cur.execute(
                        "UPDATE agenda_items SET seq = %s WHERE id = %s",
                        (new_seq, r["id"]),
                    )

    return created


def update_agenda_item(row_id: int, **fields) -> None:
    """Update editable metadata fields on an agenda item."""
    allowed = {"title", "item_id", "prefix", "presenter", "org", "vote_status", "wmpp_id", "time_slot", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [row_id]
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(f"UPDATE agenda_items SET {set_clause} WHERE id = %s", values)


def delete_agenda_item(item_id: int) -> None:
    """Delete a single agenda item and its polymorphic summary/tag rows."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "DELETE FROM entity_tags WHERE entity_type = 'agenda_item' AND entity_id = %s",
                (item_id,),
            )
            cur.execute(
                "DELETE FROM summary_versions WHERE entity_type = 'agenda_item' AND entity_id = %s",
                (item_id,),
            )
            # item_documents rows cascade automatically
            cur.execute("DELETE FROM agenda_items WHERE id = %s", (item_id,))


def get_max_seq(meeting_id: int) -> int:
    """Return the highest seq value for a meeting's agenda items, or 0."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM agenda_items WHERE meeting_id = %s",
                (meeting_id,),
            )
            row = cur.fetchone()
            return row["max_seq"] if row else 0


def save_manual_summary(entity_type: str, entity_id: int,
                        one_line: str | None, detailed: str | None,
                        created_by: str = "user") -> dict:
    """
    Save a manually-written summary as a new approved version.
    Any previously approved version is superseded automatically.
    """
    row = create_summary_version(
        entity_type=entity_type,
        entity_id=entity_id,
        one_line=one_line or None,
        detailed=detailed or None,
        model_id=None,
        is_manual=True,
        status="approved",
        created_by=created_by,
    )
    # Supersede all other non-stub versions for this entity
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                UPDATE summary_versions
                SET status = 'superseded'
                WHERE entity_type = %s AND entity_id = %s
                  AND id != %s AND status NOT IN ('stub', 'superseded')
            """, (entity_type, entity_id, row["id"]))
    return row


def autosave_summary(entity_type: str, entity_id: int,
                     detailed: str, one_line: str | None = None) -> None:
    """
    Write an autosave draft for an entity.
    Replaces any previous autosave row so at most one autosave exists per entity.
    Does NOT supersede AI-generated drafts or approved versions.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                DELETE FROM summary_versions
                WHERE entity_type = %s AND entity_id = %s AND created_by = 'autosave'
            """, (entity_type, entity_id))
    create_summary_version(
        entity_type=entity_type,
        entity_id=entity_id,
        detailed=detailed,
        one_line=one_line,
        model_id=None,
        is_manual=True,
        status="draft",
        created_by="autosave",
    )


def get_autosave(entity_type: str, entity_id: int) -> dict | None:
    """Return the autosave draft for an entity, if one exists."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT * FROM summary_versions
                WHERE entity_type = %s AND entity_id = %s AND created_by = 'autosave'
                LIMIT 1
            """, (entity_type, entity_id))
            row = cur.fetchone()
            return dict(row) if row else None


def clear_autosave(entity_type: str, entity_id: int) -> None:
    """Remove autosave rows for an entity (called after formal save)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                DELETE FROM summary_versions
                WHERE entity_type = %s AND entity_id = %s AND created_by = 'autosave'
            """, (entity_type, entity_id))


def get_agenda_item(item_id: int) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM agenda_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def upsert_document(
    meeting_id: int,
    filename: str,
    file_type: str | None = None,
    source_url: str | None = None,
    file_hash: str | None = None,
    ceii_skipped: bool = False,
) -> dict:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO documents
                    (meeting_id, filename, file_type, source_url, file_hash, ceii_skipped)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (meeting_id, filename)
                DO UPDATE SET
                    file_type    = EXCLUDED.file_type,
                    source_url   = EXCLUDED.source_url,
                    file_hash    = EXCLUDED.file_hash,
                    ceii_skipped = EXCLUDED.ceii_skipped
                RETURNING *
            """, (meeting_id, filename, file_type, source_url, file_hash, ceii_skipped))
            return dict(cur.fetchone())


def set_document_raw_content(document_id: int, raw_content: str) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE documents SET raw_content = %s WHERE id = %s",
                (raw_content, document_id),
            )


def get_document(document_id: int) -> dict | None:
    """Fetch a single document row (no bytes), or None."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM documents WHERE id = %s", (document_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def _unique_filename(cur, meeting_id: int, filename: str) -> str:
    """Return `filename`, or `name (2).ext` etc, so a manual add never
    collides with (and clobbers) an existing document for the meeting."""
    cur.execute(
        "SELECT filename FROM documents WHERE meeting_id = %s", (meeting_id,)
    )
    existing = {r["filename"] for r in cur.fetchall()}
    if filename not in existing:
        return filename
    stem, dot, ext = filename.rpartition(".")
    base, suffix = (stem, f".{ext}") if dot else (filename, "")
    n = 2
    while f"{base} ({n}){suffix}" in existing:
        n += 1
    return f"{base} ({n}){suffix}"


def add_manual_document(
    meeting_id: int,
    filename: str,
    file_type: str | None = None,
    source_url: str | None = None,
    raw_content: str | None = None,
) -> dict:
    """Insert a user-added document (flagged manual). Unlike upsert_document
    this never overwrites an existing row — the filename is de-duplicated
    first — so manually-attached memos are independent of scraped materials.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            safe_name = _unique_filename(cur, meeting_id, filename)
            cur.execute("""
                INSERT INTO documents
                    (meeting_id, filename, file_type, source_url,
                     raw_content, manual)
                VALUES (%s, %s, %s, %s, %s, true)
                RETURNING *
            """, (meeting_id, safe_name, file_type, source_url, raw_content))
            return dict(cur.fetchone())


def store_document_file(document_id: int, mime_type: str, data: bytes) -> None:
    """Persist the raw bytes of an uploaded manual document (side table)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO document_files (document_id, mime_type, size_bytes, data)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (document_id)
                DO UPDATE SET mime_type  = EXCLUDED.mime_type,
                              size_bytes = EXCLUDED.size_bytes,
                              data       = EXCLUDED.data
            """, (document_id, mime_type, len(data), psycopg2.Binary(data)))


def get_document_file(document_id: int) -> dict | None:
    """Fetch stored bytes for an uploaded document, or None."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT mime_type, size_bytes, data FROM document_files WHERE document_id = %s",
                (document_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def delete_document(document_id: int) -> bool:
    """Delete a document and everything hanging off it (item_documents,
    document_images, document_files all cascade). Returns True if removed."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Document images
# ---------------------------------------------------------------------------

def insert_document_image(
    document_id: int,
    filename: str,
    page_or_slide: int,
    img_index: int = 0,
    width: int | None = None,
    height: int | None = None,
    file_path: str | None = None,
    image_b64: str | None = None,
    description: str | None = None,
) -> dict:
    """Insert or update an extracted image record."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO document_images
                    (document_id, filename, page_or_slide, img_index,
                     width, height, file_path, image_b64, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (document_id, page_or_slide, img_index)
                DO UPDATE SET
                    filename  = EXCLUDED.filename,
                    width     = EXCLUDED.width,
                    height    = EXCLUDED.height,
                    file_path = EXCLUDED.file_path,
                    image_b64 = EXCLUDED.image_b64,
                    description = COALESCE(EXCLUDED.description, document_images.description)
                RETURNING *
            """, (document_id, filename, page_or_slide, img_index,
                  width, height, file_path, image_b64, description))
            return dict(cur.fetchone())


def get_images_for_document(document_id: int, min_size: int = 0) -> list[dict]:
    """Return images for a document, optionally filtered by minimum dimension."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT * FROM document_images
                WHERE document_id = %s
                  AND (width  >= %s OR width  IS NULL)
                  AND (height >= %s OR height IS NULL)
                ORDER BY page_or_slide, img_index
            """, (document_id, min_size, min_size))
            return [dict(r) for r in cur.fetchall()]


def get_images_for_item(item_id: int, min_size: int = 0) -> list[dict]:
    """Return images for all documents assigned to an agenda item."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT di.*, d.filename AS doc_filename
                FROM document_images di
                JOIN item_documents id ON id.document_id = di.document_id
                JOIN documents d       ON d.id = di.document_id
                WHERE id.item_id = %s
                  AND (di.width  >= %s OR di.width  IS NULL)
                  AND (di.height >= %s OR di.height IS NULL)
                ORDER BY di.document_id, di.page_or_slide, di.img_index
            """, (item_id, min_size, min_size))
            return [dict(r) for r in cur.fetchall()]


def set_image_description(image_id: int, description: str) -> None:
    """Update the Claude-generated description for an image."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE document_images SET description = %s WHERE id = %s",
                (description, image_id),
            )


def get_images_by_ids(image_ids: list[int]) -> list[dict]:
    """Fetch image records by a list of IDs (batch query)."""
    if not image_ids:
        return []
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM document_images WHERE id = ANY(%s) ORDER BY id",
                (image_ids,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_editor_images_by_ids(image_ids: list[int]) -> list[dict]:
    """Fetch editor-pasted image records by a list of IDs (batch query).

    A different table from document_images: these are clipboard pastes stored
    as raw `data` bytea, not base64-encoded figures extracted from a PDF.
    """
    if not image_ids:
        return []
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT id, mime_type, data FROM editor_images "
                "WHERE id = ANY(%s) ORDER BY id",
                (image_ids,),
            )
            return [dict(r) for r in cur.fetchall()]


def count_images_for_document(document_id: int) -> int:
    """Return the number of extracted images for a document."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM document_images WHERE document_id = %s",
                (document_id,),
            )
            return cur.fetchone()["cnt"]


# Every place stored markdown can reference an extracted image. Stored text
# carries `<!-- image_id:N -->` markers; hand-edited text may carry the
# resolved `/api/images/N` form instead — both count as references.
_IMAGE_REF_SOURCES_SQL = """
    SELECT (regexp_matches(detailed, 'image_id:(\\d+)', 'g'))[1]::int AS img_id
      FROM summary_versions WHERE detailed LIKE '%%image_id:%%'
    UNION
    SELECT (regexp_matches(detailed, '/api/images/(\\d+)', 'g'))[1]::int
      FROM summary_versions WHERE detailed LIKE '%%/api/images/%%'
    UNION
    SELECT (regexp_matches(report_md, 'image_id:(\\d+)', 'g'))[1]::int
      FROM deep_dive_reports WHERE report_md LIKE '%%image_id:%%'
    UNION
    SELECT (regexp_matches(report_md, '/api/images/(\\d+)', 'g'))[1]::int
      FROM deep_dive_reports WHERE report_md LIKE '%%/api/images/%%'
    UNION
    SELECT (regexp_matches(brief_md, 'image_id:(\\d+)', 'g'))[1]::int
      FROM initiative_briefs WHERE brief_md LIKE '%%image_id:%%'
    UNION
    SELECT (regexp_matches(brief_md, '/api/images/(\\d+)', 'g'))[1]::int
      FROM initiative_briefs WHERE brief_md LIKE '%%/api/images/%%'
"""


def image_stats() -> dict:
    """Storage snapshot for the admin dashboard: how many extracted images
    exist, how many any stored markdown actually references, and the bytes
    the unreferenced remainder occupies."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            # Empty params tuple so psycopg2 runs placeholder processing and
            # the %% escapes in the shared SQL collapse to literal %.
            cur.execute(f"""
                WITH refs AS ({_IMAGE_REF_SOURCES_SQL})
                SELECT
                    (SELECT COUNT(*) FROM document_images)          AS stored,
                    (SELECT COALESCE(SUM(length(image_b64)), 0)
                       FROM document_images)                        AS stored_bytes,
                    (SELECT COUNT(*) FROM document_images
                      WHERE id IN (SELECT img_id FROM refs))        AS referenced,
                    (SELECT COALESCE(SUM(length(image_b64)), 0)
                       FROM document_images
                      WHERE id NOT IN (SELECT img_id FROM refs))    AS unreferenced_bytes
            """, ())
            row = dict(cur.fetchone())
    return {k: int(v or 0) for k, v in row.items()}


def prune_unreferenced_document_images(older_than_days: int = 30) -> dict:
    """Delete extracted images that no stored markdown references.

    Guards: only images older than the window (re-summarize churn keeps its
    cache) and only for documents with a source_url — the extractor
    re-downloads on demand, and ISO-NE keeps old materials up indefinitely,
    so these rows are a regenerable cache, not primary data. Every version
    of every summary counts as a reference (restoring an old version from
    history must not 404 its figures). Manual/uploaded documents (no
    source_url) are never pruned. editor_images is a different table and is
    untouched.

    Returns {deleted, freed_bytes}. Space is reclaimed by the caller's
    vacuum (see vacuum_document_images) — DELETE alone leaves the file size
    unchanged.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(f"""
                WITH refs AS ({_IMAGE_REF_SOURCES_SQL})
                DELETE FROM document_images di
                 USING documents d
                 WHERE d.id = di.document_id
                   AND COALESCE(d.source_url, '') <> ''
                   AND di.created_at < NOW() - make_interval(days => %s)
                   AND di.id NOT IN (SELECT img_id FROM refs)
             RETURNING length(di.image_b64) AS b
            """, (older_than_days,))
            rows = cur.fetchall()
    return {
        "deleted": len(rows),
        "freed_bytes": int(sum((r["b"] or 0) for r in rows)),
    }


def vacuum_document_images() -> None:
    """VACUUM FULL the images table so a big prune actually shrinks the
    database on disk. Needs autocommit (VACUUM can't run in a transaction);
    takes an exclusive lock for a few seconds, so callers should run it
    off-peak and only after a non-trivial prune."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        old_autocommit = conn.autocommit
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("VACUUM (FULL, ANALYZE) document_images")
        finally:
            conn.autocommit = old_autocommit
    finally:
        pool.putconn(conn)


def get_documents_for_meeting(meeting_id: int) -> list[dict]:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM documents WHERE meeting_id = %s ORDER BY filename",
                (meeting_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_existing_filenames(meeting_id: int) -> set[str]:
    """Return the set of filenames already stored for this meeting."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT filename FROM documents WHERE meeting_id = %s",
                (meeting_id,),
            )
            return {r["filename"] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Item–document assignments
# ---------------------------------------------------------------------------

def assign_document_to_item(item_id: int, document_id: int) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO item_documents (item_id, document_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (item_id, document_id))


def reassign_document(document_id: int, new_item_id: int, meeting_id: int) -> None:
    """
    Move a document to a different agenda item within the same meeting.
    Removes all existing item_documents rows for this document (within this
    meeting) and inserts a fresh one pointing to new_item_id.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                DELETE FROM item_documents
                WHERE document_id = %s
                  AND item_id IN (
                      SELECT id FROM agenda_items WHERE meeting_id = %s
                  )
            """, (document_id, meeting_id))
            cur.execute("""
                INSERT INTO item_documents (item_id, document_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (new_item_id, document_id))


def unassign_document(document_id: int, meeting_id: int) -> None:
    """Remove all item assignments for a document within a meeting."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                DELETE FROM item_documents
                WHERE document_id = %s
                  AND item_id IN (
                      SELECT id FROM agenda_items WHERE meeting_id = %s
                  )
            """, (document_id, meeting_id))


def get_unassigned_documents(meeting_id: int) -> list[dict]:
    """Return unassigned, non-ignored documents for this meeting."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT d.* FROM documents d
                WHERE d.meeting_id = %s
                  AND d.ignored = false
                  AND NOT EXISTS (
                      SELECT 1 FROM item_documents id2
                      WHERE id2.document_id = d.id
                  )
                ORDER BY d.filename
            """, (meeting_id,))
            return [dict(r) for r in cur.fetchall()]


def get_ignored_documents(meeting_id: int) -> list[dict]:
    """Return documents explicitly marked as ignored for this meeting."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT * FROM documents
                WHERE meeting_id = %s AND ignored = true
                ORDER BY filename
            """, (meeting_id,))
            return [dict(r) for r in cur.fetchall()]


def set_document_ignored(document_id: int, ignored: bool) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE documents SET ignored = %s WHERE id = %s",
                (ignored, document_id),
            )


def get_documents_for_item(item_id: int) -> list[dict]:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT d.* FROM documents d
                JOIN item_documents id ON id.document_id = d.id
                WHERE id.item_id = %s
                ORDER BY d.filename
            """, (item_id,))
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def upsert_tag(name: str, tag_type: str = "custom",
               description: str | None = None) -> dict:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO tags (name, tag_type, description)
                VALUES (%s, %s, %s)
                ON CONFLICT (name)
                DO UPDATE SET tag_type = EXCLUDED.tag_type,
                              description = COALESCE(EXCLUDED.description, tags.description)
                RETURNING *
            """, (name, tag_type, description))
            return dict(cur.fetchone())


def tag_entity(tag_id: int, entity_type: str, entity_id: int) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO entity_tags (tag_id, entity_type, entity_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (tag_id, entity_type, entity_id))


def untag_entity(tag_id: int, entity_type: str, entity_id: int) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                DELETE FROM entity_tags
                WHERE tag_id = %s AND entity_type = %s AND entity_id = %s
            """, (tag_id, entity_type, entity_id))


def get_tags_for_entity(entity_type: str, entity_id: int) -> list[dict]:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT t.* FROM tags t
                JOIN entity_tags et ON et.tag_id = t.id
                WHERE et.entity_type = %s AND et.entity_id = %s
                ORDER BY t.tag_type, t.name
            """, (entity_type, entity_id))
            return [dict(r) for r in cur.fetchall()]


def get_entities_for_tag(tag_name: str,
                          entity_type: str | None = None) -> list[dict]:
    """Return all (entity_type, entity_id) rows for a given tag name."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            sql = """
                SELECT et.entity_type, et.entity_id, et.created_at
                FROM entity_tags et
                JOIN tags t ON t.id = et.tag_id
                WHERE t.name = %s
            """
            params: list = [tag_name]
            if entity_type:
                sql += " AND et.entity_type = %s"
                params.append(entity_type)
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Summary versions
# ---------------------------------------------------------------------------

def _next_version(cur, entity_type: str, entity_id: int) -> int:
    cur.execute("""
        SELECT COALESCE(MAX(version), 0) + 1 AS next_version
        FROM summary_versions
        WHERE entity_type = %s AND entity_id = %s
    """, (entity_type, entity_id))
    return cur.fetchone()["next_version"]


def create_summary_version(
    entity_type: str,
    entity_id: int,
    one_line: str | None = None,
    detailed: str | None = None,
    model_id: str | None = None,
    is_manual: bool = False,
    status: str = "stub",
    created_by: str = "system",
) -> dict:
    """
    Insert a new summary version for an entity.
    Version number is auto-incremented per (entity_type, entity_id).
    Returns the new row as a dict.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            version = _next_version(cur, entity_type, entity_id)
            cur.execute("""
                INSERT INTO summary_versions
                    (entity_type, entity_id, version, one_line, detailed,
                     model_id, is_manual, status, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (entity_type, entity_id, version, one_line, detailed,
                  model_id, is_manual, status, created_by))
            return dict(cur.fetchone())


def get_current_summary(entity_type: str, entity_id: int) -> dict | None:
    """
    Return the best available summary version for an entity:
    prefer 'approved', else 'draft', else highest version number.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT * FROM summary_versions
                WHERE entity_type = %s AND entity_id = %s
                ORDER BY
                    CASE status
                        WHEN 'approved' THEN 0
                        WHEN 'draft'    THEN 1
                        WHEN 'stub'     THEN 2
                        ELSE 3
                    END,
                    version DESC
                LIMIT 1
            """, (entity_type, entity_id))
            row = cur.fetchone()
            return dict(row) if row else None


def get_prior_meeting_briefings(
    meeting_id: int, within_days: int = 60, limit: int = 3
) -> list[dict]:
    """Return current briefings for prior meetings of the SAME committee whose
    date falls within `within_days` before this meeting, most recent first.

    Each dict: {id, meeting_date, title, detailed}. Only meetings that actually
    have a briefing with body text are returned. Used to populate the
    [PRIOR CONTEXT] section of the Level 3 briefing prompt.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                SELECT id, meeting_date, title
                  FROM meetings
                 WHERE meeting_type_id = (SELECT meeting_type_id FROM meetings WHERE id = %s)
                   AND meeting_date <  (SELECT meeting_date FROM meetings WHERE id = %s)
                   AND meeting_date >= (SELECT meeting_date FROM meetings WHERE id = %s)
                                       - make_interval(days => %s)
                   AND id <> %s
                 ORDER BY meeting_date DESC
                 LIMIT %s
                """,
                (meeting_id, meeting_id, meeting_id, within_days, meeting_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]

    out: list[dict] = []
    for r in rows:
        summ = get_current_summary("meeting", r["id"])
        if summ and (summ.get("detailed") or "").strip():
            out.append({
                "id": r["id"],
                "meeting_date": str(r["meeting_date"]),
                "title": r.get("title") or "",
                "detailed": summ["detailed"],
            })
    return out


def list_summary_versions(entity_type: str, entity_id: int) -> list[dict]:
    """Return all summary versions for an entity, newest first."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT * FROM summary_versions
                WHERE entity_type = %s AND entity_id = %s
                ORDER BY version DESC
            """, (entity_type, entity_id))
            return [dict(r) for r in cur.fetchall()]


def approve_summary_version(summary_id: int) -> None:
    """Mark one version as approved and supersede all others for that entity."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            # Get entity info for this version
            cur.execute(
                "SELECT entity_type, entity_id FROM summary_versions WHERE id = %s",
                (summary_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            entity_type, entity_id = row["entity_type"], row["entity_id"]

            # Supersede all other non-stub versions
            cur.execute("""
                UPDATE summary_versions
                SET status = 'superseded'
                WHERE entity_type = %s AND entity_id = %s
                  AND id != %s AND status != 'stub'
            """, (entity_type, entity_id, summary_id))

            cur.execute(
                "UPDATE summary_versions SET status = 'approved' WHERE id = %s",
                (summary_id,),
            )


# ---------------------------------------------------------------------------
# Tags — additional helpers
# ---------------------------------------------------------------------------

def list_all_tags(tag_type: str | None = None) -> list[dict]:
    """Return all tags, optionally filtered by tag_type."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            sql = "SELECT * FROM tags"
            params: list = []
            if tag_type:
                sql += " WHERE tag_type = %s"
                params.append(tag_type)
            sql += " ORDER BY tag_type, name"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def search_documents_by_tag(tag_name: str) -> list[dict]:
    """Return documents tagged with the given tag, with meeting context."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT d.*, m.meeting_date, m.end_date, m.title AS meeting_title,
                       mt.short_name AS type_short, mt.name AS type_name,
                       v.short_name AS venue_short
                FROM documents d
                JOIN entity_tags et ON et.entity_type = 'document' AND et.entity_id = d.id
                JOIN tags t         ON t.id = et.tag_id
                JOIN meetings m     ON m.id = d.meeting_id
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v       ON v.id = mt.venue_id
                WHERE t.name = %s
                ORDER BY m.meeting_date DESC, d.filename
            """, (tag_name,))
            return [dict(r) for r in cur.fetchall()]


def search_documents_by_text(query: str, limit: int = 50) -> list[dict]:
    """Search documents by filename (case-insensitive LIKE), with meeting context."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT d.*, m.meeting_date, m.end_date, m.title AS meeting_title,
                       mt.short_name AS type_short, mt.name AS type_name,
                       v.short_name AS venue_short
                FROM documents d
                JOIN meetings m     ON m.id = d.meeting_id
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v       ON v.id = mt.venue_id
                WHERE d.filename ILIKE %s
                ORDER BY m.meeting_date DESC, d.filename
                LIMIT %s
            """, (f"%{query}%", limit))
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Deep dive reports
# ---------------------------------------------------------------------------

def create_deep_dive_report(
    title: str,
    config: dict | None = None,
    prompt_slug: str | None = None,
    model_id: str | None = None,
    created_by: str = "system",
) -> dict:
    """Create a new deep dive report and return it."""
    import json as _json
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO deep_dive_reports
                    (title, config, prompt_slug, model_id, created_by)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
            """, (title, _json.dumps(config or {}), prompt_slug, model_id,
                  created_by))
            return dict(cur.fetchone())


def update_deep_dive_report(report_id: int, **fields) -> None:
    """Update specified fields on a deep dive report."""
    allowed = {"title", "status", "report_md", "error_message", "config",
               "prompt_slug", "model_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    # Serialize config to JSON if present
    import json as _json
    if "config" in updates and isinstance(updates["config"], dict):
        updates["config"] = _json.dumps(updates["config"])
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [report_id]
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                f"UPDATE deep_dive_reports SET {set_clause}, updated_at = NOW() WHERE id = %s",
                values,
            )


def get_deep_dive_report(report_id: int) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM deep_dive_reports WHERE id = %s",
                        (report_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def list_deep_dive_reports(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT * FROM deep_dive_reports
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]


def add_deep_dive_document(report_id: int, document_id: int, seq: int = 0) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO deep_dive_documents (report_id, document_id, seq)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (report_id, document_id, seq))


def get_deep_dive_documents(report_id: int) -> list[dict]:
    """Return source documents for a deep dive, with meeting context."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT d.*, dd.seq,
                       m.meeting_date, m.end_date, m.title AS meeting_title,
                       mt.short_name AS type_short, mt.name AS type_name,
                       v.short_name AS venue_short
                FROM deep_dive_documents dd
                JOIN documents d      ON d.id = dd.document_id
                JOIN meetings m       ON m.id = d.meeting_id
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v         ON v.id = mt.venue_id
                WHERE dd.report_id = %s
                ORDER BY dd.seq, d.filename
            """, (report_id,))
            return [dict(r) for r in cur.fetchall()]


def delete_deep_dive_report(report_id: int) -> None:
    """Delete a deep dive report and its document links (cascades)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("DELETE FROM deep_dive_reports WHERE id = %s",
                        (report_id,))


def claim_deep_dive_report(report_id: int, stale_minutes: int = 15) -> dict | None:
    """Atomically flip a report row to 'generating' and return it, or None
    when another request already holds a live claim. Same admission guard as
    claim_monthly_roundup — one report is a single LLM call, so a
    'generating' row older than the stale window is dead and taken over."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                UPDATE deep_dive_reports
                   SET status = 'generating',
                       error_message = NULL,
                       updated_at = NOW()
                 WHERE id = %s
                   AND NOT (status = 'generating'
                            AND updated_at > NOW() - make_interval(mins => %s))
             RETURNING *
            """, (report_id, stale_minutes))
            row = cur.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# Monthly roundups — cross-committee "state of play" reports
# ---------------------------------------------------------------------------

# A meeting "has a briefing" when a non-superseded meeting-level summary with
# body text exists. Editor autosaves land as status='stub', so stubs don't
# count — this predicate must match what get_month_briefings collects.
_HAS_BRIEFING_SQL = """
    EXISTS (SELECT 1 FROM summary_versions sv
             WHERE sv.entity_type = 'meeting'
               AND sv.entity_id   = m.id
               AND sv.status IN ('approved', 'draft')
               AND COALESCE(sv.detailed, '') <> '')
"""


def create_monthly_roundup(
    venue_id: int,
    month,  # date or ISO "YYYY-MM-01" string
    model_id: str | None = None,
    created_by: str = "system",
) -> dict:
    """Get-or-create the roundup row for (venue, month). On conflict the
    existing row is returned untouched (bar updated_at) — the caller resets
    status/fields explicitly when it actually kicks off a run."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO monthly_roundups (venue_id, month, model_id, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (venue_id, month)
                DO UPDATE SET updated_at = NOW()
                RETURNING *
            """, (venue_id, month, model_id, created_by))
            return dict(cur.fetchone())


def update_monthly_roundup(roundup_id: int, **fields) -> None:
    """Update specified fields on a roundup (whitelisted), bumping updated_at."""
    allowed = {"status", "model_id", "report_md", "error_message",
               "progress_text", "input_tokens", "output_tokens", "cost_usd"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [roundup_id]
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                f"UPDATE monthly_roundups SET {set_clause}, updated_at = NOW() WHERE id = %s",
                values,
            )


def claim_monthly_roundup(roundup_id: int, progress_text: str = "",
                          stale_minutes: int = 15) -> dict | None:
    """Atomically flip a roundup row to 'generating' and return it, or None
    when another request already holds a live claim (status='generating'
    with updated_at inside the stale window).

    This is the admission guard for generation: SELECT-then-UPDATE in the
    route would let two concurrent POSTs both spawn threads and pay for two
    LLM calls on the same row. The stale window keeps the takeover behavior
    for runs orphaned by a restart — one roundup is a single LLM call, so a
    'generating' row older than that is dead (single-process deploy model)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                UPDATE monthly_roundups
                   SET status = 'generating',
                       progress_text = %s,
                       error_message = NULL,
                       updated_at = NOW()
                 WHERE id = %s
                   AND NOT (status = 'generating'
                            AND updated_at > NOW() - make_interval(mins => %s))
             RETURNING *
            """, (progress_text, roundup_id, stale_minutes))
            row = cur.fetchone()
            return dict(row) if row else None


def get_monthly_roundup(roundup_id: int) -> dict | None:
    """Fetch one roundup row with venue context joined in."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT r.*, v.short_name AS venue_short, v.name AS venue_name
                  FROM monthly_roundups r
                  JOIN venues v ON v.id = r.venue_id
                 WHERE r.id = %s
            """, (roundup_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_roundup_by_month(venue_id: int, month) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT r.*, v.short_name AS venue_short, v.name AS venue_name
                  FROM monthly_roundups r
                  JOIN venues v ON v.id = r.venue_id
                 WHERE r.venue_id = %s AND r.month = %s
            """, (venue_id, month))
            row = cur.fetchone()
            return dict(row) if row else None


def list_monthly_roundups(venue_short: str) -> list[dict]:
    """All roundup rows for a venue, newest month first (report_md included —
    rows are few and the list endpoint strips it)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT r.*, v.short_name AS venue_short, v.name AS venue_name
                  FROM monthly_roundups r
                  JOIN venues v ON v.id = r.venue_id
                 WHERE v.short_name = %s
                 ORDER BY r.month DESC
            """, (venue_short,))
            return [dict(r) for r in cur.fetchall()]


def get_latest_prior_roundup(venue_id: int, month, within_months: int = 3) -> dict | None:
    """Most recent COMPLETE roundup before `month` (within a few months), for
    the [PRIOR CONTEXT] section. Tolerates gaps — e.g. June's roundup can lean
    on April's if May was never generated."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT r.*, v.short_name AS venue_short, v.name AS venue_name
                  FROM monthly_roundups r
                  JOIN venues v ON v.id = r.venue_id
                 WHERE r.venue_id = %s
                   AND r.month < %s
                   AND r.month >= %s::date - make_interval(months => %s)
                   AND r.status = 'complete'
                   AND COALESCE(r.report_md, '') <> ''
                 ORDER BY r.month DESC
                 LIMIT 1
            """, (venue_id, month, month, within_months))
            row = cur.fetchone()
            return dict(row) if row else None


def delete_monthly_roundup(roundup_id: int) -> bool:
    """Delete a roundup and its provenance links (cascade). True if removed."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("DELETE FROM monthly_roundups WHERE id = %s",
                        (roundup_id,))
            return cur.rowcount > 0


def set_roundup_meetings(roundup_id: int, meeting_ids: list[int]) -> None:
    """Replace the provenance set for a roundup (called on each run)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("DELETE FROM roundup_meetings WHERE roundup_id = %s",
                        (roundup_id,))
            for mid in meeting_ids:
                cur.execute("""
                    INSERT INTO roundup_meetings (roundup_id, meeting_id)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                """, (roundup_id, mid))


def get_roundup_meetings(roundup_id: int) -> list[dict]:
    """Source meetings for a roundup, with committee context."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT m.id, m.meeting_date, m.end_date, m.title, m.external_id,
                       mt.short_name AS type_short, mt.name AS type_name,
                       v.short_name AS venue_short
                  FROM roundup_meetings rm
                  JOIN meetings m       ON m.id = rm.meeting_id
                  JOIN meeting_types mt ON mt.id = m.meeting_type_id
                  JOIN venues v         ON v.id = mt.venue_id
                 WHERE rm.roundup_id = %s
                 ORDER BY m.meeting_date, mt.short_name
            """, (roundup_id,))
            return [dict(r) for r in cur.fetchall()]


def list_roundup_months(venue_short: str) -> list[dict]:
    """Months that have at least one meeting briefing, newest first:
    [{month: date(1st), briefing_count, committees: [short, ...]}]."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(f"""
                SELECT date_trunc('month', m.meeting_date)::date AS month,
                       COUNT(*) AS briefing_count,
                       ARRAY_AGG(DISTINCT mt.short_name) AS committees
                  FROM meetings m
                  JOIN meeting_types mt ON mt.id = m.meeting_type_id
                  JOIN venues v         ON v.id = mt.venue_id
                 WHERE v.short_name = %s
                   AND {_HAS_BRIEFING_SQL}
                 GROUP BY 1
                 ORDER BY 1 DESC
            """, (venue_short,))
            return [dict(r) for r in cur.fetchall()]


def get_month_briefings(venue_short: str, month) -> list[dict]:
    """Every meeting of the venue in the given calendar month that has a
    current briefing, chronological, each with its briefing markdown attached:
    [{id, meeting_date, end_date, title, type_short, type_name, detailed}].
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT m.id, m.meeting_date, m.end_date, m.title, m.external_id,
                       mt.short_name AS type_short, mt.name AS type_name,
                       v.short_name AS venue_short
                  FROM meetings m
                  JOIN meeting_types mt ON mt.id = m.meeting_type_id
                  JOIN venues v         ON v.id = mt.venue_id
                 WHERE v.short_name = %s
                   AND m.meeting_date >= %s::date
                   AND m.meeting_date <  %s::date + INTERVAL '1 month'
                 ORDER BY m.meeting_date, mt.short_name
            """, (venue_short, month, month))
            rows = [dict(r) for r in cur.fetchall()]

    out: list[dict] = []
    for r in rows:
        summ = get_current_summary("meeting", r["id"])
        if (summ and summ.get("status") in ("approved", "draft")
                and (summ.get("detailed") or "").strip()):
            r["detailed"] = summ["detailed"]
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Initiative briefs  — cached "story so far" per initiative tag
# ---------------------------------------------------------------------------

def get_initiative_tag(code: str) -> dict | None:
    """The initiative-typed tag row for a code like 'CAR-SA', or None."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM tags WHERE name = %s AND tag_type = 'initiative'",
                (code,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_tag(tag_id: int) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM tags WHERE id = %s", (tag_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_initiative_items(tag_id: int) -> list[dict]:
    """Every agenda item tagged with this initiative, newest meeting first,
    each with its current (approved-preferred) summary joined in.

    Shared by the initiatives API drill-in and the brief generator so both
    always see the same item set.
    """
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                SELECT
                    ai.id           AS item_db_id,
                    ai.item_id,
                    ai.title        AS item_title,
                    ai.presenter,
                    ai.org           AS organization,
                    ai.vote_status,
                    m.id            AS meeting_id,
                    m.title         AS meeting_title,
                    m.meeting_date,
                    mt.short_name   AS type_short,
                    mt.name         AS type_name,
                    v.short_name    AS venue,
                    sv.detailed     AS summary_detailed,
                    sv.one_line     AS summary_one_line,
                    sv.status       AS summary_status,
                    sv.version      AS summary_version
                FROM entity_tags et
                JOIN agenda_items ai   ON ai.id = et.entity_id
                JOIN meetings m        ON m.id  = ai.meeting_id
                JOIN meeting_types mt  ON mt.id = m.meeting_type_id
                JOIN venues v          ON v.id  = mt.venue_id
                LEFT JOIN LATERAL (
                    SELECT detailed, one_line, status, version
                      FROM summary_versions
                     WHERE entity_type = 'agenda_item'
                       AND entity_id   = ai.id
                       AND status != 'superseded'
                  ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                           version DESC
                     LIMIT 1
                ) sv ON true
                WHERE et.tag_id = %s
                  AND et.entity_type = 'agenda_item'
                ORDER BY m.meeting_date DESC, ai.seq
                """,
                (tag_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_initiative_brief(tag_id: int) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM initiative_briefs WHERE tag_id = %s", (tag_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None


def ensure_initiative_brief(tag_id: int, created_by: str = "system") -> dict:
    """Get-or-create the brief row for a tag (status 'draft' on creation)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """INSERT INTO initiative_briefs (tag_id, created_by)
                   VALUES (%s, %s)
                   ON CONFLICT (tag_id) DO NOTHING""",
                (tag_id, created_by),
            )
    return get_initiative_brief(tag_id)  # type: ignore[return-value]


def update_initiative_brief(tag_id: int, **fields) -> None:
    """Update whitelisted fields on a brief, bumping updated_at.
    Flipping status to 'complete' also stamps generated_at."""
    allowed = {"status", "brief_md", "error_message", "model_id",
               "input_tokens", "output_tokens", "cost_usd",
               "source_item_count", "source_latest_meeting_date"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    if updates.get("status") == "complete":
        set_clause += ", generated_at = NOW()"
    values = list(updates.values()) + [tag_id]
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                f"UPDATE initiative_briefs SET {set_clause}, updated_at = NOW() "
                "WHERE tag_id = %s",
                values,
            )


def claim_initiative_brief(tag_id: int, stale_minutes: int = 15) -> dict | None:
    """Atomically flip a brief row to 'generating' and return it, or None when
    another request already holds a live claim. Same admission guard as
    claim_monthly_roundup: one brief is a single LLM call, so a 'generating'
    row older than the stale window is dead and gets taken over."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                UPDATE initiative_briefs
                   SET status = 'generating',
                       error_message = NULL,
                       updated_at = NOW()
                 WHERE tag_id = %s
                   AND NOT (status = 'generating'
                            AND updated_at > NOW() - make_interval(mins => %s))
             RETURNING *
            """, (tag_id, stale_minutes))
            row = cur.fetchone()
            return dict(row) if row else None


def get_briefing_neighbors(meeting_id: int) -> dict:
    """{prev_id, next_id}: the chronologically adjacent meetings that also
    have a briefing (same predicate as the roundup collector), for the
    reader's prev/next navigation. Ties on meeting_date break by id."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                f"""
                WITH briefed AS (
                    SELECT m.id, m.meeting_date
                      FROM meetings m
                     WHERE {_HAS_BRIEFING_SQL}
                ), me AS (
                    SELECT id, meeting_date FROM briefed WHERE id = %s
                )
                SELECT
                  (SELECT b.id FROM briefed b, me
                    WHERE (b.meeting_date, b.id) < (me.meeting_date, me.id)
                 ORDER BY b.meeting_date DESC, b.id DESC LIMIT 1) AS prev_id,
                  (SELECT b.id FROM briefed b, me
                    WHERE (b.meeting_date, b.id) > (me.meeting_date, me.id)
                 ORDER BY b.meeting_date ASC, b.id ASC LIMIT 1) AS next_id
                """,
                (meeting_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {"prev_id": None, "next_id": None}


# ---------------------------------------------------------------------------
# Email preferences + digest queries
# ---------------------------------------------------------------------------

def get_user_email_prefs(user_id: int) -> dict:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT email_prefs FROM app_users WHERE id = %s",
                        (user_id,))
            row = cur.fetchone()
            return dict(row["email_prefs"]) if row and row.get("email_prefs") else {}


def set_user_email_prefs(user_id: int, prefs: dict) -> dict:
    """Merge the given keys into the user's email_prefs; returns the result.
    Callers whitelist keys — this helper stores what it's given."""
    import json as _json
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """UPDATE app_users
                      SET email_prefs = email_prefs || %s::jsonb
                    WHERE id = %s
                RETURNING email_prefs""",
                (_json.dumps(prefs), user_id),
            )
            row = cur.fetchone()
            return dict(row["email_prefs"]) if row else {}


def list_users_with_email_pref(key: str) -> list[dict]:
    """Active users who opted into the given email pref key."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """SELECT id, email, name FROM app_users
                    WHERE is_active
                      AND COALESCE((email_prefs->>%s)::boolean, false)""",
                (key,),
            )
            return [dict(r) for r in cur.fetchall()]


def list_watchers_with_email_pref(meeting_id: int, key: str) -> list[dict]:
    """Watchers of a meeting who also opted into the given email pref."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """SELECT u.id, u.email, u.name
                     FROM meeting_watches w
                     JOIN app_users u ON u.id = w.user_id
                    WHERE w.meeting_id = %s
                      AND u.is_active
                      AND COALESCE((u.email_prefs->>%s)::boolean, false)""",
                (meeting_id, key),
            )
            return [dict(r) for r in cur.fetchall()]


def list_recent_approved_briefings(days: int = 7) -> list[dict]:
    """Meetings whose meeting-level briefing was approved in the window,
    newest approval first — the digest's 'new briefings' section."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """SELECT DISTINCT ON (m.id)
                          m.id, m.title, m.meeting_date, m.end_date,
                          mt.short_name AS type_short, mt.name AS type_name,
                          v.short_name  AS venue_short,
                          sv.approved_at
                     FROM summary_versions sv
                     JOIN meetings m       ON m.id = sv.entity_id
                     JOIN meeting_types mt ON mt.id = m.meeting_type_id
                     JOIN venues v         ON v.id = mt.venue_id
                    WHERE sv.entity_type = 'meeting'
                      AND sv.status = 'approved'
                      AND sv.approved_at >= NOW() - make_interval(days => %s)
                 ORDER BY m.id, sv.approved_at DESC""",
                (days,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    rows.sort(key=lambda r: r.get("approved_at") or 0, reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Meeting attachments  — user-uploaded files (Files portal)
# ---------------------------------------------------------------------------

def _attachment_meta(row: dict) -> dict:
    """Metadata-only projection (never carries the `data` blob)."""
    return {
        "id": row["id"],
        "meeting_id": row["meeting_id"],
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
        "note": row.get("note"),
        "uploaded_by": row.get("uploaded_by"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def create_meeting_attachment(
    meeting_id: int,
    filename: str,
    mime_type: str,
    data: bytes,
    note: str | None = None,
    uploaded_by: str | None = None,
) -> dict:
    """Store an uploaded file for a meeting. Returns metadata (no blob)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO meeting_attachments
                    (meeting_id, filename, mime_type, size_bytes, note, data, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, meeting_id, filename, mime_type, size_bytes,
                          note, uploaded_by, created_at
            """, (meeting_id, filename, mime_type, len(data), note,
                  psycopg2.Binary(data), uploaded_by))
            return _attachment_meta(dict(cur.fetchone()))


def get_meeting_attachments(meeting_id: int) -> list[dict]:
    """List attachment metadata for a meeting (newest first, no blobs)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT id, meeting_id, filename, mime_type, size_bytes,
                       note, uploaded_by, created_at
                FROM meeting_attachments
                WHERE meeting_id = %s
                ORDER BY created_at DESC, id DESC
            """, (meeting_id,))
            return [_attachment_meta(dict(r)) for r in cur.fetchall()]


def get_meeting_attachment(attachment_id: int) -> dict | None:
    """Fetch one attachment INCLUDING its `data` blob, or None."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT id, meeting_id, filename, mime_type, size_bytes,
                       note, uploaded_by, created_at, data
                FROM meeting_attachments
                WHERE id = %s
            """, (attachment_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def delete_meeting_attachment(attachment_id: int) -> bool:
    """Delete an attachment. Returns True if a row was removed."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("DELETE FROM meeting_attachments WHERE id = %s",
                        (attachment_id,))
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# FERC dockets — eLibrary docket tracking (migration 014)
# ---------------------------------------------------------------------------

def create_docket(docket_number: str, title: str | None = None,
                  notes: str | None = None,
                  created_by: str | None = None) -> dict:
    """Get-or-create a tracked docket by its normalized number. Adding an
    already-tracked docket is idempotent: the existing row comes back
    untouched and the route just navigates to it."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                INSERT INTO dockets (docket_number, title, notes, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (docket_number)
                DO UPDATE SET docket_number = EXCLUDED.docket_number
                RETURNING *
            """, (docket_number, title, notes, created_by))
            return dict(cur.fetchone())


def get_docket(docket_id: int) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM dockets WHERE id = %s", (docket_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_docket_by_number(docket_number: str) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM dockets WHERE docket_number = %s",
                        (docket_number,))
            row = cur.fetchone()
            return dict(row) if row else None


def list_dockets() -> list[dict]:
    """All tracked dockets, newest first, each with filing rollups and the
    current state-of-play status joined in (list page in one query)."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT d.*,
                       COALESCE(f.filing_count, 0)      AS filing_count,
                       COALESCE(f.intervenor_count, 0)  AS intervenor_count,
                       f.latest_filed_date,
                       sv.status  AS brief_status,
                       sv.created_at AS brief_generated_at
                  FROM dockets d
                  LEFT JOIN (
                      SELECT docket_id,
                             COUNT(*) AS filing_count,
                             COUNT(*) FILTER (WHERE document_class = 'Intervention')
                                 AS intervenor_count,
                             MAX(COALESCE(filed_date, issued_date)) AS latest_filed_date
                        FROM docket_filings
                       GROUP BY docket_id
                  ) f ON f.docket_id = d.id
                  LEFT JOIN LATERAL (
                      SELECT status, created_at
                        FROM summary_versions
                       WHERE entity_type = 'docket'
                         AND entity_id   = d.id
                         AND status != 'superseded'
                    ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                             version DESC
                       LIMIT 1
                  ) sv ON true
                 ORDER BY d.created_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]


def update_docket(docket_id: int, **fields) -> None:
    """Update specified fields on a docket (whitelisted)."""
    allowed = {"title", "notes", "auto_refresh", "last_crawled_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [docket_id]
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                f"UPDATE dockets SET {set_clause} WHERE id = %s", values,
            )


def touch_docket_crawled(docket_id: int) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE dockets SET last_crawled_at = NOW() WHERE id = %s",
                (docket_id,))


def delete_docket(docket_id: int) -> bool:
    """Delete a docket (filings/files/jobs cascade). Cleans up the docket's
    summary_versions rows too — those don't FK back to dockets."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                DELETE FROM summary_versions
                 WHERE (entity_type = 'docket' AND entity_id = %s)
                    OR (entity_type = 'docket_filing' AND entity_id IN
                        (SELECT id FROM docket_filings WHERE docket_id = %s))
            """, (docket_id, docket_id))
            cur.execute("DELETE FROM dockets WHERE id = %s", (docket_id,))
            return cur.rowcount > 0


def upsert_docket_filing(docket_id: int, accession_number: str,
                         **fields) -> dict:
    """Insert or refresh one filing row keyed by (docket, accession).
    On conflict the metadata columns are updated in place — re-running an
    enrichment never duplicates a filing."""
    allowed = {"category", "document_class", "document_type", "description",
               "sub_docket", "filed_date", "issued_date", "posted_date",
               "comments_due_date", "response_due_date", "ferc_cite",
               "fed_reg_num", "filing_parties", "treatment", "is_docless",
               "raw_hit", "raw_docinfo"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    import json as _json
    for jsonb_col in ("filing_parties", "raw_hit", "raw_docinfo"):
        if jsonb_col in cols and cols[jsonb_col] is not None:
            cols[jsonb_col] = _json.dumps(cols[jsonb_col])
    col_names = ["docket_id", "accession_number"] + list(cols)
    placeholders = ", ".join(["%s"] * len(col_names))
    update_clause = ", ".join(
        f"{k} = EXCLUDED.{k}" for k in cols) or "accession_number = EXCLUDED.accession_number"
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(f"""
                INSERT INTO docket_filings ({', '.join(col_names)})
                VALUES ({placeholders})
                ON CONFLICT (docket_id, accession_number)
                DO UPDATE SET {update_clause}
                RETURNING *
            """, [docket_id, accession_number] + list(cols.values()))
            return dict(cur.fetchone())


def get_docket_filing(filing_id: int) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT * FROM docket_filings WHERE id = %s",
                        (filing_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_docket_accessions(docket_id: int) -> set[str]:
    """Accession numbers already stored for a docket — the crawl diff."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT accession_number FROM docket_filings WHERE docket_id = %s",
                (docket_id,))
            return {r["accession_number"] for r in cur.fetchall()}


def list_docket_filings(docket_id: int) -> list[dict]:
    """Every filing on a docket, newest first, each with its current
    (approved-preferred) summary joined in — shared by the docket API and
    the state-of-play generator so both see the same set."""
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("""
                SELECT df.*,
                       sv.detailed AS summary_detailed,
                       sv.one_line AS summary_one_line,
                       sv.status   AS summary_status,
                       sv.version  AS summary_version
                  FROM docket_filings df
                  LEFT JOIN LATERAL (
                      SELECT detailed, one_line, status, version
                        FROM summary_versions
                       WHERE entity_type = 'docket_filing'
                         AND entity_id   = df.id
                         AND status != 'superseded'
                    ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                             version DESC
                       LIMIT 1
                  ) sv ON true
                 WHERE df.docket_id = %s
                 ORDER BY COALESCE(df.filed_date, df.issued_date) DESC, df.id DESC
            """, (docket_id,))
            return [dict(r) for r in cur.fetchall()]


def upsert_docket_filing_file(filing_id: int, file_id: str, **fields) -> dict:
    """Insert or refresh one file row keyed by (filing, eLibrary file GUID).
    raw_content deliberately excluded — the extraction cache is written by
    set_filing_file_content and must survive metadata refreshes."""
    allowed = {"file_desc", "orig_file_name", "file_type", "file_size",
               "page_count", "file_list_order", "included"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    col_names = ["filing_id", "file_id"] + list(cols)
    placeholders = ", ".join(["%s"] * len(col_names))
    update_clause = ", ".join(
        f"{k} = EXCLUDED.{k}" for k in cols) or "file_id = EXCLUDED.file_id"
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(f"""
                INSERT INTO docket_filing_files ({', '.join(col_names)})
                VALUES ({placeholders})
                ON CONFLICT (filing_id, file_id)
                DO UPDATE SET {update_clause}
                RETURNING *
            """, [filing_id, file_id] + list(cols.values()))
            return dict(cur.fetchone())


def list_docket_filing_files(docket_id: int,
                             with_content: bool = False) -> list[dict]:
    """All file rows across a docket's filings in one query (no N+1),
    ordered for display. raw_content is heavy — opt in via with_content."""
    content_col = "dff.raw_content," if with_content else "NULL AS raw_content,"
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(f"""
                SELECT dff.id, dff.filing_id, dff.file_id, dff.file_desc,
                       dff.orig_file_name, dff.file_type, dff.file_size,
                       dff.page_count, dff.file_list_order, dff.included,
                       {content_col}
                       (dff.raw_content IS NOT NULL) AS has_content
                  FROM docket_filing_files dff
                  JOIN docket_filings df ON df.id = dff.filing_id
                 WHERE df.docket_id = %s
                 ORDER BY dff.filing_id, dff.file_list_order NULLS LAST, dff.id
            """, (docket_id,))
            return [dict(r) for r in cur.fetchall()]


def list_filing_files(filing_id: int, with_content: bool = False) -> list[dict]:
    """File rows for one filing, in list order."""
    content_col = "raw_content," if with_content else "NULL AS raw_content,"
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(f"""
                SELECT id, filing_id, file_id, file_desc, orig_file_name,
                       file_type, file_size, page_count, file_list_order,
                       included, {content_col}
                       (raw_content IS NOT NULL) AS has_content
                  FROM docket_filing_files
                 WHERE filing_id = %s
                 ORDER BY file_list_order NULLS LAST, id
            """, (filing_id,))
            return [dict(r) for r in cur.fetchall()]


def set_filing_file_content(file_row_id: int, raw_content: str) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE docket_filing_files SET raw_content = %s WHERE id = %s",
                (raw_content, file_row_id))
