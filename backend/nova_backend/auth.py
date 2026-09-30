"""NOVA backend — multi-user authentication (signup, login, sessions).

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib only — no extra
dependency) using a per-user random salt.  Sessions are opaque random
tokens stored server side so they can be revoked individually.

Token transport: the frontend sends ``Authorization: Bearer <token>`` on
every request (including the SSE streams), and the server also sets an
HttpOnly cookie for same-origin deployments such as the Docker/nginx setup.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from typing import Any, Optional

from .config import settings
from .memory.store import store

log = logging.getLogger("nova.auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
MIN_PASSWORD_LEN = 8

#: sliding-window login throttle: key -> (attempts, window_start_ts)
_THROTTLE: dict[str, tuple[int, float]] = {}
_THROTTLE_LOCK = threading.Lock()
THROTTLE_MAX_ATTEMPTS = 8
THROTTLE_WINDOW_S = 300.0


class AuthError(Exception):
    """Raised with a user-facing message; mapped to HTTP 4xx by the routes."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not USERNAME_RE.match(name):
        raise AuthError(
            "Username must be 3-32 characters: letters, digits, '_', '-', '.'"
        )
    return name


def validate_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password) > 200:
        raise AuthError("Password is too long")
    return password


def hash_password(password: str, salt: Optional[str] = None, iterations: Optional[int] = None) -> tuple[str, str, int]:
    """Returns ``(hash_hex, salt_hex, iterations)``."""
    salt_hex = salt or secrets.token_hex(16)
    rounds = int(iterations or settings.pbkdf2_iterations)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), rounds
    )
    return digest.hex(), salt_hex, rounds


def verify_password(password: str, salt: str, expected_hash: str, iterations: int) -> bool:
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected_hash or "")


# ---------------------------------------------------------------------------
# login throttle
# ---------------------------------------------------------------------------


def _throttle_key(username: str, client: str) -> str:
    return f"{(username or '').lower()}|{client or 'anon'}"


def throttle_check(key: str) -> None:
    with _THROTTLE_LOCK:
        attempts, started = _THROTTLE.get(key, (0, 0.0))
        if attempts and time.time() - started > THROTTLE_WINDOW_S:
            _THROTTLE.pop(key, None)
            attempts = 0
        if attempts >= THROTTLE_MAX_ATTEMPTS:
            raise AuthError("Too many attempts — try again in a few minutes", 429)


def throttle_fail(key: str) -> None:
    with _THROTTLE_LOCK:
        attempts, started = _THROTTLE.get(key, (0, 0.0))
        if not attempts or time.time() - started > THROTTLE_WINDOW_S:
            started = time.time()
            attempts = 0
        _THROTTLE[key] = (attempts + 1, started)


def throttle_reset(key: str) -> None:
    with _THROTTLE_LOCK:
        _THROTTLE.pop(key, None)


# ---------------------------------------------------------------------------
# accounts + sessions
# ---------------------------------------------------------------------------


def register(username: str, password: str) -> dict[str, Any]:
    name = validate_username(username)
    validate_password(password)
    if store.get_user(name):
        raise AuthError("That username is already taken", 409)
    salt = secrets.token_hex(16)
    password_hash, salt, iterations = hash_password(password, salt=salt)
    user = store.create_user(name, password_hash, salt, iterations)
    log.info("registered user %s", user["username"])
    return public_user(user)


def login(username: str, password: str, user_agent: str = "") -> dict[str, Any]:
    name = (username or "").strip()
    key = _throttle_key(name, user_agent)
    throttle_check(key)
    user = store.get_user(name)
    if user is None or not verify_password(
        password or "", user["salt"], user["password_hash"], user["iterations"]
    ):
        throttle_fail(key)
        # constant-ish response time for unknown users
        if user is None:
            hash_password(password or "invalid", salt="00" * 16, iterations=10000)
        raise AuthError("Invalid username or password", 401)
    throttle_reset(key)
    session = create_session(int(user["id"]), user_agent)
    return {"user": public_user(user), "token": session["token"], "expires_ts": session["expires_ts"]}


def create_session(user_id: int, user_agent: str = "") -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    expires_ts = time.time() + settings.session_ttl_hours * 3600
    session = store.create_session(token, int(user_id), expires_ts, user_agent)
    log.debug("session created for user %s", user_id)
    return session


def authenticate(token: Optional[str]) -> Optional[dict[str, Any]]:
    """Token -> ``{id, username, ...}`` (never includes the hash)."""
    if not token or not settings.auth_enabled:
        return None
    session = store.get_session(token)
    if session is None:
        return None
    user = store.get_user_by_id(int(session["user_id"]))
    if user is None:
        store.delete_session(token)
        return None
    return public_user(user)


def logout(token: Optional[str]) -> bool:
    return bool(token and store.delete_session(token))


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(user["id"]),
        "username": user["username"],
        "created_at": user.get("created_at"),
    }


def token_from_header(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return header.strip() or None


__all__ = [
    "AuthError",
    "authenticate",
    "create_session",
    "hash_password",
    "login",
    "logout",
    "public_user",
    "register",
    "token_from_header",
    "verify_password",
]
