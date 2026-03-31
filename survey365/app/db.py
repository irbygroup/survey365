"""
SQLite + SpatiaLite database connection and helpers.

DB file location: configurable via SURVEY365_DB env var.
Default: /opt/survey365/data/survey365.db

SpatiaLite is loaded as an extension on every connection open.
WAL mode is enabled for concurrent reads with single writer.
"""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

DB_PATH = os.environ.get("SURVEY365_DB", "/opt/survey365/data/survey365.db")
MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# SpatiaLite extension name varies by platform
_SPATIALITE_NAMES = ("mod_spatialite", "libspatialite")


def _find_spatialite() -> str:
    """Return the loadable SpatiaLite extension name, or empty string if unavailable."""
    for name in _SPATIALITE_NAMES:
        try:
            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            conn.load_extension(name)
            conn.close()
            return name
        except Exception:
            continue
    return ""


_spatialite_ext: str | None = None


def _get_spatialite_ext() -> str:
    """Cache the SpatiaLite extension name after first lookup."""
    global _spatialite_ext
    if _spatialite_ext is None:
        _spatialite_ext = _find_spatialite()
    return _spatialite_ext


@asynccontextmanager
async def get_db():
    """Async context manager yielding an aiosqlite connection with SpatiaLite loaded.

    Uses deferred transactions (aiosqlite default) so that BEGIN is issued
    on the first write statement, and commit() must be called explicitly.
    """
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        # Enable WAL mode for better concurrent access
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        # Load SpatiaLite if available
        ext = _get_spatialite_ext()
        if ext:
            await db.enable_load_extension(True)
            await db.load_extension(ext)

        yield db
    finally:
        await db.close()


async def init_db():
    """Initialize the database: create data directory, run migrations if needed."""
    # Ensure data directory exists
    db_dir = Path(DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    async with get_db() as db:
        # Check if tables already exist
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sites'"
        )
        row = await cursor.fetchone()
        if row is None:
            # Run initial migration
            migration_file = MIGRATIONS_DIR / "001_initial.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                # SpatiaLite-specific DDL needs special handling:
                # Execute statements one at a time (executescript doesn't work with extensions)
                statements = [s.strip() for s in sql.split(";") if s.strip()]
                for stmt in statements:
                    if not stmt:
                        continue
                    try:
                        await db.execute(stmt)
                    except Exception:
                        # SpatiaLite functions (AddGeometryColumn, etc.) may fail
                        # if SpatiaLite is not available -- that is acceptable for dev
                        pass

            # Initialize SpatiaLite metadata if extension is loaded
            ext = _get_spatialite_ext()
            if ext:
                try:
                    await db.execute("SELECT InitSpatialMetaData(1)")
                except Exception:
                    pass  # Already initialized or not available
                try:
                    await db.execute(
                        "SELECT AddGeometryColumn('sites', 'geom', 4326, 'POINT', 'XY')"
                    )
                except Exception:
                    pass  # Column already exists
                try:
                    await db.execute("SELECT CreateSpatialIndex('sites', 'geom')")
                except Exception:
                    pass  # Index already exists

        # --- Migration 002: Projects ---
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
        )
        if await cursor.fetchone() is None:
            migration_file = MIGRATIONS_DIR / "002_projects.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                    try:
                        await db.execute(stmt)
                    except Exception:
                        pass  # Column may already exist on re-run

            # Assign orphaned sites (pre-existing) to a default project
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM sites WHERE project_id IS NULL"
            )
            orphan_count = (await cursor.fetchone())["cnt"]
            if orphan_count > 0:
                cursor = await db.execute(
                    """
                    INSERT INTO projects (name, description, last_accessed)
                    VALUES ('Default Project', 'Auto-created for existing sites', datetime('now'))
                    """
                )
                default_pid = cursor.lastrowid
                await db.execute(
                    "UPDATE sites SET project_id = ? WHERE project_id IS NULL",
                    (default_pid,),
                )
                # Auto-activate the default project
                await db.execute(
                    """
                    INSERT OR REPLACE INTO config (key, value, updated_at)
                    VALUES ('active_project_id', ?, datetime('now'))
                    """,
                    (str(default_pid),),
                )

        # --- Migration 003: GNSS config keys ---
        cursor = await db.execute(
            "SELECT value FROM config WHERE key = 'gnss_port'"
        )
        if await cursor.fetchone() is None:
            migration_file = MIGRATIONS_DIR / "003_gnss_config.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                    try:
                        await db.execute(stmt)
                    except Exception:
                        pass

        # --- Migration 004: ALDOT CORS profiles ---
        cursor = await db.execute(
            "SELECT id FROM ntrip_profiles WHERE name LIKE 'ALDOT CORS%' LIMIT 1"
        )
        if await cursor.fetchone() is None:
            migration_file = MIGRATIONS_DIR / "004_aldot_cors.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                    try:
                        await db.execute(stmt)
                    except Exception:
                        pass

        await db.commit()


async def get_config(key: str) -> str | None:
    """Read a single config value by key."""
    async with get_db() as db:
        cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["value"]


async def set_config(key: str, value: str):
    """Write a config value, creating or updating the row."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO config (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value),
        )
        await db.commit()


async def get_all_config() -> dict[str, str]:
    """Read all config key-value pairs as a dict."""
    async with get_db() as db:
        cursor = await db.execute("SELECT key, value FROM config")
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}


async def get_active_project_id() -> int | None:
    """Get the active project ID from config."""
    value = await get_config("active_project_id")
    if value and value.strip():
        try:
            return int(value)
        except ValueError:
            return None
    return None


async def set_active_project_id(project_id: int | None):
    """Set the active project ID in config."""
    await set_config("active_project_id", str(project_id) if project_id else "")
