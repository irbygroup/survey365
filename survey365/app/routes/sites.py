"""
Sites CRUD routes: saved survey points with SpatiaLite proximity queries.

All sites have lat/lon/height coordinates and optional SpatiaLite geometry
for spatial distance calculations. If SpatiaLite is not available, proximity
sorting falls back to a Haversine approximation in SQL.
"""

import math
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import get_db

router = APIRouter(prefix="/api/sites", tags=["sites"])


class SiteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    height: float | None = None
    ortho_height: float | None = None
    datum: str = "NAD83(2011)"
    epoch: str = "2010.0"
    source: str | None = "manual"
    accuracy_h: float | None = None
    accuracy_v: float | None = None
    established: str | None = None
    notes: str | None = None


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    height: float | None = None
    ortho_height: float | None = None
    datum: str | None = None
    epoch: str | None = None
    source: str | None = None
    accuracy_h: float | None = None
    accuracy_v: float | None = None
    established: str | None = None
    notes: str | None = None


def _row_to_dict(row, distance_m: float | None = None) -> dict:
    """Convert a database row to a site dict, adding distance if provided."""
    site = {
        "id": row["id"],
        "name": row["name"],
        "lat": row["lat"],
        "lon": row["lon"],
        "height": row["height"],
        "ortho_height": row["ortho_height"],
        "datum": row["datum"],
        "epoch": row["epoch"],
        "source": row["source"],
        "accuracy_h": row["accuracy_h"],
        "accuracy_v": row["accuracy_v"],
        "established": row["established"],
        "last_used": row["last_used"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if distance_m is not None:
        site["distance_m"] = round(distance_m, 1)
    return site


@router.get("")
async def list_sites(
    near_lat: float | None = Query(default=None, ge=-90, le=90),
    near_lon: float | None = Query(default=None, ge=-180, le=180),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List sites with optional proximity sort and text search.

    If near_lat and near_lon are provided, results are sorted by distance
    and include a distance_m field. Uses SpatiaLite ST_Distance when available,
    otherwise falls back to Haversine approximation.
    """
    async with get_db() as db:
        has_proximity = near_lat is not None and near_lon is not None

        if has_proximity:
            # Try SpatiaLite distance first
            try:
                query = """
                    SELECT *, ST_Distance(geom, MakePoint(?, ?, 4326), 1) as distance_m
                    FROM sites
                """
                params: list = [near_lon, near_lat]

                if search:
                    query += " WHERE (name LIKE ? OR notes LIKE ?)"
                    params.extend([f"%{search}%", f"%{search}%"])

                query += " ORDER BY distance_m ASC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                cursor = await db.execute(query, params)
                rows = await cursor.fetchall()

                # Get total count
                count_query = "SELECT COUNT(*) as cnt FROM sites"
                count_params: list = []
                if search:
                    count_query += " WHERE (name LIKE ? OR notes LIKE ?)"
                    count_params = [f"%{search}%", f"%{search}%"]
                count_cursor = await db.execute(count_query, count_params)
                count_row = await count_cursor.fetchone()

                sites = [_row_to_dict(r, distance_m=r["distance_m"]) for r in rows]
                return {"sites": sites, "total": count_row["cnt"]}

            except Exception:
                # SpatiaLite not available, fall through to Haversine fallback
                pass

            # Haversine fallback: approximate distance using SQL math
            # This uses the equirectangular approximation which is accurate enough
            # for sorting at the distances we care about (< 100km)
            cos_lat = math.cos(math.radians(near_lat))
            query = f"""
                SELECT *,
                    111319.9 * sqrt(
                        pow((lat - ?) * 1.0, 2) +
                        pow((lon - ?) * {cos_lat}, 2)
                    ) as distance_m
                FROM sites
            """
            params = [near_lat, near_lon]

            if search:
                query += " WHERE (name LIKE ? OR notes LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])

            query += " ORDER BY distance_m ASC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        else:
            # No proximity -- order by last_used descending, then name
            query = "SELECT * FROM sites"
            params = []

            if search:
                query += " WHERE (name LIKE ? OR notes LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])

            query += " ORDER BY last_used DESC NULLS LAST, name ASC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        # Get total count
        count_query = "SELECT COUNT(*) as cnt FROM sites"
        count_params = []
        if search:
            count_query += " WHERE (name LIKE ? OR notes LIKE ?)"
            count_params = [f"%{search}%", f"%{search}%"]
        count_cursor = await db.execute(count_query, count_params)
        count_row = await count_cursor.fetchone()

        if has_proximity:
            sites = [_row_to_dict(r, distance_m=r["distance_m"]) for r in rows]
        else:
            sites = [_row_to_dict(r) for r in rows]

        return {"sites": sites, "total": count_row["cnt"]}


@router.get("/{site_id}")
async def get_site(site_id: int):
    """Get a single site by ID."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM sites WHERE id = ?", (site_id,))
        row = await cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Site not found")

    return _row_to_dict(row)


@router.post("", status_code=201)
async def create_site(site: SiteCreate):
    """Create a new site with optional SpatiaLite geometry."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO sites (name, lat, lon, height, ortho_height, datum, epoch,
                               source, accuracy_h, accuracy_v, established, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site.name,
                site.lat,
                site.lon,
                site.height,
                site.ortho_height,
                site.datum,
                site.epoch,
                site.source,
                site.accuracy_h,
                site.accuracy_v,
                site.established or datetime.now().strftime("%Y-%m-%d"),
                site.notes,
            ),
        )
        site_id = cursor.lastrowid

        # Set SpatiaLite geometry if available
        try:
            await db.execute(
                "UPDATE sites SET geom = MakePoint(?, ?, 4326) WHERE id = ?",
                (site.lon, site.lat, site_id),
            )
        except Exception:
            pass  # SpatiaLite not available

        await db.commit()

        # Fetch the created row
        cursor = await db.execute("SELECT * FROM sites WHERE id = ?", (site_id,))
        row = await cursor.fetchone()

    return _row_to_dict(row)


@router.put("/{site_id}")
async def update_site(site_id: int, site: SiteUpdate):
    """Update an existing site. Only provided fields are updated."""
    async with get_db() as db:
        # Check site exists
        cursor = await db.execute("SELECT id FROM sites WHERE id = ?", (site_id,))
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Site not found")

        # Build dynamic UPDATE from provided fields
        updates = []
        values = []
        update_data = site.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        for field_name, value in update_data.items():
            updates.append(f"{field_name} = ?")
            values.append(value)

        # Always update the updated_at timestamp
        updates.append("updated_at = datetime('now')")

        values.append(site_id)

        await db.execute(
            f"UPDATE sites SET {', '.join(updates)} WHERE id = ?",
            values,
        )

        # Update SpatiaLite geometry if lat or lon changed
        if "lat" in update_data or "lon" in update_data:
            # Need full lat/lon for geometry update
            cursor = await db.execute(
                "SELECT lat, lon FROM sites WHERE id = ?", (site_id,)
            )
            row = await cursor.fetchone()
            try:
                await db.execute(
                    "UPDATE sites SET geom = MakePoint(?, ?, 4326) WHERE id = ?",
                    (row["lon"], row["lat"], site_id),
                )
            except Exception:
                pass  # SpatiaLite not available

        await db.commit()

        # Fetch updated row
        cursor = await db.execute("SELECT * FROM sites WHERE id = ?", (site_id,))
        row = await cursor.fetchone()

    return _row_to_dict(row)


@router.delete("/{site_id}")
async def delete_site(site_id: int):
    """Delete a site by ID."""
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM sites WHERE id = ?", (site_id,))
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Site not found")

        await db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        await db.commit()

    return {"ok": True}
