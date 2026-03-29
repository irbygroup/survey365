"""
NTRIP profiles CRUD (simplified for Phase 1).

NTRIP profiles store connection details for outbound casters (Emlid, etc.),
inbound CORS sources, and local casters. Phase 1 provides basic CRUD --
profile selection and service integration come in Phase 2.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_db

router = APIRouter(prefix="/api/ntrip", tags=["ntrip"])


class NtripProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., pattern="^(outbound_caster|inbound_cors|local_caster)$")
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    mountpoint: str | None = None
    username: str | None = None
    password: str | None = None
    is_default: bool = False
    notes: str | None = None


class NtripProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: str | None = Field(default=None, pattern="^(outbound_caster|inbound_cors|local_caster)$")
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    mountpoint: str | None = None
    username: str | None = None
    password: str | None = None
    is_default: bool | None = None
    notes: str | None = None


def _row_to_dict(row) -> dict:
    """Convert a database row to an NTRIP profile dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "host": row["host"],
        "port": row["port"],
        "mountpoint": row["mountpoint"],
        "username": row["username"],
        "password": row["password"],
        "is_default": bool(row["is_default"]),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("")
async def list_profiles():
    """List all NTRIP profiles."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM ntrip_profiles ORDER BY is_default DESC, name ASC"
        )
        rows = await cursor.fetchall()

    return {"profiles": [_row_to_dict(r) for r in rows]}


@router.get("/{profile_id}")
async def get_profile(profile_id: int):
    """Get a single NTRIP profile by ID."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM ntrip_profiles WHERE id = ?", (profile_id,)
        )
        row = await cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="NTRIP profile not found")

    return _row_to_dict(row)


@router.post("", status_code=201)
async def create_profile(profile: NtripProfileCreate):
    """Create a new NTRIP profile."""
    async with get_db() as db:
        # If this profile is set as default, unset other defaults of same type
        if profile.is_default:
            await db.execute(
                "UPDATE ntrip_profiles SET is_default = 0 WHERE type = ?",
                (profile.type,),
            )

        cursor = await db.execute(
            """
            INSERT INTO ntrip_profiles (name, type, host, port, mountpoint,
                                         username, password, is_default, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile.name,
                profile.type,
                profile.host,
                profile.port,
                profile.mountpoint,
                profile.username,
                profile.password,
                1 if profile.is_default else 0,
                profile.notes,
            ),
        )
        profile_id = cursor.lastrowid
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM ntrip_profiles WHERE id = ?", (profile_id,)
        )
        row = await cursor.fetchone()

    return _row_to_dict(row)


@router.put("/{profile_id}")
async def update_profile(profile_id: int, profile: NtripProfileUpdate):
    """Update an existing NTRIP profile."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, type FROM ntrip_profiles WHERE id = ?", (profile_id,)
        )
        existing = await cursor.fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="NTRIP profile not found")

        update_data = profile.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # If setting as default, unset other defaults of same type
        if update_data.get("is_default"):
            profile_type = update_data.get("type", existing["type"])
            await db.execute(
                "UPDATE ntrip_profiles SET is_default = 0 WHERE type = ?",
                (profile_type,),
            )

        # Convert is_default bool to int for SQLite
        if "is_default" in update_data:
            update_data["is_default"] = 1 if update_data["is_default"] else 0

        updates = []
        values = []
        for field_name, value in update_data.items():
            updates.append(f"{field_name} = ?")
            values.append(value)

        updates.append("updated_at = datetime('now')")
        values.append(profile_id)

        await db.execute(
            f"UPDATE ntrip_profiles SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM ntrip_profiles WHERE id = ?", (profile_id,)
        )
        row = await cursor.fetchone()

    return _row_to_dict(row)


@router.delete("/{profile_id}")
async def delete_profile(profile_id: int):
    """Delete an NTRIP profile by ID."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM ntrip_profiles WHERE id = ?", (profile_id,)
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="NTRIP profile not found")

        await db.execute("DELETE FROM ntrip_profiles WHERE id = ?", (profile_id,))
        await db.commit()

    return {"ok": True}
