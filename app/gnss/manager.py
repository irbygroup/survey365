"""
GNSS Manager: central orchestrator for serial I/O, parsing, and RTCM distribution.

Owns the serial port, instantiates the backend, routes frames, manages state.
Module-level singleton `gnss_manager` replaces the old `gnss_reader`/`gnss_state`.
"""

import asyncio
import logging
import os
import time

from .raw_relay import RawRelay
from .rtcm_fanout import RTCMFanout
from .rtcm import build_rtcm_1006, parse_rtcm_message_type
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
        self.serial_reader = SerialReader(
            port=port,
            baud=baud,
            raw_chunk_callback=self._handle_raw_chunk,
        )
        self.state = GNSSState()
        self.rtcm_fanout = RTCMFanout()
        self.raw_relay = RawRelay()
        self.local_caster_proxy = None

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
        self._rtcm_engine = "native"
        self._synthetic_reference_frame: bytes | None = None
        self._last_reference_injected_at = 0.0
        self._last_native_reference_seen_at = 0.0

    async def start(self):
        """Open serial port, configure receiver, start read loop."""
        await self._load_runtime_config()
        await self.raw_relay.start()
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

        if self.local_caster_proxy is not None:
            try:
                await self.local_caster_proxy.close()
            except Exception:
                logger.exception("Failed stopping local caster proxy")
            self.local_caster_proxy = None

        await self.rtcm_fanout.clear_outputs()
        await self.raw_relay.stop()
        await self.serial_reader.close()
        await self.state.set_connected(False)
        logger.info("GNSS manager stopped")

    async def configure_base(
        self,
        lat: float,
        lon: float,
        height: float,
        rtcm_message_spec: str | None = None,
    ):
        """Configure receiver as fixed-position base station."""
        await self.backend.configure_base_mode(self.serial_reader, lat, lon, height)
        if self._rtcm_engine == "rtklib":
            await self.backend.disable_rtcm_output(self.serial_reader)
            await self.backend.enable_raw_output(self.serial_reader)
            self.clear_base_reference()
        else:
            await self.backend.enable_rtcm_output(
                self.serial_reader,
                message_spec=rtcm_message_spec,
            )
            self._synthetic_reference_frame = build_rtcm_1006(lat, lon, height)
            self._last_reference_injected_at = 0.0
            self._last_native_reference_seen_at = 0.0
            logger.info("Synthetic RTCM 1006 reference frame prepared for base mode")

    async def configure_rover(self):
        """Configure receiver for rover mode (disable TMODE3)."""
        await self.backend.configure_rover_mode(self.serial_reader)
        if self._rtcm_engine == "rtklib":
            await self.backend.disable_raw_output(self.serial_reader)
        self.clear_base_reference()

    async def inject_rtcm(self, data: bytes):
        """Write RTCM3 corrections into the receiver (for rover/establish mode)."""
        await self.serial_reader.write(data)

    def clear_base_reference(self):
        """Clear synthetic base reference framing state."""
        self._synthetic_reference_frame = None
        self._last_reference_injected_at = 0.0
        self._last_native_reference_seen_at = 0.0

    async def generate_gga(self) -> str | None:
        """Generate an NMEA GGA sentence from current position for VRS feedback."""
        async with self.state._lock:
            if self.state.fix_type_raw < 2 or self.state.last_pvt_update <= 0:
                return None

            lat = self.state.latitude
            lon = self.state.longitude
            height = self.state.height
            sats = self.state.satellites_used
            h_acc = self.state.accuracy_h
            hour = self.state.utc_hour
            minute = self.state.utc_minute
            second = self.state.utc_second

        # Convert decimal degrees to NMEA ddmm.mmmm format
        lat_abs = abs(lat)
        lat_deg = int(lat_abs)
        lat_min = (lat_abs - lat_deg) * 60
        lat_ns = "N" if lat >= 0 else "S"

        lon_abs = abs(lon)
        lon_deg = int(lon_abs)
        lon_min = (lon_abs - lon_deg) * 60
        lon_ew = "E" if lon >= 0 else "W"

        # Quality: 1=GPS, 4=RTK Fixed, 5=RTK Float
        quality = 1
        if h_acc > 0 and h_acc < 0.05:
            quality = 4
        elif h_acc > 0 and h_acc < 0.5:
            quality = 5

        hdop = 1.0  # approximation

        # Build GGA sentence (without checksum)
        body = (
            f"GPGGA,{hour:02d}{minute:02d}{second:02d}.00,"
            f"{lat_deg:02d}{lat_min:07.4f},{lat_ns},"
            f"{lon_deg:03d}{lon_min:07.4f},{lon_ew},"
            f"{quality},{sats:02d},{hdop:.1f},"
            f"{height:.3f},M,0.000,M,,"
        )

        # Compute NMEA checksum (XOR of all chars between $ and *)
        checksum = 0
        for c in body:
            checksum ^= ord(c)

        return f"${body}*{checksum:02X}"

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
                    if self._rtcm_engine == "native":
                        now = time.monotonic()
                        msg_type = parse_rtcm_message_type(frame_data)
                        if msg_type in {1005, 1006}:
                            self._last_native_reference_seen_at = now
                        elif (
                            self._synthetic_reference_frame is not None
                            and now - self._last_native_reference_seen_at > 2.0
                            and now - self._last_reference_injected_at >= 1.0
                        ):
                            await self.rtcm_fanout.broadcast(self._synthetic_reference_frame)
                            self._last_reference_injected_at = now
                        await self.rtcm_fanout.broadcast(frame_data)
                # NMEA frames ignored for now (UBX provides everything we need)
        finally:
            await self.serial_reader.close()
            await self.state.set_connected(False)

    async def _load_runtime_config(self):
        """Load GNSS port/baud/backend from config DB before opening serial."""
        from ..db import get_config

        port = await get_config("gnss_port")
        baud = await get_config("gnss_baud")
        backend_name = await get_config("gnss_backend")
        rtcm_engine = await get_config("rtcm_engine")

        self.serial_reader.port = (
            (port or "").strip()
            or os.environ.get("GNSS_PORT")
            or self.serial_reader.port
        )

        if baud:
            try:
                self.serial_reader.baud = int(baud)
            except ValueError:
                logger.warning("Invalid gnss_baud config value: %r", baud)

        selected_backend = (
            (backend_name or "").strip().lower()
            or os.environ.get("GNSS_BACKEND", "ublox").strip().lower()
        )

        if selected_backend == "ublox" and not isinstance(self.backend, UBloxBackend):
            self.backend = UBloxBackend()
        elif selected_backend != "ublox" and self.backend.__class__.__name__ != "QuectelBackend":
            from .quectel import QuectelBackend

            self.backend = QuectelBackend()

        self._rtcm_engine = (rtcm_engine or "native").strip().lower()
        if self._rtcm_engine not in {"native", "rtklib"}:
            self._rtcm_engine = "native"

        logger.info(
            "GNSS runtime config loaded: port=%s baud=%s backend=%s rtcm_engine=%s",
            self.serial_reader.port,
            self.serial_reader.baud,
            selected_backend,
            self._rtcm_engine,
        )

    def receiver_descriptor(self) -> str:
        return f"RTKBase {self.backend.receiver_model},Survey365 {self.backend.receiver_firmware}"

    def _handle_raw_chunk(self, data: bytes) -> None:
        loop = self.serial_reader._loop
        if not data or loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(self.raw_relay.publish_nowait, data)


# Module-level singleton
gnss_manager = GNSSManager()
