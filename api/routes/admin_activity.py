"""Activity feed — the audit log, read side (Admin → Activity).

The middleware (api/audit.py) records raw method + route template; this
router translates rows into human labels via _LABELS at read time, so
labeling improvements never require touching stored data. Unmapped routes
degrade to "METHOD /route" — visible, never dropped.

Mounted _ADMIN in api/main.py. (PR 3 adds the read-analytics views
endpoints alongside.)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pipeline import db

router = APIRouter(prefix="/api/admin", tags=["admin-activity"])

# (method, route template) -> label. Path params fill the story in the UI.
_LABELS: dict[tuple[str, str], str] = {
    # Briefings / summaries
    ("POST", "/api/meetings/{meeting_id}/briefing/approve"): "Approved briefing",
    ("POST", "/api/meetings/{meeting_id}/briefing/unapprove"): "Unapproved briefing",
    ("PUT", "/api/summaries/{entity_type}/{entity_id}"): "Edited summary",
    ("POST", "/api/summaries/{entity_type}/{entity_id}/versions/{version_id}/restore"): "Restored summary version",
    ("POST", "/api/meetings/{meeting_id}/summarize"): "Started summarize run",
    # Meetings / documents
    ("DELETE", "/api/meetings/{meeting_id}"): "Deleted meeting",
    ("DELETE", "/api/meetings/{meeting_id}/documents"): "Deleted all meeting documents",
    ("POST", "/api/meetings/{meeting_id}/attachments"): "Uploaded attachment",
    ("DELETE", "/api/attachments/{attachment_id}"): "Deleted attachment",
    ("POST", "/api/agenda-items/{item_id}/materials"): "Added item material",
    ("DELETE", "/api/documents/{doc_id}"): "Deleted document",
    ("PATCH", "/api/documents/{doc_id}"): "Updated document",
    ("PATCH", "/api/documents/{doc_id}/item"): "Reassigned document",
    ("POST", "/api/agenda-items/{item_id}/documents/{doc_id}"): "Assigned document",
    ("DELETE", "/api/agenda-items/{item_id}/documents/{doc_id}"): "Unassigned document",
    # Agenda items
    ("POST", "/api/meetings/{meeting_id}/agenda-items"): "Added agenda item",
    ("PATCH", "/api/agenda-items/{row_id}"): "Edited agenda item",
    ("DELETE", "/api/agenda-items/{row_id}"): "Deleted agenda item",
    ("POST", "/api/agenda-items/{row_id}/resummarize"): "Re-ran item summary",
    # Generation
    ("POST", "/api/roundups/generate"): "Generated roundup",
    ("DELETE", "/api/roundups/{roundup_id}"): "Deleted roundup",
    ("POST", "/api/deep-dives"): "Created deep dive",
    ("POST", "/api/deep-dives/{report_id}/rerun"): "Re-ran deep dive",
    ("DELETE", "/api/deep-dives/{report_id}"): "Deleted deep dive",
    ("POST", "/api/initiatives/{code}/brief"): "Generated initiative brief",
    # Dockets
    ("POST", "/api/dockets"): "Added docket",
    ("PATCH", "/api/dockets/{docket_id}"): "Updated docket",
    ("DELETE", "/api/dockets/{docket_id}"): "Deleted docket",
    ("POST", "/api/dockets/{docket_id}/sync"): "Synced docket",
    ("POST", "/api/dockets/{docket_id}/state-of-play"): "Generated state of play",
    ("POST", "/api/docket-jobs/{job_id}/cancel"): "Cancelled docket job",
    # Share links
    ("POST", "/api/meetings/{meeting_id}/share"): "Created share link",
    ("DELETE", "/api/share-tokens/{token_id}"): "Revoked share link",
    # Jobs
    ("POST", "/api/jobs/{job_id}/cancel"): "Cancelled summarize job",
    # Ingest
    ("POST", "/api/admin/ingest-by-url"): "Ingested meeting by URL",
    # Admin ops
    ("POST", "/api/admin/discover"): "Ran calendar discovery",
    ("POST", "/api/admin/refresh"): "Refreshed upcoming meetings",
    ("POST", "/api/admin/refresh-materials/{meeting_id}"): "Re-checked meeting materials",
    ("POST", "/api/admin/parse-agenda/{meeting_id}"): "Parsed agenda",
    ("POST", "/api/admin/bump-lifecycle/{meeting_id}"): "Bumped lifecycle",
    ("POST", "/api/admin/cleanup-zip-expansion/{meeting_id}"): "Reset zip expansion",
    ("POST", "/api/admin/images/prune"): "Pruned images",
    # Config / prompts
    ("PUT", "/api/admin/config"): "Updated app config",
    ("PUT", "/api/prompts/{slug}"): "Edited prompt",
    ("DELETE", "/api/prompts/{slug}"): "Reset prompt override",
    ("PUT", "/api/model-config"): "Updated model config",
    # Users
    ("PATCH", "/api/admin/users/{user_id}"): "Changed user role/status",
    ("POST", "/api/admin/invites"): "Created invite",
    ("POST", "/api/admin/password-resets"): "Created password reset",
    ("DELETE", "/api/admin/user-tokens/{token_id}"): "Revoked invite/reset token",
    # Self-service (viewer-permitted; still audited)
    ("POST", "/api/watches/by-meeting/{meeting_id}"): "Watched meeting",
    ("DELETE", "/api/watches/by-meeting/{meeting_id}"): "Unwatched meeting",
    ("POST", "/api/notifications/mark-read"): "Marked notifications read",
    ("PATCH", "/api/me/prefs"): "Updated email prefs",
    ("POST", "/api/ask"): "Asked Poolside",
}


def _label(row: dict) -> str:
    key = (row.get("method"), row.get("route"))
    if key in _LABELS:
        return _LABELS[key]
    return f"{row.get('method')} {row.get('route') or row.get('path')}"


def _serialize(row: dict) -> dict[str, Any]:
    out = dict(row)
    out["label"] = _label(row)
    v = out.get("created_at")
    if v is not None and hasattr(v, "isoformat"):
        out["created_at"] = v.isoformat()
    return out


@router.get("/audit")
def list_audit(
    limit: int = 50,
    before_id: int | None = None,
    user: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    rows = db.list_audit(limit=limit, before_id=before_id, user_email=user)
    items = [_serialize(r) for r in rows]
    next_before = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "next_before_id": next_before}
