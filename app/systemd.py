"""
Helpers for interacting with systemd-managed services.
"""

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger("survey365.systemd")


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


async def reset_failed_service(name: str) -> None:
    """Reset the failed state of a systemd unit."""
    result = await run_command("sudo", "-n", "systemctl", "reset-failed", name, timeout=10.0)
    if result.returncode != 0:
        logger.warning("reset-failed %s: %s", name, result.stderr)


# ── Background reconciliation ────────────────────────────────────────────

_reconcile_task: asyncio.Task | None = None
_RECONCILE_INTERVAL = 20.0  # seconds


async def _reconcile_loop() -> None:
    """Periodically check real systemd unit states and correct cached flags.

    Runs every ~20 seconds. Does NOT run on the hot request path.
    """
    while True:
        try:
            await asyncio.sleep(_RECONCILE_INTERVAL)
            await reconcile_rtklib_state()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("RTKLIB state reconciliation error", exc_info=True)


async def reconcile_rtklib_state() -> None:
    """One-shot: compare cached state with real systemd state and correct drift."""
    for service, field in (
        (RTKLIB_LOCAL_CASTER_SERVICE, "local_caster"),
        (RTKLIB_OUTBOUND_SERVICE, "outbound"),
        (RTKLIB_LOG_SERVICE, "log"),
    ):
        cached = getattr(_rtklib_service_state, field)
        if cached:
            # We think this service is running — verify
            real_active = await systemctl_is_active(service)
            if not real_active:
                logger.info(
                    "Reconciliation: %s was cached as active but systemd says inactive; correcting",
                    service,
                )
                setattr(_rtklib_service_state, field, False)


def start_reconciliation() -> None:
    """Start the background reconciliation task."""
    global _reconcile_task
    if _reconcile_task is not None:
        return
    _reconcile_task = asyncio.create_task(_reconcile_loop())
    logger.info("RTKLIB service-state reconciliation started (interval=%.0fs)", _RECONCILE_INTERVAL)


async def stop_reconciliation() -> None:
    """Stop the background reconciliation task."""
    global _reconcile_task
    if _reconcile_task is not None:
        _reconcile_task.cancel()
        try:
            await _reconcile_task
        except asyncio.CancelledError:
            pass
        _reconcile_task = None
