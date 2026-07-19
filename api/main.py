"""FastAPI app — Poolside.

Run with:
    uvicorn api.main:app --reload --port 8000

Wraps pipeline/db.py and pipeline/* with a thin REST surface consumed by
the Vite + React frontend in /web.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env first, BEFORE any pipeline imports — and override empty env vars
# (e.g. some shells set ANTHROPIC_API_KEY="" which silently blocks the
# default load_dotenv() in pipeline/db.py from setting it).
from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import current_user, require_secret
from .migrate import run_migrations
from .routes import (
    admin,
    admin_dashboard,
    agenda_items,
    ask,
    attachments,
    auth,
    briefings,
    config as config_route,
    deep_dives,
    documents,
    editor_images,
    item_materials,
    images,
    ingest,
    initiatives,
    jobs,
    manual_ingest,
    me,
    meetings,
    notifications,
    prompts,
    roundups,
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
    # Refuse to boot without a real session-signing secret — deliberately NOT
    # wrapped in try/except so a misconfigured deploy fails its healthcheck.
    require_secret()

    # Schema bootstrap + migrations on startup. Deliberately NOT caught:
    # serving against a broken/missing schema is worse than failing the
    # healthcheck and keeping the previous deployment live.
    ran = run_migrations()
    if ran:
        log.info("migrations ran: %s", ", ".join(ran))
    else:
        log.info("no pending migrations")

    # Seed the admin user on fresh databases (idempotent; used to live in
    # the retired Streamlit start.sh).
    try:
        admin_email = os.environ.get("ADMIN_EMAIL")
        admin_password = os.environ.get("ADMIN_PASSWORD")
        if admin_email and admin_password:
            from pipeline.auth import create_user, get_user_by_email
            if get_user_by_email(admin_email) is None:
                create_user(email=admin_email, name="Admin", password=admin_password)
                log.info("seeded admin user %s", admin_email)
    except Exception as e:
        log.warning("admin seed failed: %s", e)

    # Reap any summarize jobs that were running when the previous process
    # died (we have no way to resume them, so mark them failed). 'cancelling'
    # is included: its thread is just as dead, and leaving it would pin the
    # meeting's "active job" (and its unique-active slot) forever.
    try:
        from pipeline import db as _db
        with _db._conn() as _conn:
            with _db._cursor(_conn) as _cur:
                _cur.execute(
                    """UPDATE summarize_jobs
                          SET status = 'failed',
                              error = COALESCE(error, 'server restarted mid-run'),
                              finished_at = NOW()
                        WHERE status IN ('queued', 'running', 'cancelling')"""
                )
                if _cur.rowcount:
                    log.info("reaped %d stale summarize_jobs row(s)", _cur.rowcount)
    except Exception as e:
        log.warning("could not reap stale summarize_jobs: %s", e)

    # Same for monthly roundups: their generation thread is just as dead
    # after a restart. Without this the row shows 'generating' (UI spinner)
    # until the 15-minute claim window lets someone retake it — reap it to
    # an honest error immediately instead.
    try:
        from pipeline import db as _db
        with _db._conn() as _conn:
            with _db._cursor(_conn) as _cur:
                _cur.execute(
                    """UPDATE monthly_roundups
                          SET status = 'error',
                              error_message = COALESCE(error_message,
                                                       'server restarted mid-run'),
                              updated_at = NOW()
                        WHERE status = 'generating'"""
                )
                if _cur.rowcount:
                    log.info("reaped %d stale monthly_roundups row(s)", _cur.rowcount)
    except Exception as e:
        log.warning("could not reap stale monthly_roundups: %s", e)

    # And initiative briefs — same status-on-row pattern, same dead thread.
    try:
        from pipeline import db as _db
        with _db._conn() as _conn:
            with _db._cursor(_conn) as _cur:
                _cur.execute(
                    """UPDATE initiative_briefs
                          SET status = 'error',
                              error_message = COALESCE(error_message,
                                                       'server restarted mid-run'),
                              updated_at = NOW()
                        WHERE status = 'generating'"""
                )
                if _cur.rowcount:
                    log.info("reaped %d stale initiative_briefs row(s)", _cur.rowcount)
    except Exception as e:
        log.warning("could not reap stale initiative_briefs: %s", e)

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

# ── Public surface (no session required) ──────────────────────────────
# auth: login/logout. share + user_tokens: mixed routers whose management
# endpoints each carry their own Depends(current_user); their /api/public/*
# endpoints (share render + invite/reset accept) must stay anonymous.
app.include_router(auth.router)
app.include_router(share.router)
app.include_router(user_tokens.router)

# ── Everything else requires a valid session cookie ────────────────────
# Router-level dependency; FastAPI's per-request dependency cache means
# endpoints that also declare current_user (for the user dict) don't pay twice.
_AUTH = [Depends(current_user)]
app.include_router(me.router, dependencies=_AUTH)
app.include_router(meetings.router, dependencies=_AUTH)
app.include_router(briefings.router, dependencies=_AUTH)
app.include_router(roundups.router, dependencies=_AUTH)
app.include_router(deep_dives.router, dependencies=_AUTH)
app.include_router(ask.router, dependencies=_AUTH)
app.include_router(documents.router, dependencies=_AUTH)
app.include_router(attachments.router, dependencies=_AUTH)
app.include_router(item_materials.router, dependencies=_AUTH)
app.include_router(agenda_items.router, dependencies=_AUTH)
app.include_router(prompts.router, dependencies=_AUTH)
app.include_router(prompts.config_router, dependencies=_AUTH)
app.include_router(summaries.router, dependencies=_AUTH)
app.include_router(editor_images.router, dependencies=_AUTH)
app.include_router(images.router, dependencies=_AUTH)
app.include_router(ingest.router, dependencies=_AUTH)
app.include_router(admin.router, dependencies=_AUTH)
app.include_router(admin_dashboard.router, dependencies=_AUTH)
app.include_router(config_route.router, dependencies=_AUTH)
app.include_router(manual_ingest.router, dependencies=_AUTH)
app.include_router(jobs.router, dependencies=_AUTH)
app.include_router(search.router, dependencies=_AUTH)
app.include_router(initiatives.router, dependencies=_AUTH)
app.include_router(notifications.router, dependencies=_AUTH)
app.include_router(watches.router, dependencies=_AUTH)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the built SPA from /web/dist when present (Railway production layout).
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    @app.get("/{path:path}")
    def spa(path: str = ""):
        # Unknown API paths get a JSON 404, not a 200 with index.html —
        # the latter masks typos and confuses monitoring.
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(_DIST / "index.html")
