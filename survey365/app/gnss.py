"""
UBX protocol parser reading from RTKBase's str2str TCP relay on port 5015.

Architecture:
- GNSSState: thread-safe in-memory state updated by the background reader
- GNSSReader: async background task that connects to TCP:5015, parses UBX frames,
  and updates GNSSState continuously

The F9P outputs UBX-NAV-PVT (position/fix) and UBX-NAV-SAT (satellite detail)
at 1Hz through str2str_tcp. We parse those two message types and ignore everything
else (RTCM3, other UBX messages).

UBX frame structure:
  Sync: 0xB5 0x62
  Class: 1 byte
  ID: 1 byte
  Length: 2 bytes (little-endian)
  Payload: <length> bytes
  Checksum: 2 bytes (Fletcher-8 over class+id+length+payload)
"""

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field

logger = logging.getLogger("survey365.gnss")

# UBX sync bytes
UBX_SYNC_1 = 0xB5
UBX_SYNC_2 = 0x62

# UBX message class and ID constants
UBX_NAV_CLASS = 0x01
UBX_NAV_PVT_ID = 0x07
UBX_NAV_SAT_ID = 0x35

# Constellation mapping from gnssId
CONSTELLATION_MAP = {
    0: "GPS",
    1: "SBAS",
    2: "Galileo",
    3: "BeiDou",
    5: "QZSS",
    6: "GLONASS",
}

# Fix type mapping from UBX fixType field
FIX_TYPE_MAP = {
    0: "No Fix",
    1: "Dead Reckoning",
    2: "2D",
    3: "3D",
    4: "GNSS+DR",
    5: "Time Only",
}

# TCP connection settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5015
RECONNECT_DELAY = 2.0  # seconds between reconnection attempts
READ_BUFFER_SIZE = 4096
MAX_FRAME_SIZE = 8192  # maximum expected UBX frame size


@dataclass
class SatelliteInfo:
    """Per-satellite detail from UBX-NAV-SAT."""
    constellation: str = ""
    svid: int = 0
    elevation: int = 0
    azimuth: int = 0
    cn0: float = 0.0
    used: bool = False


@dataclass
class GNSSState:
    """Thread-safe in-memory GNSS state. Updated by the reader, read by routes/WS."""
    fix_type: str = "No Fix"
    fix_type_raw: int = 0
    satellites_used: int = 0
    satellites_visible: int = 0
    latitude: float = 0.0
    longitude: float = 0.0
    height: float = 0.0
    accuracy_h: float = 0.0
    accuracy_v: float = 0.0
    pdop: float = 0.0
    ground_speed: float = 0.0
    heading: float = 0.0
    utc_year: int = 0
    utc_month: int = 0
    utc_day: int = 0
    utc_hour: int = 0
    utc_minute: int = 0
    utc_second: int = 0
    satellite_details: list[dict] = field(default_factory=list)
    last_pvt_update: float = 0.0
    last_sat_update: float = 0.0
    connected: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def update_pvt(
        self,
        fix_type_raw: int,
        num_sv: int,
        lat: float,
        lon: float,
        height: float,
        h_acc: float,
        v_acc: float,
        pdop: float,
        ground_speed: float,
        heading: float,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
    ):
        """Update state from a parsed UBX-NAV-PVT message."""
        async with self._lock:
            self.fix_type_raw = fix_type_raw
            self.fix_type = FIX_TYPE_MAP.get(fix_type_raw, "Unknown")
            self.satellites_used = num_sv
            self.latitude = lat
            self.longitude = lon
            self.height = height
            self.accuracy_h = h_acc
            self.accuracy_v = v_acc
            self.pdop = pdop
            self.ground_speed = ground_speed
            self.heading = heading
            self.utc_year = year
            self.utc_month = month
            self.utc_day = day
            self.utc_hour = hour
            self.utc_minute = minute
            self.utc_second = second
            self.last_pvt_update = time.time()

    async def update_satellites(self, satellites: list[dict], num_visible: int):
        """Update state from a parsed UBX-NAV-SAT message."""
        async with self._lock:
            self.satellite_details = satellites
            self.satellites_visible = num_visible
            self.last_sat_update = time.time()

    async def set_connected(self, connected: bool):
        async with self._lock:
            self.connected = connected

    async def snapshot(self) -> dict:
        """Return a copy of the current state as a dict (safe to serialize)."""
        async with self._lock:
            return {
                "fix_type": self.fix_type,
                "satellites_used": self.satellites_used,
                "satellites_visible": self.satellites_visible,
                "latitude": self.latitude,
                "longitude": self.longitude,
                "height": self.height,
                "accuracy_h": self.accuracy_h,
                "accuracy_v": self.accuracy_v,
                "pdop": self.pdop,
                "ground_speed": self.ground_speed,
                "heading": self.heading,
                "last_pvt_update": self.last_pvt_update,
                "connected": self.connected,
                "age": round(time.time() - self.last_pvt_update, 1)
                if self.last_pvt_update > 0
                else None,
            }

    async def satellite_snapshot(self) -> dict:
        """Return satellite detail as a dict."""
        async with self._lock:
            # Build per-constellation summary
            summary: dict[str, dict[str, int]] = {}
            for sat in self.satellite_details:
                constellation = sat.get("constellation", "Unknown")
                if constellation not in summary:
                    summary[constellation] = {"used": 0, "visible": 0}
                summary[constellation]["visible"] += 1
                if sat.get("used", False):
                    summary[constellation]["used"] += 1

            return {
                "satellites": list(self.satellite_details),
                "summary": summary,
            }

    async def get_position(self) -> tuple[float, float, float] | None:
        """Return (lat, lon, height) if we have a valid fix, else None."""
        async with self._lock:
            if self.fix_type_raw >= 2 and self.last_pvt_update > 0:
                return (self.latitude, self.longitude, self.height)
            return None


def _compute_ubx_checksum(data: bytes) -> tuple[int, int]:
    """Compute UBX Fletcher-8 checksum over class+id+length+payload bytes."""
    ck_a = 0
    ck_b = 0
    for byte in data:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return ck_a, ck_b


def parse_nav_pvt(payload: bytes) -> dict | None:
    """Parse UBX-NAV-PVT payload (92 bytes).

    Returns dict with extracted fields, or None if payload is too short.
    """
    if len(payload) < 92:
        return None

    # Extract fields per u-blox protocol spec
    year = struct.unpack_from("<H", payload, 4)[0]
    month = payload[6]
    day = payload[7]
    hour = payload[8]
    minute = payload[9]
    second = payload[10]
    fix_type = payload[20]
    num_sv = payload[23]
    lon_raw = struct.unpack_from("<i", payload, 24)[0]  # 1e-7 degrees
    lat_raw = struct.unpack_from("<i", payload, 28)[0]  # 1e-7 degrees
    height_raw = struct.unpack_from("<i", payload, 32)[0]  # mm above ellipsoid
    h_acc_raw = struct.unpack_from("<I", payload, 40)[0]  # mm
    v_acc_raw = struct.unpack_from("<I", payload, 44)[0]  # mm
    ground_speed_raw = struct.unpack_from("<i", payload, 60)[0]  # mm/s
    heading_raw = struct.unpack_from("<i", payload, 64)[0]  # 1e-5 degrees
    pdop_raw = struct.unpack_from("<H", payload, 76)[0]  # 0.01 scale

    return {
        "fix_type": fix_type,
        "num_sv": num_sv,
        "lat": lat_raw * 1e-7,
        "lon": lon_raw * 1e-7,
        "height": height_raw / 1000.0,
        "h_acc": h_acc_raw / 1000.0,
        "v_acc": v_acc_raw / 1000.0,
        "pdop": pdop_raw * 0.01,
        "ground_speed": ground_speed_raw / 1000.0,
        "heading": heading_raw * 1e-5,
        "year": year,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": minute,
        "second": second,
    }


def parse_nav_sat(payload: bytes) -> list[dict]:
    """Parse UBX-NAV-SAT payload (variable length).

    Returns list of satellite info dicts.
    """
    if len(payload) < 8:
        return []

    # Header: iTOW(4) + version(1) + numSvs(1) + reserved(2)
    num_svs = payload[5]
    satellites = []

    offset = 8  # Start of per-SV blocks (12 bytes each)
    for _ in range(num_svs):
        if offset + 12 > len(payload):
            break

        gnss_id = payload[offset]
        sv_id = payload[offset + 1]
        cno = payload[offset + 2]
        elev = struct.unpack_from("<b", payload, offset + 3)[0]
        azim = struct.unpack_from("<h", payload, offset + 4)[0]
        flags = struct.unpack_from("<I", payload, offset + 8)[0]

        # Bit 3 of flags: svUsed (used in navigation solution)
        used = bool(flags & 0x08)

        satellites.append({
            "constellation": CONSTELLATION_MAP.get(gnss_id, f"Unknown({gnss_id})"),
            "svid": sv_id,
            "elevation": elev,
            "azimuth": azim,
            "cn0": float(cno),
            "used": used,
        })

        offset += 12

    return satellites


class GNSSReader:
    """Async background task that connects to str2str TCP:5015 and parses UBX frames.

    Continuously reads from the TCP stream, extracts UBX-NAV-PVT and UBX-NAV-SAT
    messages, and updates the shared GNSSState object.

    Handles reconnection if the TCP stream drops (e.g., during service restarts).
    """

    def __init__(
        self,
        state: GNSSState,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ):
        self.state = state
        self.host = host
        self.port = port
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self):
        """Start the background reader task."""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("GNSS reader started (target %s:%d)", self.host, self.port)

    async def stop(self):
        """Stop the background reader task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.state.set_connected(False)
        logger.info("GNSS reader stopped")

    async def _run_loop(self):
        """Main loop: connect, read, parse. Reconnect on error."""
        while self._running:
            try:
                await self._connect_and_read()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("GNSS reader error: %s. Reconnecting in %.0fs...", exc, RECONNECT_DELAY)
                await self.state.set_connected(False)

            if self._running:
                await asyncio.sleep(RECONNECT_DELAY)

    async def _connect_and_read(self):
        """Connect to TCP stream and continuously parse UBX frames."""
        logger.info("Connecting to GNSS TCP stream at %s:%d...", self.host, self.port)
        reader, writer = await asyncio.open_connection(self.host, self.port)
        await self.state.set_connected(True)
        logger.info("Connected to GNSS TCP stream")

        buffer = bytearray()

        try:
            while self._running:
                data = await asyncio.wait_for(
                    reader.read(READ_BUFFER_SIZE),
                    timeout=10.0,
                )
                if not data:
                    logger.warning("GNSS TCP stream closed by remote")
                    break

                buffer.extend(data)

                # Parse all complete UBX frames in the buffer
                while len(buffer) >= 8:  # Minimum UBX frame: sync(2)+class(1)+id(1)+len(2)+ck(2) = 8
                    # Scan for UBX sync bytes
                    sync_pos = -1
                    for i in range(len(buffer) - 1):
                        if buffer[i] == UBX_SYNC_1 and buffer[i + 1] == UBX_SYNC_2:
                            sync_pos = i
                            break

                    if sync_pos == -1:
                        # No sync found, keep only the last byte (might be partial sync)
                        buffer = buffer[-1:]
                        break

                    # Discard bytes before sync
                    if sync_pos > 0:
                        buffer = buffer[sync_pos:]

                    # Need at least header to read length
                    if len(buffer) < 6:
                        break

                    # Extract payload length (bytes 4-5, little-endian)
                    payload_len = struct.unpack_from("<H", buffer, 4)[0]
                    frame_len = 6 + payload_len + 2  # header(6) + payload + checksum(2)

                    if frame_len > MAX_FRAME_SIZE:
                        # Corrupt length, skip this sync and search again
                        buffer = buffer[2:]
                        continue

                    if len(buffer) < frame_len:
                        break  # Wait for more data

                    # Extract the full frame
                    frame = bytes(buffer[:frame_len])
                    buffer = buffer[frame_len:]

                    # Validate checksum (over class+id+length+payload, bytes 2 through -2)
                    ck_a, ck_b = _compute_ubx_checksum(frame[2:-2])
                    if ck_a != frame[-2] or ck_b != frame[-1]:
                        logger.debug("UBX checksum mismatch, skipping frame")
                        continue

                    # Dispatch by class and ID
                    msg_class = frame[2]
                    msg_id = frame[3]
                    payload = frame[6:6 + payload_len]

                    await self._handle_message(msg_class, msg_id, payload)

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            await self.state.set_connected(False)

    async def _handle_message(self, msg_class: int, msg_id: int, payload: bytes):
        """Route a parsed UBX message to the appropriate handler."""
        if msg_class != UBX_NAV_CLASS:
            return  # Only interested in NAV messages

        if msg_id == UBX_NAV_PVT_ID:
            result = parse_nav_pvt(payload)
            if result is not None:
                await self.state.update_pvt(
                    fix_type_raw=result["fix_type"],
                    num_sv=result["num_sv"],
                    lat=result["lat"],
                    lon=result["lon"],
                    height=result["height"],
                    h_acc=result["h_acc"],
                    v_acc=result["v_acc"],
                    pdop=result["pdop"],
                    ground_speed=result["ground_speed"],
                    heading=result["heading"],
                    year=result["year"],
                    month=result["month"],
                    day=result["day"],
                    hour=result["hour"],
                    minute=result["minute"],
                    second=result["second"],
                )

        elif msg_id == UBX_NAV_SAT_ID:
            satellites = parse_nav_sat(payload)
            await self.state.update_satellites(satellites, len(satellites))


# Module-level shared state and reader instances
gnss_state = GNSSState()
gnss_reader = GNSSReader(gnss_state)


def get_status() -> GNSSState:
    """Return the shared GNSS state object."""
    return gnss_state


def get_reader() -> GNSSReader:
    """Return the shared GNSS reader instance."""
    return gnss_reader
