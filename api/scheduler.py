"""APScheduler — runs inside the FastAPI process via the app lifespan.

Two jobs:
  * Daily 06:00 ET     — scrape calendars for known venues, create stub
                         meeting rows for any new events.
  * Mon-Fri 08:00-18:00 ET, every 30 min — refresh upcoming meetings:
                         pull new docs, run auto-assignment, bump lifecycle.

Both jobs are idempotent and call the same code paths as POST /api/admin/*.
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("poolside.scheduler")
_scheduler: AsyncIOScheduler | None = None


def _discover_job() -> None:
    from .routes.admin import discover_all_venues

    try:
        res = discover_all_venues()
        n = sum(res["discovered"].values()) if "discovered" in res else 0
        log.info("scheduled discover_all_venues — discovered %d new meeting(s)", n)
    except Exception as e:
        log.exception("scheduled discover_all_venues failed: %s", e)


def _refresh_job() -> None:
    from .routes.admin import refresh_upcoming_meetings

    try:
        res = refresh_upcoming_meetings()
        log.info("scheduled refresh_upcoming_meetings — touched %d meeting(s)", res.get("count", 0))
    except Exception as e:
        log.exception("scheduled refresh_upcoming_meetings failed: %s", e)


def _drift_alarm_job() -> None:
    """If the discovery cron hasn't found anything in 48h, raise a broadcast
    notification — usually means ISO-NE changed their site and our scraper
    needs a poke. Idempotent: only writes one alarm per 24h window.
    """
    from datetime import datetime, timedelta, timezone
    from pipeline import db_new as db
    from .routes.notifications import create_notification

    try:
        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute("SELECT MAX(last_scraped_at) AS last FROM venues")
                row = cur.fetchone()
                last_scraped = row["last"] if row else None
                cur.execute(
                    """SELECT 1 FROM notifications
                        WHERE kind = 'drift_alarm'
                          AND created_at > NOW() - INTERVAL '24 hours'
                        LIMIT 1"""
                )
                recent_alarm = cur.fetchone()

        if recent_alarm:
            return  # already alarmed once in the last day; don't spam
        if last_scraped is None:
            return  # never scraped — handled separately
        threshold = datetime.now(timezone.utc) - timedelta(hours=48)
        if last_scraped >= threshold:
            return  # all good

        hours = int((datetime.now(timezone.utc) - last_scraped).total_seconds() // 3600)
        create_notification(
            kind="drift_alarm",
            user_id=None,  # broadcast
            payload={
                "last_scraped_at": last_scraped.isoformat(),
                "hours_silent": hours,
                "hint": "Discovery cron hasn't found a new meeting in 48h+. The ISO-NE calendar markup may have changed.",
            },
        )
        log.warning("drift_alarm raised — %dh since last scrape", hours)
    except Exception as e:
        log.exception("drift_alarm job failed: %s", e)


def start_scheduler() -> AsyncIOScheduler | None:
    """Start the scheduler. Returns the instance, or None when disabled.

    Disable via env var POOLSIDE_SCHEDULER=off (useful for tests / one-off uvicorn).
    """
    global _scheduler
    if os.environ.get("POOLSIDE_SCHEDULER", "").lower() in ("off", "0", "false", "no"):
        log.info("scheduler disabled by POOLSIDE_SCHEDULER env")
        return None

    if _scheduler is not None:
        return _scheduler

    s = AsyncIOScheduler(timezone="America/New_York")
    s.add_job(
        _discover_job,
        CronTrigger(hour=6, minute=0),
        id="discover_all_venues",
        replace_existing=True,
    )
    s.add_job(
        _refresh_job,
        CronTrigger(day_of_week="mon-fri", hour="8-18", minute="0,30"),
        id="refresh_upcoming_meetings",
        replace_existing=True,
    )
    s.add_job(
        _drift_alarm_job,
        CronTrigger(hour=7, minute=0),
        id="drift_alarm",
        replace_existing=True,
    )
    s.start()
    _scheduler = s
    jobs = [(j.id, str(j.next_run_time)) for j in s.get_jobs()]
    log.info("scheduler started — jobs: %s", jobs)
    return s


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        log.info("scheduler stopped")
        _scheduler = None


def get_scheduler_status() -> dict:
    """For /api/admin/scheduler-status."""
    if _scheduler is None:
        return {"running": False, "jobs": []}
    return {
        "running": True,
        "jobs": [
            {"id": j.id, "next_run_time": str(j.next_run_time) if j.next_run_time else None}
            for j in _scheduler.get_jobs()
        ],
    }
