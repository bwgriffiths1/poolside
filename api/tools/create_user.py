"""CLI helper to create or update a Poolside user.

Usage:
    python -m api.tools.create_user <email> <name> [role]

role ∈ admin|editor|viewer, default admin — this tool is the bootstrap
path, so it preserves the old everything-is-admin semantics unless told
otherwise. Prompts for a password. If the email already exists, the
password, name (and role, when given) are updated in place.
"""
from __future__ import annotations

import getpass
import sys

from api.auth import VALID_ROLES
from pipeline.auth import create_user, get_user_by_email, hash_password
from pipeline.db import _conn, _cursor


def _update_user(email: str, name: str, password: str, role: str) -> dict:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """UPDATE app_users
                       SET name = %s, password_hash = %s,
                           auth_provider = 'local', role = %s
                     WHERE email = %s
                 RETURNING *""",
                (name, hash_password(password), role, email),
            )
            return cur.fetchone()


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print("usage: python -m api.tools.create_user <email> <name> [admin|editor|viewer]",
              file=sys.stderr)
        return 2

    email = sys.argv[1].strip().lower()
    name = sys.argv[2].strip()
    role = sys.argv[3].strip().lower() if len(sys.argv) == 4 else "admin"
    if role not in VALID_ROLES:
        print(f"role must be one of: {', '.join(VALID_ROLES)}", file=sys.stderr)
        return 2
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm:  ")
    if pw1 != pw2:
        print("passwords do not match", file=sys.stderr)
        return 1
    if len(pw1) < 6:
        print("password must be at least 6 characters", file=sys.stderr)
        return 1

    existing = get_user_by_email(email)
    if existing:
        _update_user(email, name, pw1, role)
        print(f"updated user: {email} ({role})")
    else:
        create_user(email=email, name=name, password=pw1,
                    auth_provider="local", role=role)
        print(f"created user: {email} ({role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
