"""
Status routes: GNSS state and satellite detail.

These endpoints read from the in-memory GNSS state object -- no database hit.
Service status is obtained via systemctl is-active calls.
"""

import time

from fastapi import APIRouter

from ..geodesy import enrich_gnss_snapshot
from ..gnss import gnss_manager, gnss_state

router = APIRouter(prefix="/api", tags=["status"])


def get_services_snapshot() -> dict:
    return {
        "gnss_connected": gnss_manager.serial_reader.is_connected,
        "rtcm_outputs": len(gnss_manager.rtcm_fanout.outputs),
        "ntrip_push": gnss_manager.rtcm_fanout.has_output("ntrip_push"),
        "local_caster": gnss_manager.rtcm_fanout.has_output("local_caster"),
        "rinex_logging": gnss_manager.rtcm_fanout.has_output("rinex"),
    }


async def build_status_payload() -> dict:
    """Build the shared status payload used by REST and WebSocket updates."""
    from .mode import get_mode_state

    gnss = await enrich_gnss_snapshot(await gnss_state.snapshot())
    mode_state = get_mode_state()

    return {
        "mode": mode_state["mode"],
        "mode_label": mode_state["mode_label"],
        "site": mode_state["site"],
        "establishing": mode_state["establishing"],
        "establish_progress": mode_state["establish_progress"],
        "gnss": gnss,
        "services": get_services_snapshot(),
        "uptime_seconds": round(time.time() - _start_time, 0),
        "session": mode_state.get("session"),
    }


@router.get("/status")
async def get_status():
    """Return current GNSS status, active mode, and service states.

    No auth required -- field crew needs this.
    """
    return await build_status_payload()


@router.get("/satellites")
async def get_satellites():
    """Return per-satellite detail (constellation, svid, cn0, used flag).

    No auth required -- field crew needs this for sky view.
    """
    return await gnss_state.satellite_snapshot()


# Track startup time for uptime calculation
_start_time = time.time()
