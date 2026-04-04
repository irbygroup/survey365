"""
Configuration routes: read/write app settings.

Admin-only access for full config. Public endpoint for MapTiler key
(needed by field crew for the map to work).

Known config keys and their types:
- maptiler_key (str): MapTiler API key for basemaps
- auto_resume (bool as str): Resume last session on boot
- default_lat/default_lon/default_zoom: default map view
- gnss_port/gnss_baud/gnss_backend: receiver connection settings
- antenna_height_m: antenna reference point height above ground/mark in meters
- rtcm_messages: RTCM output selection, e.g. "1005,1077,1087,1097,1127,1230(10)"
- rinex_enabled/rinex_rotate_hours/rinex_data_dir: raw logging settings
- local_caster_enabled/local_caster_port/local_caster_mountpoint: local NTRIP caster
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_admin
from ..db import get_all_config, get_config, set_config

router = APIRouter(prefix="/api/config", tags=["config"])

# Keys that are safe to expose publicly (no auth required)
PUBLIC_KEYS = {"maptiler_key", "default_lat", "default_lon", "default_zoom"}

# Keys that are never returned in API responses
SECRET_KEYS = {"web_password_hash", "session_secret"}

# All known config keys (rejects unknown keys on write)
KNOWN_KEYS = {
    "maptiler_key",
    "auto_resume",
    "default_lat",
    "default_lon",
    "default_zoom",
    "gnss_port",
    "gnss_baud",
    "gnss_backend",
    "antenna_height_m",
    "rtcm_messages",
    "rinex_enabled",
    "rinex_rotate_hours",
    "rinex_data_dir",
    "local_caster_enabled",
    "local_caster_port",
    "local_caster_mountpoint",
}


class ConfigUpdate(BaseModel):
    """Partial config update -- only include keys you want to change."""
    maptiler_key: str | None = None
    auto_resume: str | None = None
    default_lat: str | None = None
    default_lon: str | None = None
    default_zoom: str | None = None
    gnss_port: str | None = None
    gnss_baud: str | None = None
    gnss_backend: str | None = None
    antenna_height_m: str | None = None
    rtcm_messages: str | None = None
    rinex_enabled: str | None = None
    rinex_rotate_hours: str | None = None
    rinex_data_dir: str | None = None
    local_caster_enabled: str | None = None
    local_caster_port: str | None = None
    local_caster_mountpoint: str | None = None


@router.get("")
async def get_full_config(_admin=Depends(require_admin)):
    """Get all config values (admin only). Never returns password hash or session secret."""
    all_config = await get_all_config()

    # Filter out secret keys
    result = {k: v for k, v in all_config.items() if k not in SECRET_KEYS}

    # Add a convenience flag indicating whether a password is set
    pw_hash = all_config.get("web_password_hash", "")
    result["password_set"] = bool(pw_hash)
    return result


@router.put("")
async def update_config(config: ConfigUpdate, _admin=Depends(require_admin)):
    """Update config values (admin only). Only accepts known keys."""
    update_data = config.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No config values provided")

    # Validate that all keys are known
    unknown_keys = set(update_data.keys()) - KNOWN_KEYS
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown config keys: {', '.join(unknown_keys)}",
        )

    for key, value in update_data.items():
        if value is not None:
            if key == "antenna_height_m":
                try:
                    if float(value) < 0:
                        raise ValueError
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="antenna_height_m must be a non-negative number",
                    ) from exc
            await set_config(key, str(value))

    return {"ok": True}


@router.get("/maptiler-key")
async def get_maptiler_key():
    """Get the MapTiler API key (no auth required -- field crew needs the map)."""
    key = await get_config("maptiler_key")
    return {"maptiler_key": key or ""}


@router.get("/public")
async def get_public_config():
    """Get publicly accessible config values (no auth required).

    Returns map defaults and MapTiler key -- everything the frontend
    needs to initialize without admin login.
    """
    all_config = await get_all_config()
    return {k: v for k, v in all_config.items() if k in PUBLIC_KEYS}
