"""
Simple password authentication with signed session cookies.

Single-password system: one password protects admin endpoints.
Field-facing endpoints (status, mode, sites) do not require auth.

Password hash stored in config table under 'web_password_hash'.
Session secret stored in config table under 'session_secret' (auto-generated on first boot).
Cookie name: s365_session, max age: 24 hours.
"""

import hashlib
import os
import secrets
import time

from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .db import get_config, set_config

SESSION_COOKIE_NAME = "s365_session"
SESSION_MAX_AGE = 86400  # 24 hours in seconds
DEFAULT_PASSWORD = "survey365"

# Cache the serializer after first initialization
_serializer: URLSafeTimedSerializer | None = None


async def _get_serializer() -> URLSafeTimedSerializer:
    """Get or create the timed serializer using the session secret from DB."""
    global _serializer
    if _serializer is not None:
        return _serializer

    secret = await get_config("session_secret")
    if not secret:
        secret = secrets.token_hex(32)
        await set_config("session_secret", secret)

    _serializer = URLSafeTimedSerializer(secret, salt="s365-session")
    return _serializer


def reset_serializer():
    """Reset the cached serializer (used when secret changes or during tests)."""
    global _serializer
    _serializer = None


async def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-SHA256."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"pbkdf2:sha256:260000${salt}${dk.hex()}"


async def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a PBKDF2-SHA256 hash."""
    if not password_hash:
        return False
    try:
        parts = password_hash.split("$")
        if len(parts) != 3:
            return False
        header, salt, stored_hash = parts
        iterations = int(header.split(":")[-1])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
        return secrets.compare_digest(dk.hex(), stored_hash)
    except (ValueError, IndexError):
        return False


async def is_password_set() -> bool:
    """Check whether a password has been set (non-empty hash in config)."""
    pw_hash = await get_config("web_password_hash")
    return bool(pw_hash)


async def ensure_default_password():
    """On first boot, set the default password if none exists."""
    pw_hash = await get_config("web_password_hash")
    if not pw_hash:
        hashed = await hash_password(DEFAULT_PASSWORD)
        await set_config("web_password_hash", hashed)


async def create_session_token() -> str:
    """Create a signed session token containing the current timestamp."""
    serializer = await _get_serializer()
    return serializer.dumps({"t": int(time.time())})


async def validate_session_token(token: str) -> bool:
    """Validate a session token. Returns True if valid and not expired."""
    if not token:
        return False
    try:
        serializer = await _get_serializer()
        serializer.loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_admin(request: Request):
    """FastAPI dependency that enforces admin authentication via session cookie.

    Raises HTTPException(401) if the session cookie is missing, invalid, or expired.
    Use as: Depends(require_admin)
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    valid = await validate_session_token(token)
    if not valid:
        raise HTTPException(status_code=401, detail="Session expired or invalid")


async def is_admin_request(request: Request) -> bool:
    """Return True when the request carries a valid admin session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    return await validate_session_token(token)
