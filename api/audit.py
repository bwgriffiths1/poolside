"""Audit middleware — auto-capture every authenticated non-GET API action.

One place instead of ~40 per-route record calls: after the response, log
method/path/route-template/params/status/duration + who, into audit_log.
current_user stashes the user on request.state during dependency
resolution (state rides the shared ASGI scope, so it's visible here after
call_next — pinned by tests/test_audit_middleware.py).

Deliberate choices:
  * No request bodies — passwords and multi-MB uploads stay out of the
    log by construction; path params carry the semantics.
  * 4xx/5xx rows are kept (flagged in the UI): a viewer probing an
    editor endpoint lands here as a 403 with the user attached — free
    security signal. 401s never appear (no request.state.user).
  * /api/auth/* is excluded (login/logout churn, no session on login),
    /api/public/* (anonymous by design), /api/track/* (read analytics
    would double-log every page view as an audit action).
  * Capture is wrapped in try/except and must NEVER break a request; the
    synchronous insert is fine for this single-worker deploy.
"""
from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from pipeline import db

log = logging.getLogger("poolside.audit")

_SKIP_METHODS = {"GET", "HEAD", "OPTIONS"}
_SKIP_PREFIXES = ("/api/auth/", "/api/public/", "/api/track/")


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Unhandled handler crash: call_next raises instead of
            # returning — record the attempt as a 500, then let the
            # server error path proceed unchanged.
            self._capture(request, status=500, start=start)
            raise
        self._capture(request, status=response.status_code, start=start)
        return response

    def _capture(self, request: Request, status: int, start: float) -> None:
        try:
            path = request.url.path
            if (
                request.method in _SKIP_METHODS
                or not path.startswith("/api/")
                or any(path.startswith(p) for p in _SKIP_PREFIXES)
            ):
                return
            user = getattr(request.state, "user", None)
            if not user:
                return  # 401s and anonymous surfaces

            route = request.scope.get("route")
            db.record_audit({
                "user_id": user.get("id"),
                "user_email": user.get("email") or "",
                "method": request.method,
                "path": path,
                "route": getattr(route, "path_format", None),
                "path_params": dict(request.scope.get("path_params") or {}),
                "query": request.url.query or None,
                "status": status,
                "duration_ms": int((time.monotonic() - start) * 1000),
            })
        except Exception:  # the log must never break the action
            log.exception("audit capture failed for %s %s",
                          request.method, request.url.path)
