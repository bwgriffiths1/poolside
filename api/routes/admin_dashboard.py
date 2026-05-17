"""Admin dashboard endpoints — usage / cost rollups from summarize_jobs.

Keep these read-only. Mutations (invite, delete, etc.) live in their own
route modules.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from pipeline import db_new as db
from ..auth import current_user

router = APIRouter(prefix="/api/admin", tags=["admin-dashboard"])


def _start_of_month(d: date) -> date:
    return d.replace(day=1)


def _months_back(d: date, n: int) -> date:
    """Return the first day of the month that is *n* months before d."""
    year = d.year
    month = d.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


@router.get("/usage")
def usage_dashboard(_: dict = Depends(current_user)) -> dict[str, Any]:
    """Summarization cost + token rollups for the dashboard.

    Returns:
        {
          "this_month": { cost_usd, input_tokens, output_tokens, jobs },
          "last_month": { ...same shape },
          "by_committee_this_month": [ { committee, venue, cost_usd, jobs }, ... ],
          "trailing_six_months": [ { month: "2026-05", cost_usd, jobs }, ... ],
        }
    """
    today = date.today()
    this_month_start = _start_of_month(today)
    last_month_start = _months_back(today, 1)
    six_months_start = _months_back(today, 5)  # current month + 5 prior = 6

    with db._conn() as conn:
        with db._cursor(conn) as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(cost_usd), 0)::float       AS cost_usd,
                    COALESCE(SUM(input_tokens), 0)::bigint  AS input_tokens,
                    COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
                    COUNT(*)::int                            AS jobs
                FROM summarize_jobs
                WHERE status = 'complete'
                  AND finished_at >= %s
                """,
                (this_month_start,),
            )
            this_month = dict(cur.fetchone() or {})

            cur.execute(
                """
                SELECT
                    COALESCE(SUM(cost_usd), 0)::float       AS cost_usd,
                    COALESCE(SUM(input_tokens), 0)::bigint  AS input_tokens,
                    COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
                    COUNT(*)::int                            AS jobs
                FROM summarize_jobs
                WHERE status = 'complete'
                  AND finished_at >= %s
                  AND finished_at <  %s
                """,
                (last_month_start, this_month_start),
            )
            last_month = dict(cur.fetchone() or {})

            cur.execute(
                """
                SELECT
                    v.short_name AS venue,
                    mt.short_name AS committee,
                    COALESCE(SUM(sj.cost_usd), 0)::float AS cost_usd,
                    COUNT(*)::int AS jobs
                FROM summarize_jobs sj
                JOIN meetings m       ON m.id  = sj.meeting_id
                JOIN meeting_types mt ON mt.id = m.meeting_type_id
                JOIN venues v         ON v.id  = mt.venue_id
                WHERE sj.status = 'complete'
                  AND sj.finished_at >= %s
                GROUP BY v.short_name, mt.short_name
                ORDER BY cost_usd DESC
                """,
                (this_month_start,),
            )
            by_committee = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    to_char(date_trunc('month', finished_at), 'YYYY-MM') AS month,
                    COALESCE(SUM(cost_usd), 0)::float AS cost_usd,
                    COUNT(*)::int                      AS jobs
                FROM summarize_jobs
                WHERE status = 'complete'
                  AND finished_at >= %s
                GROUP BY 1
                ORDER BY 1
                """,
                (six_months_start,),
            )
            trailing = [dict(r) for r in cur.fetchall()]

    return {
        "this_month": this_month,
        "last_month": last_month,
        "by_committee_this_month": by_committee,
        "trailing_six_months": trailing,
        "month_label": today.strftime("%B %Y"),
    }
