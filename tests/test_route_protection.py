"""Route-inventory invariant: the mount partition IS the permission policy.

Walks every registered /api route and asserts the gating contract from
api/main.py, so a future router mounted without a gate (or an admin op
that loses its require_admin) fails CI instead of shipping open. This is
the default-deny safety net the design traded middleware away for.
"""
from fastapi.routing import APIRoute

import api.main as m
from api.auth import (
    current_user,
    require_admin,
    require_editor,
    require_editor_for_writes,
)

# Routers whose endpoints are viewer-permitted by design (_ANY mounts).
VIEWER_ROUTER_PREFIXES = (
    "/api/me",
    "/api/notifications",
    "/api/watches",
    "/api/ask",
    "/api/track",
)

# Anonymous by design.
PUBLIC_EXACT = {"/api/health", "/api/auth/login", "/api/auth/logout"}
PUBLIC_PREFIXES = ("/api/public/",)

ROLE_GATES = {require_admin, require_editor, require_editor_for_writes}
SAFE = {"GET", "HEAD", "OPTIONS"}


def _dep_calls(dependant) -> set:
    out = set()
    for d in dependant.dependencies:
        if d.call is not None:
            out.add(d.call)
        out |= _dep_calls(d)
    return out


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _api_routes() -> list[APIRoute]:
    return [
        r for r in m.app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api")
    ]


def _is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or any(path.startswith(p) for p in PUBLIC_PREFIXES)


def test_every_private_api_route_requires_a_session():
    missing = []
    for r in _api_routes():
        if _is_public(r.path):
            continue
        if current_user not in _dep_calls(r.dependant):
            missing.append(f"{sorted(r.methods)} {r.path}")
    assert not missing, f"routes without current_user: {missing}"


def test_public_routes_carry_no_session_dependency():
    for r in _api_routes():
        if _is_public(r.path):
            assert current_user not in _dep_calls(r.dependant), (
                f"public route {r.path} grew a session dependency"
            )


def test_every_write_outside_viewer_routers_has_a_role_gate():
    missing = []
    for r in _api_routes():
        if _is_public(r.path):
            continue
        if not (r.methods - SAFE):
            continue  # read-only route
        if any(_under(r.path, p) for p in VIEWER_ROUTER_PREFIXES):
            continue  # self-service surface — viewer-permitted by design
        if not (ROLE_GATES & _dep_calls(r.dependant)):
            missing.append(f"{sorted(r.methods)} {r.path}")
    assert not missing, f"write routes without a role gate: {missing}"


# ── Admin surface ───────────────────────────────────────────────────────

ADMIN_REQUIRED = [
    # admin-only routers
    "/api/admin/usage",
    "/api/prompts",
    "/api/prompts/{slug}",
    "/api/model-config",
    "/api/admin/users",
    "/api/admin/users/{user_id}",
    # user-token management
    "/api/admin/invites",
    "/api/admin/password-resets",
    "/api/admin/user-tokens",
    "/api/admin/user-tokens/{token_id}",
    # the six owner-named admin ops in admin.py
    "/api/admin/discover",
    "/api/admin/refresh",
    "/api/admin/images/prune",
    "/api/admin/parse-agenda/{meeting_id}",
    "/api/admin/bump-lifecycle/{meeting_id}",
    "/api/admin/cleanup-zip-expansion/{meeting_id}",
]


def test_admin_surface_requires_admin():
    by_path: dict[str, list[APIRoute]] = {}
    for r in _api_routes():
        by_path.setdefault(r.path, []).append(r)
    for path in ADMIN_REQUIRED:
        routes = by_path.get(path)
        assert routes, f"expected admin route {path} not registered"
        for r in routes:
            assert require_admin in _dep_calls(r.dependant), (
                f"{sorted(r.methods)} {path} lost require_admin"
            )


def test_editor_workflow_endpoints_stay_editor_level():
    """The admin-prefixed endpoints that are actually part of the everyday
    editor/viewer flow must NOT be admin-gated (Overview widgets, the
    Meeting-page re-check button, Settings/Add config reads, URL ingest)."""
    for r in _api_routes():
        if r.path in (
            "/api/admin/refresh-materials/{meeting_id}",
            "/api/admin/scheduler",
            "/api/admin/venues",
            "/api/admin/ingest-by-url",
        ):
            assert require_admin not in _dep_calls(r.dependant), (
                f"{r.path} must stay editor/viewer-accessible"
            )
        if r.path == "/api/admin/config":
            calls = _dep_calls(r.dependant)
            if r.methods & {"PUT"}:
                assert require_admin in calls, "PUT /api/admin/config must be admin"
            else:
                assert require_admin not in calls, "GET /api/admin/config stays open"


def test_share_management_is_editor_gated():
    seen = set()
    for r in _api_routes():
        calls = _dep_calls(r.dependant)
        if r.path == "/api/meetings/{meeting_id}/share" and "POST" in r.methods:
            assert require_editor in calls
            seen.add("create")
        if r.path == "/api/share-tokens/{token_id}" and "DELETE" in r.methods:
            assert require_editor in calls
            seen.add("revoke")
    assert seen == {"create", "revoke"}


def test_token_accept_stays_anonymous():
    for r in _api_routes():
        if r.path == "/api/public/user-tokens/{token}/accept":
            assert current_user not in _dep_calls(r.dependant)
            return
    raise AssertionError("accept endpoint not found")
