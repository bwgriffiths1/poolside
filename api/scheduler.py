"""APScheduler — runs inside the FastAPI process via the app lifespan.

Jobs (all ET):
  * Daily 06:00        — scrape calendars for known venues, create stub
                         meeting rows for any new events.
  * Daily 07:00        — drift alarm when discovery has gone silent 48h+.
  * Sun 05:00          — prune unreferenced extracted images (regenerable
                         cache; keeps the DB ~85% smaller) + VACUUM FULL.
  * Mon 07:30          — weekly week-ahead email digest (opt-in per user).
  * Mon-Fri 08:00-18:00, every 30 min — refresh upcoming meetings:
                         pull new docs, run auto-assignment, bump lifecycle.
  * Mon-Fri 07:15      — FERC eLibrary check on tracked dockets: store new
                         filings (no LLM), notify so a user can click Sync.

All jobs are idempotent and call the same code paths as POST /api/admin/*.
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("poolside.scheduler")
_scheduler: AsyncIOScheduler | None = None


def _notify_job_failed(job: str, exc: Exception) -> None:
    """Broadcast a job_failed notification so cron failures land in the
    in-app inbox instead of only the Railway log viewer. Deduped: at most
    one notification per job per 6h so a persistently-failing 30-min cron
    doesn't flood the bell."""
    try:
        from pipeline import db
        from .services.notify import create_notification

        with db._conn() as conn:
            with db._cursor(conn) as cur:
                cur.execute(
                    """SELECT 1 FROM notifications
                        WHERE kind = 'job_failed'
                          AND payload->>'job' = %s
                          AND created_at > NOW() - INTERVAL '6 hours'
                        LIMIT 1""",
                    (job,),
                )
                if cur.fetchone():
                    return
        create_notification(
            kind="job_failed",
            user_id=None,  # broadcast
            payload={"job": job, "error": str(exc)[:500]},
        )
    except Exception:
        log.exception("could not write job_failed notification")


def _discover_job() -> None:
    from .services.discovery import discover_all_venues

    try:
        res = discover_all_venues()
        n = sum(res["discovered"].values()) if "discovered" in res else 0
        log.info("scheduled discover_all_venues — discovered %d new meeting(s)", n)
    except Exception as e:
        log.exception("scheduled discover_all_venues failed: %s", e)
        _notify_job_failed("discover_all_venues", e)


def _refresh_job() -> None:
    from .services.discovery import refresh_upcoming_meetings

    try:
        res = refresh_upcoming_meetings()
        log.info("scheduled refresh_upcoming_meetings — touched %d meeting(s)", res.get("count", 0))
    except Exception as e:
        log.exception("scheduled refresh_upcoming_meetings failed: %s", e)
        _notify_job_failed("refresh_upcoming_meetings", e)


def _drift_alarm_job() -> None:
    """If the discovery cron hasn't found anything in 48h, raise a broadcast
    notification — usually means ISO-NE changed their site and our scraper
    needs a poke. Idempotent: only writes one alarm per 24h window.
    """
    from datetime import datetime, timedelta, timezone
    from pipeline import db
    from .services.notify import create_notification

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


def _weekly_digest_job() -> None:
    """Monday morning week-ahead digest for opted-in users. Skips silently
    when mail is unconfigured, nobody opted in, or there's nothing to say."""
    from pipeline import db

    from .services import mailer

    try:
        if not mailer.mail_enabled():
            log.info("weekly digest skipped — mail not configured")
            return
        users = db.list_users_with_email_pref("weekly_digest")
        if not users:
            log.info("weekly digest skipped — no opted-in users")
            return

        today_window = db.list_meetings_overview(past_days=0, future_days=7)
        upcoming = sorted(today_window, key=lambda m: str(m.get("meeting_date")))
        recent = db.list_recent_approved_briefings(days=7)
        if not upcoming and not recent:
            log.info("weekly digest skipped — nothing to report")
            return

        subject, html_body = mailer.weekly_digest_email(upcoming, recent)
        sent = sum(
            1 for u in users if mailer.send_email(u["email"], subject, html_body)
        )
        log.info("weekly digest sent to %d/%d user(s) — %d upcoming, %d new briefing(s)",
                 sent, len(users), len(upcoming), len(recent))
    except Exception as e:
        log.exception("weekly_digest job failed: %s", e)
        _notify_job_failed("weekly_digest", e)


def _prune_images_job() -> None:
    """Weekly cache eviction: extracted images that no stored markdown
    references are a regenerable cache (the extractor re-downloads from
    source_url on demand, and ISO-NE keeps old materials up), and they were
    ~85% of the database. Deletes, then VACUUM FULLs the table so the disk
    actually shrinks; stamps the result in app_config for the admin page."""
    from datetime import datetime, timezone

    from pipeline import appconfig, db, storage

    try:
        before = db.image_stats()
        result = db.prune_unreferenced_document_images(older_than_days=30)
        # Bucket objects for the pruned rows (pop: the key list is plumbing,
        # not something to stamp into app_config). Best-effort by design.
        objects_deleted = storage.delete_images(result.pop("storage_keys", []))
        if result["deleted"]:
            db.vacuum_document_images()
        stamp = {
            "at": datetime.now(timezone.utc).isoformat(),
            "deleted": result["deleted"],
            "freed_bytes": result["freed_bytes"],
            "objects_deleted": objects_deleted,
            "stored_after": before["stored"] - result["deleted"],
        }
        appconfig.set_config_key("image_prune_last", stamp, updated_by="scheduler")
        log.info("prune_images: deleted %d image(s), freed %.1f MB",
                 result["deleted"], result["freed_bytes"] / 1_048_576)
    except Exception as e:
        log.exception("prune_images job failed: %s", e)
        _notify_job_failed("prune_images", e)


def _docket_check_job() -> None:
    """Daily FERC eLibrary check for tracked dockets (auto_refresh only).

    Crawl + enrich is metadata-only — NO LLM spend. New filings raise a
    broadcast notification; summarization stays a one-click user action
    (the meetings auto_resummarize=false stance). Dockets with an active
    job are skipped rather than raced."""
    from pipeline import db
    from pipeline.docket_ingest import check_for_new_filings

    from .services.docket_jobs import active_job_id
    from .services.notify import create_notification

    try:
        dockets = [d for d in db.list_dockets() if d.get("auto_refresh")]
    except Exception as e:
        log.exception("docket_check: could not list dockets: %s", e)
        _notify_job_failed("docket_check", e)
        return

    for d in dockets:
        try:
            if active_job_id(d["id"]) is not None:
                log.info("docket_check: %s has an active job — skipping",
                         d["docket_number"])
                continue
            new_count = check_for_new_filings(d["id"])
            if new_count > 0:
                create_notification(
                    kind="docket_filings_new",
                    user_id=None,  # broadcast
                    payload={
                        "docket_id": d["id"],
                        "docket_number": d["docket_number"],
                        "count": new_count,
                    },
                )
                log.info("docket_check: %s has %d new filing(s)",
                         d["docket_number"], new_count)
        except Exception as e:
            log.exception("docket_check failed for %s: %s",
                          d.get("docket_number"), e)
            _notify_job_failed("docket_check", e)


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
    s.add_job(
        _weekly_digest_job,
        CronTrigger(day_of_week="mon", hour=7, minute=30),
        id="weekly_digest",
        replace_existing=True,
    )
    s.add_job(
        _prune_images_job,
        CronTrigger(day_of_week="sun", hour=5, minute=0),
        id="prune_images",
        replace_existing=True,
    )
    s.add_job(
        _docket_check_job,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=15),
        id="docket_check",
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
