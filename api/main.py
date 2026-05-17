"""FastAPI app — Poolside.

Run with:
    uvicorn api.main:app --reload --port 8000

Wraps pipeline/db_new.py and pipeline/* with a thin REST surface consumed by
the Vite + React frontend in /web.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env first, BEFORE any pipeline imports — and override empty env vars
# (e.g. some shells set ANTHROPIC_API_KEY="" which silently blocks the
# default load_dotenv() in pipeline/db_new.py from setting it).
from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .migrate import run_migrations
from .routes import (
    admin,
    admin_dashboard,
    agenda_items,
    auth,
    briefings,
    config as config_route,
    documents,
    editor_images,
    images,
    ingest,
    jobs,
    manual_ingest,
    me,
    meetings,
    notifications,
    prompts,
    search,
    share,
    summaries,
    user_tokens,
    watches,
)
from .scheduler import start_scheduler, stop_scheduler

log = logging.getLogger("poolside.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema migrations on startup — idempotent.
    try:
        ran = run_migrations()
        if ran:
            log.info("migrations ran: %s", ", ".join(ran))
        else:
            log.info("no pending migrations")
    except Exception as e:
        log.exception("migration failure: %s", e)

    # Reap any summarize jobs that were running when the previous process
    # died (we have no way to resume them, so mark them failed).
    try:
        from pipeline import db_new as _db
        with _db._conn() as _conn:
            with _db._cursor(_conn) as _cur:
                _cur.execute(
                    """UPDATE summarize_jobs
                          SET status = 'failed',
                              error = COALESCE(error, 'server restarted mid-run'),
                              finished_at = NOW()
                        WHERE status IN ('queued', 'running')"""
                )
                if _cur.rowcount:
                    log.info("reaped %d stale summarize_jobs row(s)", _cur.rowcount)
    except Exception as e:
        log.warning("could not reap stale summarize_jobs: %s", e)

    # Cron scheduler (set POOLSIDE_SCHEDULER=off to disable).
    try:
        start_scheduler()
    except Exception as e:
        log.exception("scheduler failed to start: %s", e)

    yield

    try:
        stop_scheduler()
    except Exception:
        pass


app = FastAPI(title="Poolside API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(meetings.router)
app.include_router(briefings.router)
app.include_router(documents.router)
app.include_router(agenda_items.router)
app.include_router(prompts.router)
app.include_router(prompts.config_router)
app.include_router(summaries.router)
app.include_router(editor_images.router)
app.include_router(images.router)
app.include_router(ingest.router)
app.include_router(admin.router)
app.include_router(admin_dashboard.router)
app.include_router(config_route.router)
app.include_router(manual_ingest.router)
app.include_router(jobs.router)
app.include_router(search.router)
app.include_router(notifications.router)
app.include_router(watches.router)
app.include_router(share.router)
app.include_router(user_tokens.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the built SPA from /web/dist when present (Railway production layout).
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    @app.get("/{path:path}")
    def spa(path: str = "") -> FileResponse:
        if path.startswith("api/"):
            return FileResponse(_DIST / "index.html")
        return FileResponse(_DIST / "index.html")
