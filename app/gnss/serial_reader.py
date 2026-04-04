"""
Async serial port reader with UBX/NMEA/RTCM3 frame detection.

Reads raw bytes from the F9P serial port in a background thread,
detects frame boundaries, and yields typed frames via an async generator.

Frame types:
  - UBX:   sync 0xB5 0x62, class, id, length(2), payload, checksum(2)
  - NMEA:  '$' to '\r\n'
  - RTCM3: preamble 0xD3, reserved(6 bits) + length(10 bits), payload, CRC24Q(3)
"""

import asyncio
import logging
import os
import struct
import threading
from collections.abc import AsyncGenerator

logger = logging.getLogger("survey365.gnss.serial")

# Defaults (overridden by env vars or config)
DEFAULT_PORT = "/dev/ttyGNSS"
DEFAULT_BAUD = 115200

# Frame detection constants
UBX_SYNC_1 = 0xB5
UBX_SYNC_2 = 0x62
RTCM3_PREAMBLE = 0xD3
MAX_UBX_FRAME = 8192
MAX_NMEA_LINE = 256
MAX_RTCM3_FRAME = 1200  # RTCM3 max message ~1023 + header + CRC

READ_CHUNK = 4096
RECONNECT_DELAY = 2.0


# CRC24Q lookup table for RTCM3
_CRC24Q_TABLE = None


def _init_crc24q_table():
    global _CRC24Q_TABLE
    if _CRC24Q_TABLE is not None:
        return
    _CRC24Q_TABLE = [0] * 256
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        _CRC24Q_TABLE[i] = crc & 0xFFFFFF


def crc24q(data: bytes) -> int:
    """Compute CRC24Q checksum for RTCM3 frames."""
    _init_crc24q_table()
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ _CRC24Q_TABLE[((crc >> 16) ^ byte) & 0xFF]
    return crc


class SerialReader:
    """Async serial port reader with frame detection.

    Uses a background thread for blocking serial I/O and bridges
    to asyncio via a queue.
    """

    def __init__(
        self,
        port: str | None = None,
        baud: int | None = None,
        raw_chunk_callback=None,
        frame_filter=None,
    ):
        self.port = port or os.environ.get("GNSS_PORT", DEFAULT_PORT)
        self.baud = baud or int(os.environ.get("GNSS_BAUD", str(DEFAULT_BAUD)))
        self.raw_chunk_callback = raw_chunk_callback
        self.frame_filter = frame_filter
        self._serial = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._queue: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._write_lock = threading.Lock()
        self.is_connected: bool = False

    async def open(self):
        """Open the serial port and start the reader thread."""
        import serial

        self._serial = serial.Serial(
            self.port,
            self.baud,
            timeout=1.0,
        )
        self._serial.reset_input_buffer()
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=500)
        self._running = True
        self.is_connected = True

        self._thread = threading.Thread(
            target=self._read_thread,
            name="gnss-serial-reader",
            daemon=True,
        )
        self._thread.start()
        logger.info("Serial port opened: %s @ %d baud", self.port, self.baud)

    async def close(self):
        """Stop the reader thread and close the serial port."""
        self._running = False
        self.is_connected = False
        # Close the port before joining so any blocking read returns promptly.
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._serial = None
        logger.info("Serial port closed")

    async def write(self, data: bytes):
        """Write data to the serial port (thread-safe)."""
        if self._serial is None or not self._serial.is_open:
            return
        with self._write_lock:
            self._serial.write(data)

    async def frames(self) -> AsyncGenerator[tuple[str, bytes], None]:
        """Async generator yielding (frame_type, frame_bytes) tuples.

        frame_type is one of: "ubx", "nmea", "rtcm3"
        """
        if self._queue is None:
            return
        while self._running:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                yield frame
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def _read_thread(self):
        """Background thread: read serial bytes and detect frames."""
        buffer = bytearray()

        while self._running:
            try:
                if self._serial is None or not self._serial.is_open:
                    break

                waiting = self._serial.in_waiting
                if waiting == 0:
                    # Small read with timeout to avoid busy-wait
                    data = self._serial.read(1)
                else:
                    data = self._serial.read(min(waiting, READ_CHUNK))

                if not data:
                    continue

                if self.raw_chunk_callback is not None:
                    try:
                        self.raw_chunk_callback(bytes(data))
                    except Exception:
                        logger.debug("Raw chunk callback failed", exc_info=True)

                buffer.extend(data)
                self._extract_frames(buffer)

            except Exception as exc:
                if self._running:
                    logger.warning("Serial read error: %s", exc)
                    self.is_connected = False
                break

    def _extract_frames(self, buffer: bytearray):
        """Extract all complete frames from the buffer, modifying it in place."""
        while len(buffer) > 0:
            # Try UBX first (most common from F9P)
            if len(buffer) >= 2 and buffer[0] == UBX_SYNC_1 and buffer[1] == UBX_SYNC_2:
                frame_len = self._try_ubx(buffer)
                if frame_len > 0:
                    frame = bytes(buffer[:frame_len])
                    if self._should_emit("ubx", frame):
                        self._emit("ubx", frame)
                    del buffer[:frame_len]
                    continue
                elif frame_len == 0:
                    break  # Need more data
                else:
                    # Invalid, skip sync bytes
                    del buffer[:2]
                    continue

            # Try RTCM3
            if buffer[0] == RTCM3_PREAMBLE:
                frame_len = self._try_rtcm3(buffer)
                if frame_len > 0:
                    frame = bytes(buffer[:frame_len])
                    if self._should_emit("rtcm3", frame):
                        self._emit("rtcm3", frame)
                    del buffer[:frame_len]
                    continue
                elif frame_len == 0:
                    break  # Need more data
                else:
                    # Invalid, skip preamble
                    del buffer[:1]
                    continue

            # Try NMEA
            if buffer[0] == ord("$") or buffer[0] == ord("!"):
                frame_len = self._try_nmea(buffer)
                if frame_len > 0:
                    frame = bytes(buffer[:frame_len])
                    if self._should_emit("nmea", frame):
                        self._emit("nmea", frame)
                    del buffer[:frame_len]
                    continue
                elif frame_len == 0:
                    break  # Need more data
                else:
                    # Not a valid NMEA start after all
                    del buffer[:1]
                    continue

            # Unknown byte — skip
            del buffer[:1]

    def _try_ubx(self, buf: bytearray) -> int:
        """Try to extract a UBX frame. Returns frame length, 0 if incomplete, -1 if invalid."""
        if len(buf) < 6:
            return 0

        payload_len = struct.unpack_from("<H", buf, 4)[0]
        frame_len = 6 + payload_len + 2

        if frame_len > MAX_UBX_FRAME:
            return -1

        if len(buf) < frame_len:
            return 0

        # Verify checksum (Fletcher-8 over class+id+length+payload)
        ck_a = 0
        ck_b = 0
        for i in range(2, frame_len - 2):
            ck_a = (ck_a + buf[i]) & 0xFF
            ck_b = (ck_b + ck_a) & 0xFF

        if ck_a != buf[frame_len - 2] or ck_b != buf[frame_len - 1]:
            return -1

        return frame_len

    def _try_rtcm3(self, buf: bytearray) -> int:
        """Try to extract an RTCM3 frame. Returns frame length, 0 if incomplete, -1 if invalid."""
        if len(buf) < 3:
            return 0

        # Byte 1 bits 7-2 are reserved (should be 0), bits 1-0 are MSB of length
        # Byte 2 is LSB of length
        length = ((buf[1] & 0x03) << 8) | buf[2]
        frame_len = 3 + length + 3  # header(3) + payload + CRC24Q(3)

        if length > 1023 or frame_len > MAX_RTCM3_FRAME:
            return -1

        if len(buf) < frame_len:
            return 0

        # Verify CRC24Q over header + payload (everything except the 3 CRC bytes)
        computed = crc24q(bytes(buf[: frame_len - 3]))
        received = (buf[frame_len - 3] << 16) | (buf[frame_len - 2] << 8) | buf[frame_len - 1]

        if computed != received:
            return -1

        return frame_len

    def _try_nmea(self, buf: bytearray) -> int:
        """Try to extract an NMEA sentence. Returns frame length, 0 if incomplete, -1 if invalid."""
        # Look for \r\n terminator
        for i in range(1, min(len(buf), MAX_NMEA_LINE)):
            if buf[i] == ord("\n") and i > 0 and buf[i - 1] == ord("\r"):
                return i + 1

        # If buffer exceeds max NMEA length without finding terminator, invalid
        if len(buf) >= MAX_NMEA_LINE:
            return -1

        return 0  # Incomplete

    def _emit(self, frame_type: str, data: bytes):
        """Push a frame to the async queue (from the reader thread)."""
        if self._loop is not None and self._queue is not None:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, (frame_type, data))
            except asyncio.QueueFull:
                pass  # Drop frame if consumer is slow

    def _should_emit(self, frame_type: str, data: bytes) -> bool:
        if self.frame_filter is None:
            return True
        try:
            return bool(self.frame_filter(frame_type, data))
        except Exception:
            logger.debug("Frame filter failed for %s", frame_type, exc_info=True)
            return True
