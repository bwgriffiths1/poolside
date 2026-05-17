"""
pipeline/auth.py — Local DB authentication + unified auth helpers.

Provides password hashing, user CRUD, and a session abstraction that works
for both Google OIDC (via st.login) and local email/password login.
Local sessions are persisted via a signed cookie so they survive page
refreshes, tab closes, and brief network disconnects.
"""

import hashlib
import hmac
import time

import bcrypt

from pipeline.db_new import _conn, _cursor

# ---------------------------------------------------------------------------
# Cookie-based local session persistence
# ---------------------------------------------------------------------------
_SESSION_COOKIE = "local_session"
_MAX_AGE = 7 * 24 * 3600  # 1 week


def _cookie_secret() -> str:
    import streamlit as st  # local import; keep this module importable from FastAPI
    return st.secrets.get("auth", {}).get("cookie_secret", "fallback-secret")


def _sign(payload: str) -> str:
    """HMAC-sign a payload string."""
    return hmac.new(_cookie_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def _make_session_cookie(email: str) -> str:
    """Build a signed cookie value: email|expiry|signature."""
    expiry = int(time.time()) + _MAX_AGE
    payload = f"{email}|{expiry}"
    sig = _sign(payload)
    return f"{payload}|{sig}"


def _verify_session_cookie(raw: str) -> str | None:
    """Return email if the cookie is valid and not expired, else None."""
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    email, expiry_str, sig = parts
    payload = f"{email}|{expiry_str}"
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    if int(expiry_str) < int(time.time()):
        return None
    return email


def _set_cookie(name: str, value: str, max_age: int) -> None:
    """Inject a tiny JS snippet to set a browser cookie."""
    import streamlit.components.v1 as components  # local import
    components.html(
        f"""<script>
        document.cookie = "{name}={value}; path=/; max-age={max_age}; SameSite=Lax";
        </script>""",
        height=0,
    )


def _clear_cookie(name: str) -> None:
    _set_cookie(name, "", 0)


def restore_local_session() -> bool:
    """Check for a valid session cookie and restore session state.
    Call early in app.py before is_authenticated().
    Returns True if a session was restored."""
    import streamlit as st
    if st.session_state.get("local_authenticated"):
        return True
    raw = st.context.cookies.get(_SESSION_COOKIE)
    if not raw:
        return False
    email = _verify_session_cookie(raw)
    if not email:
        return False
    user = get_user_by_email(email)
    if not user or not user.get("is_active", True):
        return False
    st.session_state["local_authenticated"] = True
    st.session_state["user_name"] = user["name"]
    st.session_state["user_email"] = user["email"]
    return True

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
                auth_provider: str = "local") -> dict:
    pw_hash = hash_password(password)
    with _conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """INSERT INTO app_users (email, name, password_hash, auth_provider)
                   VALUES (%s, %s, %s, %s)
                   RETURNING *""",
                (email, name, pw_hash, auth_provider),
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


# ---------------------------------------------------------------------------
# Unified session helpers
# ---------------------------------------------------------------------------

def is_authenticated() -> bool:
    """True if logged in via Google OIDC or local DB."""
    import streamlit as st
    if st.user.is_logged_in:
        return True
    return st.session_state.get("local_authenticated", False)


def get_current_user() -> dict:
    """Return {name, email, provider} for the authenticated user."""
    import streamlit as st
    if st.user.is_logged_in:
        return {
            "name": st.user.get("name", "User"),
            "email": st.user.get("email", ""),
            "provider": "google",
        }
    return {
        "name": st.session_state.get("user_name", "User"),
        "email": st.session_state.get("user_email", ""),
        "provider": "local",
    }


def login_local_user(user: dict) -> None:
    """Set session state and persistent cookie after successful local auth."""
    import streamlit as st
    st.session_state["local_authenticated"] = True
    st.session_state["user_name"] = user["name"]
    st.session_state["user_email"] = user["email"]
    _set_cookie(_SESSION_COOKIE, _make_session_cookie(user["email"]), _MAX_AGE)


def logout_local_user() -> None:
    """Clear local auth session state and cookie."""
    import streamlit as st
    for key in ("local_authenticated", "user_name", "user_email"):
        st.session_state.pop(key, None)
    _clear_cookie(_SESSION_COOKIE)
