"""
Geodetic helpers for derived elevation products.

Uses the NOAA/NGS GEOID18 web service to derive NAVD88 orthometric height from
ellipsoid height. Results are cached by rounded coordinate because geoid
separation changes slowly over short distances.
"""

import asyncio
import json
import logging
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .db import get_config

logger = logging.getLogger("survey365.geodesy")

_NOAA_GEOID_API = "https://geodesy.noaa.gov/api/geoid/ght"


@dataclass
class GeoidResult:
    model: str
    height_m: float
    error_m: float | None


class GeoidService:
    def __init__(self):
        self._cache: dict[tuple[float, float], GeoidResult] = {}
        self._lock = asyncio.Lock()

    async def lookup(self, lat: float, lon: float) -> GeoidResult | None:
        key = (round(lat, 4), round(lon, 4))
        async with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            result = await asyncio.to_thread(self._fetch, lat, lon)
        except Exception as exc:
            logger.warning("Geoid lookup failed: %s", exc)
            return None

        async with self._lock:
            self._cache[key] = result
        return result

    @staticmethod
    def _fetch(lat: float, lon: float) -> GeoidResult:
        query = urllib.parse.urlencode({
            "lat": f"{lat:.9f}",
            "lon": f"{lon:.9f}",
            "model": "14",
            "station": "Survey365",
        })
        url = f"{_NOAA_GEOID_API}?{query}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.load(response)

        return GeoidResult(
            model=data.get("geoidModel", "GEOID18"),
            height_m=float(data["geoidHeight"]),
            error_m=float(data["error"]) if data.get("error") is not None else None,
        )


geoid_service = GeoidService()


def _to_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


async def enrich_gnss_snapshot(snapshot: dict) -> dict:
    """Augment a GNSS snapshot with MSL/NAVD88/antenna-adjusted elevation fields."""
    antenna_height_m = _to_float(await get_config("antenna_height_m"), default=0.0)
    return await build_vertical_products(
        snapshot,
        antenna_height_m=antenna_height_m,
    )


async def build_vertical_products(
    snapshot: dict,
    *,
    antenna_height_m: float,
) -> dict:
    """Return GNSS snapshot fields plus derived MSL/NAVD88 elevation values."""
    enriched = dict(snapshot)
    ellipsoid_height = snapshot.get("height")
    latitude = snapshot.get("latitude")
    longitude = snapshot.get("longitude")
    vertical_accuracy = snapshot.get("accuracy_v")
    height_msl = snapshot.get("height_msl")

    enriched["height_ellipsoid"] = ellipsoid_height
    enriched["height_msl_accuracy"] = vertical_accuracy
    enriched["antenna_height_m"] = antenna_height_m
    enriched["height_navd88"] = None
    enriched["ground_navd88"] = None
    enriched["height_navd88_accuracy"] = None
    enriched["elevation"] = None
    enriched["elevation_accuracy"] = None
    enriched["elevation_label"] = "NAVD88"
    enriched["geoid_height"] = None
    enriched["geoid_model"] = None
    enriched["geoid_error"] = None

    has_position = snapshot.get("age") is not None
    if not has_position or ellipsoid_height is None or latitude is None or longitude is None:
        enriched["height"] = None
        enriched["height_msl"] = None
        enriched["height_ellipsoid"] = None
        return enriched

    geoid = await geoid_service.lookup(latitude, longitude)
    if geoid is None:
        if height_msl is not None:
            enriched["elevation"] = height_msl - antenna_height_m
            enriched["elevation_accuracy"] = vertical_accuracy
            enriched["elevation_label"] = "MSL"
        return enriched

    navd88_height = ellipsoid_height - geoid.height_m
    navd88_ground = navd88_height - antenna_height_m
    navd88_accuracy = vertical_accuracy
    if vertical_accuracy is not None and geoid.error_m is not None:
        navd88_accuracy = math.sqrt(vertical_accuracy**2 + geoid.error_m**2)

    enriched["height_navd88"] = navd88_height
    enriched["ground_navd88"] = navd88_ground
    enriched["height_navd88_accuracy"] = navd88_accuracy
    enriched["elevation"] = navd88_ground
    enriched["elevation_accuracy"] = navd88_accuracy
    enriched["elevation_label"] = "NAVD88"
    enriched["geoid_height"] = geoid.height_m
    enriched["geoid_model"] = geoid.model
    enriched["geoid_error"] = geoid.error_m
    return enriched
