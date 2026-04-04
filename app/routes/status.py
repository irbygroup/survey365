"""
Status routes: GNSS state and RTKLIB-backed service detail.
"""

import time

from fastapi import APIRouter

from ..geodesy import enrich_gnss_snapshot
from ..gnss import gnss_manager, gnss_state
from ..systemd import (
    RTKLIB_LOCAL_CASTER_SERVICE,
    RTKLIB_LOG_SERVICE,
    RTKLIB_OUTBOUND_SERVICE,
    systemctl_is_active,
)

router = APIRouter(prefix="/api", tags=["status"])


async def get_services_snapshot() -> dict:
    local_caster_active = await systemctl_is_active(RTKLIB_LOCAL_CASTER_SERVICE)
    outbound_active = await systemctl_is_active(RTKLIB_OUTBOUND_SERVICE)
    log_active = await systemctl_is_active(RTKLIB_LOG_SERVICE)
    rtcm_outputs = sum((1 if local_caster_active else 0, 1 if outbound_active else 0, 1 if log_active else 0))
    return {
        "gnss_connected": gnss_manager.serial_reader.is_connected,
        "rtcm_outputs": rtcm_outputs,
        "ntrip_push": outbound_active,
        "local_caster": local_caster_active and gnss_manager.local_caster_proxy is not None,
        "rinex_logging": log_active,
        "raw_relay_clients": gnss_manager.raw_relay.client_count,
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
        "services": await get_services_snapshot(),
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
