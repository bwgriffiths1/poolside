"""User administration — the Admin → Users panel.

List accounts, change roles, activate/deactivate. Mounted admin-only in
api/main.py (_ADMIN), so every endpoint here already sits behind
require_admin; handlers re-declare current_user only to identify the
caller (FastAPI's per-request dependency cache makes that free).

Guard rails:
  * you cannot demote or deactivate your own account;
  * no change may leave Poolside with zero active admins (409).
Role/deactivation changes take effect on the target's next request —
current_user reads the row fresh every time.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from pipeline import db

from ..auth import VALID_ROLES, current_user

router = APIRouter(prefix="/api/admin", tags=["admin-users"])


def _serialize(row: dict) -> dict[str, Any]:
    out = dict(row)
    for k in ("created_at", "last_login"):
        v = out.get(k)
        if v is not None and hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


@router.get("/users")
def list_users() -> list[dict[str, Any]]:
    return [_serialize(u) for u in db.list_app_users()]


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: dict[str, Any] = Body(...),
    caller: dict = Depends(current_user),
) -> dict[str, Any]:
    """Patch a user's role and/or active flag. Body: {role?, is_active?}."""
    role = body.get("role")
    is_active = body.get("is_active")
    if role is None and is_active is None:
        raise HTTPException(status_code=400,
                            detail="Provide role and/or is_active")
    if role is not None and role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of: {', '.join(VALID_ROLES)}",
        )
    if is_active is not None and not isinstance(is_active, bool):
        raise HTTPException(status_code=400, detail="is_active must be a boolean")

    target = db.get_app_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    changes_role = role is not None and role != target.get("role")
    deactivates = is_active is False and target.get("is_active", True)

    if user_id == caller["id"] and (changes_role or deactivates):
        raise HTTPException(
            status_code=400,
            detail="You can't change your own role or deactivate your own account.",
        )

    # Last-admin guard: never leave zero active admins.
    losing_an_admin = (
        target.get("role") == "admin"
        and target.get("is_active", True)
        and ((role is not None and role != "admin") or is_active is False)
    )
    if losing_an_admin and db.count_active_admins(exclude_user_id=user_id) == 0:
        raise HTTPException(
            status_code=409,
            detail="That would leave Poolside with no active admins.",
        )

    row = db.update_app_user(user_id, role=role, is_active=is_active)
    return _serialize(row)
