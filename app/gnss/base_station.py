"""
Base station controller: start/stop base mode with RTKLIB-managed outputs.
"""

import logging
from pathlib import Path

from ..db import get_config
from ..rtklib.runtime import clear_active_base_config, write_active_base_config
from ..runtime_paths import get_data_dir
from ..systemd import (
    RTKLIB_LOCAL_CASTER_SERVICE,
    RTKLIB_LOG_SERVICE,
    RTKLIB_OUTBOUND_SERVICE,
    reset_rtklib_service_state,
    set_rtklib_service_state,
    start_service,
    stop_service,
)
from .manager import GNSSManager
from .ntrip_caster import NTRIPCaster
from .ntrip_push import NTRIPPush
from .rinex_logger import RINEXLogger

logger = logging.getLogger("survey365.gnss.base_station")

RTKBASE_MESSAGE_DEFAULT = (
    "1004,1005(10),1006,1008(10),1012,1019,1020,1033(10),"
    "1042,1045,1046,1077,1087,1097,1107,1127,1230"
)
RAW_RELAY_PORT = 5015
LOCAL_CASTER_INTERNAL_PORT = 2110


async def start_base(
    manager: GNSSManager,
    lat: float,
    lon: float,
    height: float,
    outputs: list[str] | None = None,
):
    """Configure the receiver as a base and start the enabled output stack."""
    if outputs is None:
        outputs = await _resolve_outputs()

    rtcm_engine = (await get_config("rtcm_engine") or "native").strip().lower()
    if rtcm_engine == "rtklib":
        await _start_base_rtklib(manager, lat, lon, height, outputs)
        manager._active_output_engine = "rtklib"
        return

    await _start_base_native(manager, lat, lon, height, outputs)
    manager._active_output_engine = "native"


async def stop_base(manager: GNSSManager):
    """Stop the active base output stack and return receiver to rover mode.

    Uses the engine that was *actually started* (manager._active_output_engine)
    rather than the current DB config, so a config change while running cannot
    break teardown.
    """
    active_engine = manager._active_output_engine or (
        (await get_config("rtcm_engine") or "native").strip().lower()
    )

    if active_engine == "rtklib":
        if manager.local_caster_proxy is not None:
            await manager.local_caster_proxy.close()
            manager.local_caster_proxy = None
        for service in (RTKLIB_LOCAL_CASTER_SERVICE, RTKLIB_OUTBOUND_SERVICE, RTKLIB_LOG_SERVICE):
            try:
                await stop_service(service)
            except Exception as exc:
                logger.warning("Failed stopping %s: %s", service, exc)
        clear_active_base_config()
        reset_rtklib_service_state()
    else:
        # Native mode: clear fanout (closes caster, push, logger)
        await manager.rtcm_fanout.clear_outputs()
        if manager.local_caster_proxy is not None:
            # Already closed by clear_outputs if it was in the fanout,
            # but clear the reference regardless.
            manager.local_caster_proxy = None
        # Explicitly disable native RTCM output on the receiver
        try:
            await manager.backend.disable_rtcm_output(manager.serial_reader)
        except Exception as exc:
            logger.warning("Failed disabling native RTCM output: %s", exc)

    manager.clear_base_reference()
    manager._active_output_engine = None
    try:
        await manager.configure_rover()
    except Exception as exc:
        logger.warning("Failed returning receiver to rover mode: %s", exc)

    logger.info("Base station stopped")


async def _start_base_rtklib(
    manager: GNSSManager,
    lat: float,
    lon: float,
    height: float,
    outputs: list[str],
) -> None:
    reset_rtklib_service_state()
    rtklib_local_messages = await get_config("rtklib_local_messages") or RTKBASE_MESSAGE_DEFAULT
    rtklib_outbound_messages = await get_config("rtklib_outbound_messages") or RTKBASE_MESSAGE_DEFAULT
    local_enabled = "local_caster" in outputs
    outbound_profile = None
    if "ntrip_push" in outputs:
        outbound_profile = await _get_ntrip_profile("outbound_caster")
        if outbound_profile is not None:
            # Validate required outbound fields
            missing = []
            if not (outbound_profile.get("host") or "").strip():
                missing.append("host")
            if not (outbound_profile.get("mountpoint") or "").strip():
                missing.append("mountpoint")
            if missing:
                logger.warning(
                    "Outbound NTRIP profile incomplete (missing %s); skipping outbound",
                    ", ".join(missing),
                )
                outbound_profile = None
    log_enabled = "rinex" in outputs
    local_caster_port = int(await get_config("local_caster_port") or "2101")
    local_caster_mountpoint = await get_config("local_caster_mountpoint") or "SURVEY365"
    rinex_rotate_hours = int(await get_config("rinex_rotate_hours") or "24")
    rinex_data_dir = await get_config("rinex_data_dir") or str(get_data_dir() / "rinex")
    antenna_descriptor = await get_config("antenna_descriptor") or "ADVNULLANTENNA"

    await manager.configure_base(lat, lon, height, rtcm_message_spec=None)

    from ..version import __version__ as survey365_version

    runtime = {
        "survey365_version": survey365_version,
        "active_mode": "base",
        "rtcm_engine": "rtklib",
        "raw_relay_port": RAW_RELAY_PORT,
        "external_local_caster_port": local_caster_port if local_enabled else None,
        "trace_level": 0,
        "position": {
            "lat": lat,
            "lon": lon,
            "height": height,
        },
        "receiver_descriptor": manager.receiver_descriptor(),
        "antenna_descriptor": antenna_descriptor,
        "outputs": {
            "local_caster": {
                "enabled": local_enabled,
                "mountpoint": local_caster_mountpoint,
                "messages": rtklib_local_messages,
                "internal_port": LOCAL_CASTER_INTERNAL_PORT,
                "receiver_frequency_count": "2",
                "receiver_label": manager.receiver_descriptor().replace(" ", "_"),
                "username": "",
                "password": "",
            },
            "outbound": {
                "enabled": outbound_profile is not None,
                "host": outbound_profile["host"] if outbound_profile else "",
                "port": outbound_profile["port"] if outbound_profile else 2101,
                "mountpoint": outbound_profile["mountpoint"] if outbound_profile else "",
                "password": outbound_profile["password"] if outbound_profile else "",
                "messages": rtklib_outbound_messages,
            },
            "log": {
                "enabled": log_enabled,
                "data_dir": str(_resolve_data_dir(rinex_data_dir)),
                "rotate_hours": rinex_rotate_hours,
            },
        },
    }
    write_active_base_config(runtime)

    started_services: list[str] = []
    try:
        if log_enabled:
            await start_service(RTKLIB_LOG_SERVICE)
            started_services.append(RTKLIB_LOG_SERVICE)
            set_rtklib_service_state(log=True)

        if outbound_profile is not None:
            await start_service(RTKLIB_OUTBOUND_SERVICE)
            started_services.append(RTKLIB_OUTBOUND_SERVICE)
            set_rtklib_service_state(outbound=True)

        if local_enabled:
            await start_service(RTKLIB_LOCAL_CASTER_SERVICE)
            started_services.append(RTKLIB_LOCAL_CASTER_SERVICE)
            proxy = NTRIPCaster(
                port=local_caster_port,
                mountpoint=local_caster_mountpoint,
                upstream_port=LOCAL_CASTER_INTERNAL_PORT,
            )
            await proxy.start()
            manager.local_caster_proxy = proxy
            set_rtklib_service_state(local_caster=True)
    except Exception:
        if manager.local_caster_proxy is not None:
            await manager.local_caster_proxy.close()
            manager.local_caster_proxy = None
        for service in reversed(started_services):
            try:
                await stop_service(service)
            except Exception:
                logger.exception("Failed rolling back %s", service)
        clear_active_base_config()
        reset_rtklib_service_state()
        raise

    logger.info(
        "Base station started with RTKLIB outputs: local=%s outbound=%s log=%s",
        local_enabled,
        outbound_profile is not None,
        log_enabled,
    )


async def _start_base_native(
    manager: GNSSManager,
    lat: float,
    lon: float,
    height: float,
    outputs: list[str],
) -> None:
    reset_rtklib_service_state()
    rtcm_message_spec = await get_config("rtcm_messages")
    await manager.configure_base(lat, lon, height, rtcm_message_spec=rtcm_message_spec)

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
            upstream_port=None,  # native direct-broadcast mode
        )
        await caster.start()
        manager.rtcm_fanout.add_output(caster)  # caster receives RTCM via fanout
        manager.local_caster_proxy = caster

    output_names = [o.name for o in manager.rtcm_fanout.outputs]
    logger.info(
        "Base station started in native mode: lat=%.9f lon=%.9f height=%.4f outputs=%s",
        lat,
        lon,
        height,
        output_names,
    )


async def _get_ntrip_profile(profile_type: str) -> dict | None:
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


def _resolve_data_dir(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = get_data_dir() / path
    return path
