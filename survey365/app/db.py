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
        if row is not None:
            return  # Already initialized

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
