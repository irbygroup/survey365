"""
RTCM helpers: parse message types and build synthetic reference-station frames.
"""

import math

from .serial_reader import crc24q

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def parse_rtcm_message_type(frame: bytes) -> int | None:
    """Return the RTCM3 message number from a full RTCM frame."""
    if len(frame) < 9 or frame[0] != 0xD3:
        return None

    length = ((frame[1] & 0x03) << 8) | frame[2]
    if len(frame) < 3 + length + 3 or length < 2:
        return None

    payload = frame[3:3 + length]
    return ((payload[0] << 4) | (payload[1] >> 4)) & 0x0FFF


def build_rtcm_1006(
    latitude_deg: float,
    longitude_deg: float,
    height_m: float,
    *,
    station_id: int = 1,
    antenna_height_m: float = 0.0,
    itrf_year: int = 0,
) -> bytes:
    """Build an RTCM3 1006 frame for a fixed reference station."""
    x_m, y_m, z_m = llh_to_ecef(latitude_deg, longitude_deg, height_m)
    x = int(round(x_m * 10000.0))
    y = int(round(y_m * 10000.0))
    z = int(round(z_m * 10000.0))
    ant_h = max(0, int(round(antenna_height_m * 10000.0)))

    bits = _BitBuffer()
    bits.add_unsigned(1006, 12)
    bits.add_unsigned(station_id & 0x0FFF, 12)
    bits.add_unsigned(itrf_year & 0x3F, 6)
    bits.add_unsigned(1, 1)  # GPS
    bits.add_unsigned(1, 1)  # GLONASS
    bits.add_unsigned(1, 1)  # Galileo
    bits.add_unsigned(0, 1)  # Reference-station indicator
    bits.add_signed(x, 38)
    bits.add_unsigned(0, 1)  # Single receiver oscillator indicator
    bits.add_unsigned(0, 1)  # Reserved
    bits.add_signed(y, 38)
    bits.add_unsigned(0, 2)  # Quarter-cycle indicator
    bits.add_signed(z, 38)
    bits.add_unsigned(ant_h & 0xFFFF, 16)

    payload = bits.to_bytes()
    header = bytes((
        0xD3,
        (len(payload) >> 8) & 0x03,
        len(payload) & 0xFF,
    ))
    crc = crc24q(header + payload)
    return header + payload + bytes((
        (crc >> 16) & 0xFF,
        (crc >> 8) & 0xFF,
        crc & 0xFF,
    ))


def llh_to_ecef(latitude_deg: float, longitude_deg: float, height_m: float) -> tuple[float, float, float]:
    """Convert WGS84 geodetic coordinates to ECEF meters."""
    lat = math.radians(latitude_deg)
    lon = math.radians(longitude_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)

    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + height_m) * cos_lat * cos_lon
    y = (n + height_m) * cos_lat * sin_lon
    z = (n * (1.0 - WGS84_E2) + height_m) * sin_lat
    return x, y, z


class _BitBuffer:
    def __init__(self):
        self._bits: list[int] = []

    def add_unsigned(self, value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            self._bits.append((value >> shift) & 1)

    def add_signed(self, value: int, width: int) -> None:
        if value < 0:
            value = (1 << width) + value
        self.add_unsigned(value, width)

    def to_bytes(self) -> bytes:
        while len(self._bits) % 8:
            self._bits.append(0)

        out = bytearray()
        for i in range(0, len(self._bits), 8):
            byte = 0
            for bit in self._bits[i:i + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return bytes(out)
