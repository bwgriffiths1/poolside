"""Schema bootstrap + migrations at startup.

Two layers, both run by run_migrations():

  1. pipeline/schema.sql — the base schema. Idempotent (CREATE TABLE IF NOT
     EXISTS throughout, seeds use ON CONFLICT DO NOTHING), applied on every
     boot so a FRESH database stands up without the retired Streamlit
     start.sh. On an existing database this is a no-op.
  2. pipeline/migrations/*.sql — applied once each, tracked in
     schema_migrations, each in its own transaction. Files that already ran
     (including everything applied before the tracking table existed — the
     migrations are individually idempotent) simply record themselves on
     their final run.

Failures propagate: api/main.py deliberately does NOT catch them, so a bad
deploy fails its healthcheck instead of serving against a broken schema.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg2

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_FILE = _REPO_ROOT / "pipeline" / "schema.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "pipeline" / "migrations"


def run_migrations() -> list[str]:
    """Apply base schema, then any unapplied migration files (sorted by
    filename). Returns the list of migration files newly applied."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set — cannot run migrations")

    ran: list[str] = []
    conn = psycopg2.connect(url)
    try:
        # Serialize concurrent booters (overlapping deploys / a second
        # replica): session-scoped advisory lock, held for the whole
        # migration pass and auto-released when the connection closes.
        # The single-process deploy makes this nearly free insurance.
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(727270011)")

        # Base schema — its own transaction so a fresh DB has the tables
        # the migration files ALTER.
        with conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_FILE.read_text())

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations (
                           filename   TEXT PRIMARY KEY,
                           applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                       )"""
                )

        for f in sorted(_MIGRATIONS_DIR.glob("*.sql")) if _MIGRATIONS_DIR.exists() else []:
            # One transaction per file: an already-applied check, the DDL,
            # and the tracking insert commit (or roll back) together.
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM schema_migrations WHERE filename = %s",
                        (f.name,),
                    )
                    if cur.fetchone():
                        continue
                    cur.execute(f.read_text())
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)",
                        (f.name,),
                    )
                    ran.append(f.name)
    finally:
        conn.close()
    return ran
