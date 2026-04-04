"""
Quectel LG290P backend stub.

Placeholder for future LG290P support. The LG290P uses NMEA + proprietary
commands rather than UBX. This stub satisfies the full GNSSBackend contract
so GNSSManager can instantiate it without AttributeError, but all
configuration methods raise NotImplementedError.

Required GNSSBackend contract:
    Attributes:
        receiver_model: str
        receiver_firmware: str
    Methods:
        parse_frame(frame, state)
        configure_base_mode(serial_reader, lat, lon, height)
        configure_rover_mode(serial_reader)
        enable_rtcm_output(serial_reader, message_spec=None)
        disable_rtcm_output(serial_reader)
        enable_raw_output(serial_reader)
        disable_raw_output(serial_reader)
        enable_antenna_voltage(serial_reader)
        set_update_rate(serial_reader, hz=1)
        set_dynamic_model(serial_reader, model=2)
        poll_mon_ver(serial_reader)  -- optional identity discovery
"""

import logging

logger = logging.getLogger("survey365.gnss.quectel")


class QuectelBackend:
    """Quectel LG290P backend — stub for future implementation."""

    receiver_model: str = "LG290P"
    receiver_firmware: str = "unknown"

    async def parse_frame(self, frame: bytes, state) -> None:
        raise NotImplementedError("LG290P support coming soon")

    async def configure_base_mode(self, serial_reader, lat: float, lon: float, height: float):
        raise NotImplementedError("LG290P support coming soon")

    async def configure_rover_mode(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def enable_rtcm_output(self, serial_reader, message_spec: str | None = None):
        raise NotImplementedError("LG290P support coming soon")

    async def disable_rtcm_output(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def enable_raw_output(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def disable_raw_output(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def enable_antenna_voltage(self, serial_reader):
        raise NotImplementedError("LG290P support coming soon")

    async def set_update_rate(self, serial_reader, hz: int = 1):
        raise NotImplementedError("LG290P support coming soon")

    async def set_dynamic_model(self, serial_reader, model: int = 2):
        raise NotImplementedError("LG290P support coming soon")

    async def poll_mon_ver(self, serial_reader):
        """Identity discovery not available on Quectel backend."""
        logger.info("MON-VER polling not supported for Quectel backend")
        return None
