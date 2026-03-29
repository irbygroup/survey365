"""
RTKBase integration: read/write settings.conf and manage systemd services.

RTKBase settings.conf is a standard INI file at ~/rtkbase/settings.conf.
Path is configurable via RTKBASE_DIR environment variable.

Service control requires passwordless sudo for specific systemctl commands.
The install script configures the required sudoers entry.
"""

import asyncio
import configparser
import logging
import os
from pathlib import Path

logger = logging.getLogger("survey365.rtkbase")

# RTKBase directory (configurable for dev vs production)
RTKBASE_DIR = Path(os.environ.get("RTKBASE_DIR", os.path.expanduser("~/rtkbase")))
SETTINGS_FILE = RTKBASE_DIR / "settings.conf"

# Services managed by Survey365
BASE_SERVICES = [
    "str2str_tcp",
    "str2str_ntrip_A",
    "str2str_local_ntrip_caster",
]

NTRIP_SERVICES = [
    "str2str_ntrip_A",
    "str2str_local_ntrip_caster",
]


async def read_settings() -> dict[str, dict[str, str]]:
    """Parse RTKBase settings.conf into a dict of dicts.

    Returns a nested dict: {section: {key: value, ...}, ...}
    Uses RawConfigParser to avoid interpolation issues with RTKBase values.
    """
    path = _get_settings_path()
    if not path.exists():
        logger.warning("RTKBase settings file not found at %s", path)
        return {}

    config = configparser.RawConfigParser()
    # Preserve case of keys (RTKBase uses mixed case)
    config.optionxform = str
    config.read(str(path))

    result: dict[str, dict[str, str]] = {}
    for section in config.sections():
        result[section] = dict(config.items(section))

    return result


async def write_position(lat: float, lon: float, height: float):
    """Update the base station position in RTKBase settings.conf.

    Writes to [main] section: position= lat lon height
    The format matches what RTKBase expects: space-separated lat lon height.
    """
    path = _get_settings_path()
    if not path.exists():
        raise FileNotFoundError(f"RTKBase settings file not found at {path}")

    config = configparser.RawConfigParser()
    config.optionxform = str
    config.read(str(path))

    if not config.has_section("main"):
        config.add_section("main")

    # Format: "lat lon height" with enough decimal places for survey accuracy
    position_str = f" {lat:.9f} {lon:.9f} {height:.4f}"
    config.set("main", "position", position_str)

    # Write directly to the settings file (atomic rename fails on SD cards
    # when RTKBase has the file open)
    with open(path, "w") as f:
        config.write(f)
    logger.info("Updated RTKBase position: lat=%.9f lon=%.9f height=%.4f", lat, lon, height)


async def restart_service(service_name: str) -> bool:
    """Restart a systemd service via sudo systemctl.

    Returns True if the command succeeded, False otherwise.
    """
    return await _systemctl("restart", service_name)


async def stop_service(service_name: str) -> bool:
    """Stop a systemd service via sudo systemctl.

    Returns True if the command succeeded, False otherwise.
    """
    return await _systemctl("stop", service_name)


async def start_service(service_name: str) -> bool:
    """Start a systemd service via sudo systemctl.

    Returns True if the command succeeded, False otherwise.
    """
    return await _systemctl("start", service_name)


async def service_is_active(service_name: str) -> bool:
    """Check if a systemd service is currently active.

    Returns True if 'systemctl is-active' reports 'active'.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "is-active", service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() == "active"
    except Exception as exc:
        logger.error("Failed to check service status for %s: %s", service_name, exc)
        return False


async def start_base_services():
    """Start all base station services: str2str_tcp + NTRIP services.

    Called after writing a new position to settings.conf.
    str2str_tcp must start first as it provides the data relay.
    """
    # Start TCP relay first
    ok = await restart_service("str2str_tcp")
    if not ok:
        logger.error("Failed to start str2str_tcp")
        return False

    # Brief delay for TCP relay to initialize
    await asyncio.sleep(1.0)

    # Start NTRIP services in parallel
    results = await asyncio.gather(
        restart_service("str2str_ntrip_A"),
        restart_service("str2str_local_ntrip_caster"),
        return_exceptions=True,
    )

    all_ok = True
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("Failed to start %s: %s", NTRIP_SERVICES[i], result)
            all_ok = False
        elif not result:
            logger.error("Failed to start %s", NTRIP_SERVICES[i])
            all_ok = False

    return all_ok


async def stop_base_services():
    """Stop NTRIP broadcast services. str2str_tcp stays running (needed for GNSS status).

    Returns True if all stop commands succeeded.
    """
    results = await asyncio.gather(
        stop_service("str2str_ntrip_A"),
        stop_service("str2str_local_ntrip_caster"),
        return_exceptions=True,
    )

    all_ok = True
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("Failed to stop %s: %s", NTRIP_SERVICES[i], result)
            all_ok = False
        elif not result:
            logger.warning("Stop command for %s may have failed (service may not have been running)", NTRIP_SERVICES[i])

    return all_ok


async def get_service_status() -> dict[str, bool]:
    """Get the active/inactive status of all base station services.

    Returns dict mapping service name to boolean active status.
    """
    services = [
        "str2str_tcp",
        "str2str_ntrip_A",
        "str2str_local_ntrip_caster",
        "rtkbase_web",
    ]

    results = await asyncio.gather(
        *(service_is_active(s) for s in services),
        return_exceptions=True,
    )

    status = {}
    for i, service in enumerate(services):
        if isinstance(results[i], Exception):
            status[service] = False
        else:
            status[service] = results[i]

    return status


async def _systemctl(action: str, service_name: str) -> bool:
    """Execute a sudo systemctl command.

    Returns True if return code is 0.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", action, service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error(
                "systemctl %s %s failed (rc=%d): %s",
                action,
                service_name,
                proc.returncode,
                stderr.decode().strip(),
            )
            return False

        logger.info("systemctl %s %s succeeded", action, service_name)
        return True

    except FileNotFoundError:
        logger.error("systemctl not found (running on non-Linux system?)")
        return False
    except Exception as exc:
        logger.error("systemctl %s %s error: %s", action, service_name, exc)
        return False


def _get_settings_path() -> Path:
    """Return the path to RTKBase settings.conf, respecting env override."""
    override = os.environ.get("RTKBASE_SETTINGS")
    if override:
        return Path(override)
    return SETTINGS_FILE
