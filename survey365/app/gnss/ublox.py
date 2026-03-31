"""
u-blox ZED-F9P backend: UBX frame parsing and configuration commands.

Parsing: uses manual struct.unpack for NAV-PVT and NAV-SAT (proven from gnss.py).
Configuration: builds UBX-CFG-VALSET messages for TMODE3, RTCM output, antenna, etc.
"""

import asyncio
import logging
import struct

logger = logging.getLogger("survey365.gnss.ublox")

# UBX message class and ID constants
UBX_NAV_CLASS = 0x01
UBX_NAV_PVT_ID = 0x07
UBX_NAV_SAT_ID = 0x35
UBX_ACK_CLASS = 0x05
UBX_ACK_ACK_ID = 0x01
UBX_ACK_NAK_ID = 0x00
UBX_CFG_CLASS = 0x06
UBX_CFG_VALSET_ID = 0x8A

# CFG-VALSET layer masks
LAYER_RAM = 0x01
LAYER_BBR = 0x02
LAYER_FLASH = 0x04
LAYER_ALL = LAYER_RAM | LAYER_BBR | LAYER_FLASH

# UBX-CFG-VALSET key IDs
CFG_TMODE_MODE = 0x20030001
CFG_TMODE_POS_TYPE = 0x20030002
CFG_TMODE_LAT = 0x40030009
CFG_TMODE_LAT_HP = 0x2003000A
CFG_TMODE_LON = 0x4003000B
CFG_TMODE_LON_HP = 0x2003000C
CFG_TMODE_HEIGHT = 0x4003000D
CFG_TMODE_HEIGHT_HP = 0x2003000E

CFG_MSGOUT_RTCM_1005_USB = 0x209102C0
CFG_MSGOUT_RTCM_1077_USB = 0x209102CF
CFG_MSGOUT_RTCM_1087_USB = 0x209102D4
CFG_MSGOUT_RTCM_1097_USB = 0x2091031B
CFG_MSGOUT_RTCM_1127_USB = 0x209102D9
CFG_MSGOUT_RTCM_1230_USB = 0x20910306

CFG_USBOUTPROT_RTCM3X = 0x10780004

CFG_RATE_MEAS = 0x30210001
CFG_HW_ANT_CFG_VOLTCTRL = 0x10A3002E
CFG_HW_ANT_CFG_SHORTDET = 0x10A3002F
CFG_HW_ANT_CFG_OPENDET = 0x10A30030

CFG_NAVSPG_DYNMODEL = 0x20110021

# Dynamic models
DYNMODEL_STATIONARY = 2
DYNMODEL_PEDESTRIAN = 3
DYNMODEL_AUTOMOTIVE = 4

# Constellation mapping (same as in state.py, needed here for parsing)
CONSTELLATION_MAP = {
    0: "GPS",
    1: "SBAS",
    2: "Galileo",
    3: "BeiDou",
    5: "QZSS",
    6: "GLONASS",
}

# Default RTCM message rates: {key_id: rate}
# Rate=1 means every measurement epoch; rate=10 means every 10th epoch
DEFAULT_RTCM_RATES = {
    CFG_MSGOUT_RTCM_1005_USB: 10,   # Station coordinates, every 10s
    CFG_MSGOUT_RTCM_1077_USB: 1,    # GPS MSM7, every epoch
    CFG_MSGOUT_RTCM_1087_USB: 1,    # GLONASS MSM7
    CFG_MSGOUT_RTCM_1097_USB: 1,    # Galileo MSM7
    CFG_MSGOUT_RTCM_1127_USB: 1,    # BeiDou MSM7
    CFG_MSGOUT_RTCM_1230_USB: 10,   # GLONASS biases, every 10s
}

RTCM_MESSAGE_KEYS = {
    1005: CFG_MSGOUT_RTCM_1005_USB,
    1077: CFG_MSGOUT_RTCM_1077_USB,
    1087: CFG_MSGOUT_RTCM_1087_USB,
    1097: CFG_MSGOUT_RTCM_1097_USB,
    1127: CFG_MSGOUT_RTCM_1127_USB,
    1230: CFG_MSGOUT_RTCM_1230_USB,
}


def _ubx_checksum(data: bytes) -> bytes:
    """Compute UBX Fletcher-8 checksum over class+id+length+payload."""
    ck_a = 0
    ck_b = 0
    for b in data:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes([ck_a, ck_b])


def _ubx_message(cls: int, msg_id: int, payload: bytes = b"") -> bytes:
    """Build a complete UBX message with sync, header, payload, checksum."""
    header = struct.pack("<BBH", cls, msg_id, len(payload))
    body = header + payload
    return b"\xb5\x62" + body + _ubx_checksum(body)


def _build_valset(keys_values: list[tuple[int, int, str]], layers: int = LAYER_ALL) -> bytes:
    """Build UBX-CFG-VALSET payload.

    keys_values: list of (key_id, value, size_type) where size_type is
                 'U1', 'I1', 'U2', 'I4', 'U4'
    """
    # VALSET header: version=0, layers, reserved=0x0000
    payload = struct.pack("<BBH", 0, layers, 0)

    size_map = {
        "U1": ("<BI", 1, False),
        "I1": ("<Bb", 1, True),
        "U2": ("<BH", 2, False),
        "I4": ("<Bi", 4, True),
        "U4": ("<BI", 4, False),
    }

    for key_id, value, size_type in keys_values:
        fmt_key = "<I"
        if size_type == "U1":
            payload += struct.pack(fmt_key, key_id) + struct.pack("<B", value & 0xFF)
        elif size_type == "I1":
            payload += struct.pack(fmt_key, key_id) + struct.pack("<b", value)
        elif size_type == "U2":
            payload += struct.pack(fmt_key, key_id) + struct.pack("<H", value)
        elif size_type == "I4":
            payload += struct.pack(fmt_key, key_id) + struct.pack("<i", value)
        elif size_type == "U4":
            payload += struct.pack(fmt_key, key_id) + struct.pack("<I", value)

    return _ubx_message(UBX_CFG_CLASS, UBX_CFG_VALSET_ID, payload)


def parse_nav_pvt(payload: bytes) -> dict | None:
    """Parse UBX-NAV-PVT payload (92 bytes)."""
    if len(payload) < 92:
        return None

    year = struct.unpack_from("<H", payload, 4)[0]
    month = payload[6]
    day = payload[7]
    hour = payload[8]
    minute = payload[9]
    second = payload[10]
    fix_type = payload[20]
    num_sv = payload[23]
    lon_raw = struct.unpack_from("<i", payload, 24)[0]
    lat_raw = struct.unpack_from("<i", payload, 28)[0]
    height_raw = struct.unpack_from("<i", payload, 32)[0]
    h_acc_raw = struct.unpack_from("<I", payload, 40)[0]
    v_acc_raw = struct.unpack_from("<I", payload, 44)[0]
    ground_speed_raw = struct.unpack_from("<i", payload, 60)[0]
    heading_raw = struct.unpack_from("<i", payload, 64)[0]
    pdop_raw = struct.unpack_from("<H", payload, 76)[0]

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
    """Parse UBX-NAV-SAT payload (variable length)."""
    if len(payload) < 8:
        return []

    num_svs = payload[5]
    satellites = []
    offset = 8

    for _ in range(num_svs):
        if offset + 12 > len(payload):
            break

        gnss_id = payload[offset]
        sv_id = payload[offset + 1]
        cno = payload[offset + 2]
        elev = struct.unpack_from("<b", payload, offset + 3)[0]
        azim = struct.unpack_from("<h", payload, offset + 4)[0]
        flags = struct.unpack_from("<I", payload, offset + 8)[0]
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


def parse_rtcm_message_spec(message_spec: str | None) -> dict[int, int]:
    """Parse config like '1005(10),1077,1087,1097,1127,1230(10)'."""
    if not message_spec or not message_spec.strip():
        return dict(DEFAULT_RTCM_RATES)

    rates: dict[int, int] = {}
    for raw_item in message_spec.split(","):
        item = raw_item.strip()
        if not item:
            continue

        rate = 1
        if "(" in item and item.endswith(")"):
            msg_part, rate_part = item[:-1].split("(", 1)
            item = msg_part.strip()
            try:
                rate = max(0, int(rate_part.strip()))
            except ValueError:
                logger.warning("Ignoring invalid RTCM rate in %r", raw_item)
                continue

        try:
            message_id = int(item)
        except ValueError:
            logger.warning("Ignoring invalid RTCM message id in %r", raw_item)
            continue

        key_id = RTCM_MESSAGE_KEYS.get(message_id)
        if key_id is None:
            logger.warning("Ignoring unsupported RTCM message id %s", message_id)
            continue

        rates[key_id] = rate

    return rates or dict(DEFAULT_RTCM_RATES)


class UBloxBackend:
    """u-blox ZED-F9P backend: parse UBX frames and send configuration commands."""

    async def parse_frame(self, frame: bytes, state) -> None:
        """Parse a UBX frame and update GNSSState."""
        if len(frame) < 8:
            return

        msg_class = frame[2]
        msg_id = frame[3]
        payload_len = struct.unpack_from("<H", frame, 4)[0]
        payload = frame[6:6 + payload_len]

        if msg_class != UBX_NAV_CLASS:
            return

        if msg_id == UBX_NAV_PVT_ID:
            result = parse_nav_pvt(payload)
            if result is not None:
                await state.update_pvt(
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
            await state.update_satellites(satellites, len(satellites))

    async def configure_base_mode(self, serial_reader, lat: float, lon: float, height: float):
        """Configure F9P as fixed-position base station via UBX-CFG-VALSET.

        lat/lon in degrees, height in meters above ellipsoid.
        """
        # Split lat/lon into integer (1e-7) and high-precision (1e-9) parts
        lat_1e7 = int(lat * 1e7)
        lat_hp = int(round((lat * 1e7 - lat_1e7) * 100))
        lon_1e7 = int(lon * 1e7)
        lon_hp = int(round((lon * 1e7 - lon_1e7) * 100))
        height_cm = int(height * 100)
        height_hp = int(round((height * 100 - height_cm) * 10))

        msg = _build_valset([
            (CFG_TMODE_MODE, 2, "U1"),        # Fixed mode
            (CFG_TMODE_POS_TYPE, 1, "U1"),     # LLH
            (CFG_TMODE_LAT, lat_1e7, "I4"),
            (CFG_TMODE_LAT_HP, lat_hp, "I1"),
            (CFG_TMODE_LON, lon_1e7, "I4"),
            (CFG_TMODE_LON_HP, lon_hp, "I1"),
            (CFG_TMODE_HEIGHT, height_cm, "I4"),
            (CFG_TMODE_HEIGHT_HP, height_hp, "I1"),
            (CFG_NAVSPG_DYNMODEL, DYNMODEL_STATIONARY, "U1"),
        ])

        await serial_reader.write(msg)
        logger.info(
            "Configured base mode: lat=%.9f lon=%.9f height=%.4f",
            lat, lon, height,
        )
        await asyncio.sleep(0.5)

    async def configure_rover_mode(self, serial_reader):
        """Disable TMODE3 (return to rover/normal mode)."""
        msg = _build_valset([
            (CFG_TMODE_MODE, 0, "U1"),  # Disabled
        ])
        await serial_reader.write(msg)
        logger.info("Configured rover mode (TMODE3 disabled)")
        await asyncio.sleep(0.3)

    async def enable_rtcm_output(self, serial_reader, message_spec: str | None = None):
        """Enable RTCM3 message output on USB port."""
        selected_rates = parse_rtcm_message_spec(message_spec)
        kvs = [(CFG_USBOUTPROT_RTCM3X, 1, "U1")]
        kvs.extend((key, rate, "U1") for key, rate in selected_rates.items())
        msg = _build_valset(kvs)
        await serial_reader.write(msg)
        logger.info("Enabled RTCM3 output on USB: %s", selected_rates)
        await asyncio.sleep(0.3)

    async def disable_rtcm_output(self, serial_reader):
        """Disable all RTCM3 messages on USB port."""
        kvs = [(CFG_USBOUTPROT_RTCM3X, 0, "U1")]
        kvs.extend((key, 0, "U1") for key in DEFAULT_RTCM_RATES)
        msg = _build_valset(kvs)
        await serial_reader.write(msg)
        logger.info("Disabled RTCM3 output on USB")
        await asyncio.sleep(0.3)

    async def enable_antenna_voltage(self, serial_reader):
        """Enable antenna voltage, short detection, and open detection.

        Saves to RAM + BBR + Flash. Re-applied on every startup as safety net.
        """
        msg = _build_valset([
            (CFG_HW_ANT_CFG_VOLTCTRL, 1, "U1"),
            (CFG_HW_ANT_CFG_SHORTDET, 1, "U1"),
            (CFG_HW_ANT_CFG_OPENDET, 1, "U1"),
        ])
        await serial_reader.write(msg)
        logger.info("Antenna voltage + short/open detection enabled")
        await asyncio.sleep(0.5)

    async def set_update_rate(self, serial_reader, hz: int = 1):
        """Set measurement rate in Hz."""
        ms = max(100, 1000 // hz)
        msg = _build_valset([
            (CFG_RATE_MEAS, ms, "U2"),
        ])
        await serial_reader.write(msg)
        logger.info("Update rate set to %d Hz (%d ms)", hz, ms)
        await asyncio.sleep(0.2)

    async def set_dynamic_model(self, serial_reader, model: int = DYNMODEL_STATIONARY):
        """Set navigation dynamic model."""
        msg = _build_valset([
            (CFG_NAVSPG_DYNMODEL, model, "U1"),
        ])
        await serial_reader.write(msg)
        logger.info("Dynamic model set to %d", model)
        await asyncio.sleep(0.2)
