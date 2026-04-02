"""
Authentication routes: login, logout, password management.

Single-password system with session cookies. Default password is 'survey365'.
"""

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    hash_password,
    is_password_set,
    validate_session_token,
    verify_password,
)
from ..db import get_config, set_config

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1)


class PasswordChangeRequest(BaseModel):
    current: str | None = None
    new_password: str = Field(..., min_length=4, max_length=128)


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    """Authenticate with password and receive a session cookie.

    Returns 401 if password is incorrect.
    Returns 400 if no password has been set yet (first boot edge case --
    should not happen since ensure_default_password sets one at startup).
    """
    pw_hash = await get_config("web_password_hash")

    if not pw_hash:
        raise HTTPException(
            status_code=400,
            detail="No password set. Set one via PUT /api/auth/password first.",
        )

    valid = await verify_password(req.password, pw_hash)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid password")

    # Create session token and set cookie
    token = await create_session_token()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # Running behind Nginx on LAN; set True if behind HTTPS
    )

    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return {"ok": True}


@router.put("/password")
async def change_password(req: PasswordChangeRequest, response: Response):
    """Change the admin password.

    On first boot (no password set), the 'current' field can be null.
    Otherwise, the current password must be verified before accepting the new one.
    """
    pw_hash = await get_config("web_password_hash")

    if pw_hash:
        # Password already set -- require current password
        if req.current is None:
            raise HTTPException(
                status_code=401,
                detail="Current password is required",
            )
        valid = await verify_password(req.current, pw_hash)
        if not valid:
            raise HTTPException(status_code=401, detail="Current password incorrect")

    # Hash and store new password
    new_hash = await hash_password(req.new_password)
    await set_config("web_password_hash", new_hash)

    # Issue a new session token so the user stays logged in after password change
    token = await create_session_token()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )

    return {"ok": True}


@router.get("/check")
async def check_auth(request: Request):
    """Check authentication state.

    No auth required. Frontend calls this on load to decide whether
    to show the login prompt for admin features.

    Reads the session cookie directly from the request to avoid
    FastAPI's Cookie dependency raising 422 when the cookie is absent.
    """
    password_set = await is_password_set()
    authenticated = False

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        authenticated = await validate_session_token(token)

    return {
        "authenticated": authenticated,
        "password_set": password_set,
    }
