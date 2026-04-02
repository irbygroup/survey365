"""
RTCM3 fan-out: distribute RTCM3 bytes to registered output consumers.

Same broadcaster pattern as ws/live.py client set. Outputs implement
the RTCMOutput protocol (write/close/name).
"""

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger("survey365.gnss.rtcm_fanout")


@runtime_checkable
class RTCMOutput(Protocol):
    """Protocol for RTCM3 output consumers."""

    name: str

    async def write(self, data: bytes) -> None: ...
    async def close(self) -> None: ...


class RTCMFanout:
    """Distribute RTCM3 frames to registered outputs."""

    def __init__(self):
        self._outputs: list[RTCMOutput] = []

    @property
    def outputs(self) -> list[RTCMOutput]:
        return list(self._outputs)

    async def broadcast(self, data: bytes):
        """Send RTCM3 data to all registered outputs."""
        dead = []
        for output in self._outputs:
            try:
                await output.write(data)
            except Exception as exc:
                logger.warning("RTCM output '%s' error: %s", output.name, exc)
                dead.append(output)

        for output in dead:
            try:
                await output.close()
            except Exception:
                pass
            self._outputs.remove(output)
            logger.info("Removed failed RTCM output: %s", output.name)

    def add_output(self, output: RTCMOutput):
        self._outputs.append(output)
        logger.info("Added RTCM output: %s", output.name)

    def remove_output(self, output: RTCMOutput):
        if output in self._outputs:
            self._outputs.remove(output)
            logger.info("Removed RTCM output: %s", output.name)

    async def clear_outputs(self):
        """Close and remove all outputs."""
        for output in self._outputs:
            try:
                await output.close()
            except Exception as exc:
                logger.warning("Error closing output '%s': %s", output.name, exc)
        self._outputs.clear()
        logger.info("All RTCM outputs cleared")

    def has_output(self, name: str) -> bool:
        return any(o.name == name for o in self._outputs)
