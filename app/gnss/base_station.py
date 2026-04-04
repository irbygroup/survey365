"""
Base station controller: start/stop base mode with RTCM output management.

All configuration goes directly to the receiver via UBX commands.
"""

import logging

from ..db import get_config
from .manager import GNSSManager
from .ntrip_caster import NTRIPCaster
from .ntrip_push import NTRIPPush
from .rinex_logger import RINEXLogger

logger = logging.getLogger("survey365.gnss.base_station")


async def start_base(
    manager: GNSSManager,
    lat: float,
    lon: float,
    height: float,
    outputs: list[str] | None = None,
):
    """Configure F9P as base station and start RTCM outputs.

    Args:
        manager: The GNSS manager singleton
        lat: Base station latitude (degrees)
        lon: Base station longitude (degrees)
        height: Ellipsoid height (meters)
        outputs: List of output names to enable: "rinex", "ntrip_push", "local_caster"
                 Defaults to ["rinex"] if None.
    """
    if outputs is None:
        outputs = await _resolve_outputs()

    # Configure receiver as fixed-position base
    rtcm_message_spec = await get_config("rtcm_messages")
    await manager.configure_base(
        lat,
        lon,
        height,
        rtcm_message_spec=rtcm_message_spec,
    )

    # Start requested outputs
    if "rinex" in outputs:
        data_dir = await get_config("rinex_data_dir") or "data/rinex"
        rotate_hours = int(await get_config("rinex_rotate_hours") or "24")
        rinex = RINEXLogger(data_dir=data_dir, rotate_hours=rotate_hours)
        manager.rtcm_fanout.add_output(rinex)

    if "ntrip_push" in outputs:
        profile = await _get_ntrip_profile("outbound_caster")
        if profile:
            push = NTRIPPush(
                host=profile["host"],
                port=profile["port"],
                mountpoint=profile["mountpoint"],
                password=profile["password"] or "",
            )
            await push.connect()
            manager.rtcm_fanout.add_output(push)
        else:
            logger.warning("No outbound NTRIP profile configured, skipping push")

    if "local_caster" in outputs:
        caster_port = int(await get_config("local_caster_port") or "2101")
        caster_mount = await get_config("local_caster_mountpoint") or "SURVEY365"
        caster = NTRIPCaster(
            port=caster_port,
            mountpoint=caster_mount,
            latitude=lat,
            longitude=lon,
        )
        await caster.start()
        manager.rtcm_fanout.add_output(caster)

    output_names = [o.name for o in manager.rtcm_fanout.outputs]
    logger.info(
        "Base station started: lat=%.9f lon=%.9f height=%.4f outputs=%s",
        lat, lon, height, output_names,
    )


async def stop_base(manager: GNSSManager):
    """Stop all RTCM outputs and disable RTCM generation on receiver."""
    await manager.rtcm_fanout.clear_outputs()
    manager.clear_base_reference()
    try:
        await manager.backend.disable_rtcm_output(manager.serial_reader)
    except Exception as exc:
        logger.warning("Failed to disable RTCM output on receiver: %s", exc)

    logger.info("Base station stopped")


async def _get_ntrip_profile(profile_type: str) -> dict | None:
    """Load the default NTRIP profile of the given type from the database."""
    from ..db import get_db

    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT host, port, mountpoint, username, password
            FROM ntrip_profiles
            WHERE type = ? AND is_default = 1
            ORDER BY id LIMIT 1
            """,
            (profile_type,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "host": row["host"],
            "port": row["port"],
            "mountpoint": row["mountpoint"],
            "username": row["username"],
            "password": row["password"],
        }


async def _resolve_outputs() -> list[str]:
    """Resolve enabled RTCM outputs from config and default profiles."""
    outputs: list[str] = []

    if _config_bool(await get_config("rinex_enabled"), default=True):
        outputs.append("rinex")

    if _config_bool(await get_config("local_caster_enabled"), default=False):
        outputs.append("local_caster")

    if await _get_ntrip_profile("outbound_caster"):
        outputs.append("ntrip_push")

    return outputs


def _config_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
