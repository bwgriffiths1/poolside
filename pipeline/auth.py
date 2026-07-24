"""
pipeline/auth.py — Local DB authentication: password hashing + user CRUD.

Consumed by api/auth.py and api/routes/{auth,user_tokens}.py. The Streamlit
session/cookie layer that used to live here was removed with the Streamlit
app (2026-07); FastAPI sessions are handled in api/auth.py.
"""

import bcrypt

from pipeline.db import _conn, _cursor

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

# Pre-computed dummy hash so authenticate_user takes constant time
# even when the email does not exist (prevents timing-based enumeration).
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode("utf-8")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def get_user_by_email(email: str) -> dict | None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM app_users WHERE email = %s",
                (email,),
            )
            return cur.fetchone()


def create_user(email: str, name: str, password: str,
                auth_provider: str = "local", role: str = "viewer") -> dict:
    pw_hash = hash_password(password)
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """INSERT INTO app_users (email, name, password_hash, auth_provider, role)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING *""",
                (email, name, pw_hash, auth_provider, role),
            )
            return cur.fetchone()


def set_user_password(user_id: int, password: str) -> None:
    """Replace a user's password_hash. Used by the password-reset flow."""
    pw_hash = hash_password(password)
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE app_users SET password_hash = %s WHERE id = %s",
                (pw_hash, user_id),
            )


def update_last_login(email: str) -> None:
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE app_users SET last_login = NOW() WHERE email = %s",
                (email,),
            )


def authenticate_user(email: str, password: str) -> dict | None:
    """Verify credentials. Returns user dict on success, None on failure."""
    user = get_user_by_email(email)
    if user is None:
        # Constant-time: still run a bcrypt check so timing doesn't leak
        # whether the email exists.
        verify_password(password, _DUMMY_HASH)
        return None
    if not user.get("is_active", True):
        return None
    if not user.get("password_hash"):
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    update_last_login(email)
    return user
