"""
GNSS Manager: central orchestrator for serial I/O, parsing, and RTCM distribution.

Owns the serial port, instantiates the backend, routes frames, manages state.
Module-level singleton `gnss_manager` replaces the old `gnss_reader`/`gnss_state`.
"""

import asyncio
import logging
import os

from .rtcm_fanout import RTCMFanout
from .serial_reader import SerialReader
from .state import GNSSState
from .ublox import UBloxBackend

logger = logging.getLogger("survey365.gnss.manager")


class GNSSManager:
    """Central GNSS orchestrator: serial port, backend, state, RTCM fan-out."""

    def __init__(
        self,
        port: str | None = None,
        baud: int | None = None,
        backend_name: str | None = None,
    ):
        self.serial_reader = SerialReader(port=port, baud=baud)
        self.state = GNSSState()
        self.rtcm_fanout = RTCMFanout()

        backend_name = backend_name or os.environ.get("GNSS_BACKEND", "ublox")
        if backend_name == "ublox":
            self.backend = UBloxBackend()
        else:
            # Future: QuectelBackend for LG290P
            from .quectel import QuectelBackend
            self.backend = QuectelBackend()

        self._read_task: asyncio.Task | None = None
        self._running = False
        self._reconnect_delay = 2.0

    async def start(self):
        """Open serial port, configure receiver, start read loop."""
        self._running = True
        self._read_task = asyncio.create_task(self._run_loop())
        logger.info("GNSS manager started")

    async def stop(self):
        """Stop read loop, close serial port, close all outputs."""
        self._running = False
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        await self.rtcm_fanout.clear_outputs()
        await self.serial_reader.close()
        await self.state.set_connected(False)
        logger.info("GNSS manager stopped")

    async def configure_base(self, lat: float, lon: float, height: float):
        """Configure receiver as fixed-position base station."""
        await self.backend.configure_base_mode(self.serial_reader, lat, lon, height)
        await self.backend.enable_rtcm_output(self.serial_reader)

    async def configure_rover(self):
        """Configure receiver for rover mode (disable TMODE3)."""
        await self.backend.configure_rover_mode(self.serial_reader)

    async def inject_rtcm(self, data: bytes):
        """Write RTCM3 corrections into the receiver (for rover/establish mode)."""
        await self.serial_reader.write(data)

    async def _run_loop(self):
        """Main loop: connect, configure, read. Reconnect on error."""
        while self._running:
            try:
                await self._connect_and_read()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("GNSS connection error: %s. Reconnecting in %.0fs...", exc, self._reconnect_delay)
                await self.state.set_connected(False)

            if self._running:
                await asyncio.sleep(self._reconnect_delay)

    async def _connect_and_read(self):
        """Open serial port, configure initial state, read frames continuously."""
        logger.info("Opening GNSS serial port...")

        try:
            await self.serial_reader.open()
        except Exception as exc:
            logger.warning("Failed to open serial port: %s", exc)
            raise

        await self.state.set_connected(True)

        # Initial configuration: antenna voltage + dynamic model
        try:
            await self.backend.enable_antenna_voltage(self.serial_reader)
            logger.info("Antenna voltage configured on startup")
        except Exception as exc:
            logger.warning("Antenna voltage config failed (may already be set): %s", exc)

        try:
            async for frame_type, frame_data in self.serial_reader.frames():
                if frame_type == "ubx":
                    await self.backend.parse_frame(frame_data, self.state)
                elif frame_type == "rtcm3":
                    await self.rtcm_fanout.broadcast(frame_data)
                # NMEA frames ignored for now (UBX provides everything we need)
        finally:
            await self.serial_reader.close()
            await self.state.set_connected(False)


# Module-level singleton
gnss_manager = GNSSManager()
