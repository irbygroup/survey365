"""
Quectel LG290P backend stub.

Placeholder for future LG290P support. The LG290P uses NMEA + proprietary
commands rather than UBX. This stub matches the UBloxBackend interface
so GNSSManager can instantiate it, but all methods raise NotImplementedError.
"""

import logging

logger = logging.getLogger("survey365.gnss.quectel")


class QuectelBackend:
    """Quectel LG290P backend — stub for future implementation."""

    async def parse_frame(self, frame: bytes, state) -> None:
        raise NotImplementedError("LG290P support coming soon")

    async def configure_base_mode(self, serial_reader, lat: float, lon: float, height: float):
        raise NotImplementedError("LG290P support coming soon")

    async def configure_rover_mode(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def enable_rtcm_output(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def disable_rtcm_output(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def enable_antenna_voltage(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def set_update_rate(self, serial_reader, hz: int = 1):
        raise NotImplementedError("LG290P support coming soon")

    async def set_dynamic_model(self, serial_reader, model: int = 2):
        raise NotImplementedError("LG290P support coming soon")
