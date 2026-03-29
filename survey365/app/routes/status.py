"""
Status routes: GNSS state and satellite detail.

These endpoints read from the in-memory GNSS state object -- no database hit.
Service status is obtained via systemctl is-active calls.
"""

import time

from fastapi import APIRouter

from ..gnss import gnss_state
from ..rtkbase import get_service_status

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status():
    """Return current GNSS status, active mode, and service states.

    No auth required -- field crew needs this.
    """
    # Import here to avoid circular import with mode state
    from .mode import get_mode_state

    gnss = await gnss_state.snapshot()
    services = await get_service_status()
    mode_state = get_mode_state()

    return {
        "mode": mode_state["mode"],
        "mode_label": mode_state["mode_label"],
        "site": mode_state["site"],
        "gnss": gnss,
        "services": services,
        "uptime_seconds": round(time.time() - _start_time, 0),
        "session": mode_state.get("session"),
    }


@router.get("/satellites")
async def get_satellites():
    """Return per-satellite detail (constellation, svid, cn0, used flag).

    No auth required -- field crew needs this for sky view.
    """
    return await gnss_state.satellite_snapshot()


# Track startup time for uptime calculation
_start_time = time.time()
