"""
Helpers for interacting with systemd-managed services.
"""

import asyncio
from dataclasses import dataclass


SURVEY365_SERVICE = "survey365.service"
SURVEY365_UPDATE_SERVICE = "survey365-update.service"
SURVEY365_UPDATE_CHECK_TIMER = "survey365-update-check.timer"

RTKLIB_LOCAL_CASTER_SERVICE = "survey365-rtklib-local-caster.service"
RTKLIB_OUTBOUND_SERVICE = "survey365-rtklib-outbound.service"
RTKLIB_LOG_SERVICE = "survey365-rtklib-log.service"

RTKLIB_SERVICES = (
    RTKLIB_LOCAL_CASTER_SERVICE,
    RTKLIB_OUTBOUND_SERVICE,
    RTKLIB_LOG_SERVICE,
)


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class RTKLIBServiceState:
    local_caster: bool = False
    outbound: bool = False
    log: bool = False


_rtklib_service_state = RTKLIBServiceState()


async def run_command(*args: str, timeout: float = 20.0) -> CommandResult:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return CommandResult(124, "", "command timed out")
    return CommandResult(proc.returncode, stdout.decode().strip(), stderr.decode().strip())


async def systemctl_state(name: str) -> str:
    result = await run_command("systemctl", "is-active", name)
    return result.stdout or ("unknown" if result.returncode != 0 else "inactive")


async def systemctl_is_active(name: str) -> bool:
    return await systemctl_state(name) == "active"


async def sudo_systemctl(action: str, name: str, *, timeout: float = 20.0) -> CommandResult:
    return await run_command("sudo", "-n", "systemctl", action, name, timeout=timeout)


async def start_service(name: str) -> None:
    result = await sudo_systemctl("start", name)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"failed to start {name}")


async def stop_service(name: str) -> None:
    result = await sudo_systemctl("stop", name)
    if result.returncode != 0 and "not loaded" not in result.stderr.lower():
        raise RuntimeError(result.stderr or f"failed to stop {name}")


async def restart_service(name: str) -> None:
    result = await sudo_systemctl("restart", name)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"failed to restart {name}")


def set_rtklib_service_state(*, local_caster: bool | None = None, outbound: bool | None = None, log: bool | None = None) -> None:
    if local_caster is not None:
        _rtklib_service_state.local_caster = local_caster
    if outbound is not None:
        _rtklib_service_state.outbound = outbound
    if log is not None:
        _rtklib_service_state.log = log


def reset_rtklib_service_state() -> None:
    set_rtklib_service_state(local_caster=False, outbound=False, log=False)


def get_rtklib_service_state() -> RTKLIBServiceState:
    return RTKLIBServiceState(
        local_caster=_rtklib_service_state.local_caster,
        outbound=_rtklib_service_state.outbound,
        log=_rtklib_service_state.log,
    )
