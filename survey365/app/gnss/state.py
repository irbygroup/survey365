"""
GNSS state: shared in-memory state updated by the reader, consumed by routes/WS.

Moved from app/gnss.py — GNSSState is unchanged. Thread-safe via asyncio.Lock.
"""

import asyncio
import time
from dataclasses import dataclass, field

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
