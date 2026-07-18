"""CLI helper to create or update a Poolside user.

Usage:
    python -m api.tools.create_user <email> <name>

Prompts for a password. If the email already exists, the password (and name)
are updated in place.
"""
from __future__ import annotations

import getpass
import sys

from pipeline.auth import create_user, get_user_by_email, hash_password
from pipeline.db import _conn, _cursor


def _update_password_and_name(email: str, name: str, password: str) -> dict:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """UPDATE app_users
                       SET name = %s, password_hash = %s, auth_provider = 'local'
                     WHERE email = %s
                 RETURNING *""",
                (name, hash_password(password), email),
            )
            return cur.fetchone()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python -m api.tools.create_user <email> <name>", file=sys.stderr)
        return 2

    email = sys.argv[1].strip().lower()
    name = sys.argv[2].strip()
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
        _update_password_and_name(email, name, pw1)
        print(f"updated user: {email}")
    else:
        create_user(email=email, name=name, password=pw1, auth_provider="local")
        print(f"created user: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
