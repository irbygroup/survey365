"""
Project management routes: CRUD operations for survey projects.

Projects group sites and sessions together. One project is "active" at a time,
and all field operations (sites, mode, sessions) are scoped to it.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_active_project_id, get_db, set_active_project_id

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    client: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    client: str | None = None


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "client": row["client"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_accessed": row["last_accessed"],
    }


@router.get("")
async def list_projects():
    """List all projects, sorted by last_accessed descending."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM projects ORDER BY last_accessed DESC NULLS LAST, name ASC"
        )
        rows = await cursor.fetchall()

    active_id = await get_active_project_id()
    projects = []
    for row in rows:
        p = _row_to_dict(row)
        p["active"] = p["id"] == active_id
        projects.append(p)

    return {"projects": projects, "active_project_id": active_id}


@router.get("/active")
async def get_active_project():
    """Get the currently active project."""
    active_id = await get_active_project_id()
    if active_id is None:
        return {"project": None}

    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (active_id,))
        row = await cursor.fetchone()

    if row is None:
        # Active project was deleted, clear it
        await set_active_project_id(None)
        return {"project": None}

    return {"project": _row_to_dict(row)}


@router.post("", status_code=201)
async def create_project(project: ProjectCreate):
    """Create a new project."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO projects (name, description, client, last_accessed)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (project.name, project.description, project.client),
        )
        project_id = cursor.lastrowid
        await db.commit()

        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()

    return _row_to_dict(row)


@router.put("/{project_id}")
async def update_project(project_id: int, project: ProjectUpdate):
    """Update a project. Only provided fields are updated."""
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found")

        updates = []
        values = []
        update_data = project.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        for field_name, value in update_data.items():
            updates.append(f"{field_name} = ?")
            values.append(value)

        updates.append("updated_at = datetime('now')")
        values.append(project_id)

        await db.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        await db.commit()

        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()

    return _row_to_dict(row)


@router.delete("/{project_id}")
async def delete_project(project_id: int):
    """Delete a project. Fails if project has sites."""
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found")

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM sites WHERE project_id = ?", (project_id,)
        )
        count = await cursor.fetchone()
        if count["cnt"] > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete project with {count['cnt']} site(s). Delete or move sites first.",
            )

        await db.execute("DELETE FROM sessions WHERE project_id = ?", (project_id,))
        await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()

    # Clear active project if it was the deleted one
    active_id = await get_active_project_id()
    if active_id == project_id:
        await set_active_project_id(None)

    return {"ok": True}


@router.post("/{project_id}/activate")
async def activate_project(project_id: int):
    """Set a project as the active project."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")

        await db.execute(
            "UPDATE projects SET last_accessed = datetime('now') WHERE id = ?",
            (project_id,),
        )
        await db.commit()

    await set_active_project_id(project_id)

    return {"ok": True, "project": _row_to_dict(row)}
