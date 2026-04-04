"""Tests for migration 009 — RTKLIB config defaults and legacy message migration."""

import asyncio
import os
import tempfile

import pytest
import pytest_asyncio

# Override DB path before importing app modules
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".db")
os.environ["SURVEY365_DB"] = _test_db_path

import app.db as db_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    """Reset the DB path and module state for each test."""
    db_mod.DB_PATH = _test_db_path
    yield
    # Clean up the file after each test
    try:
        os.unlink(_test_db_path)
    except OSError:
        pass
    # Re-create the file for the next test
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["SURVEY365_DB"] = path
    db_mod.DB_PATH = path
    globals()["_test_db_path"] = path
    globals()["_test_db_fd"] = fd


@pytest.mark.asyncio
async def test_fresh_install_gets_rtklib_defaults():
    """A fresh DB should get rtcm_engine=rtklib and default RTKLIB message sets."""
    await db_mod.init_db()
    engine = await db_mod.get_config("rtcm_engine")
    assert engine == "rtklib"
    local = await db_mod.get_config("rtklib_local_messages")
    assert local is not None
    assert "1004" in local


@pytest.mark.asyncio
async def test_legacy_custom_messages_migrated():
    """Non-default rtcm_messages should be copied into RTKLIB keys once."""
    # Pre-seed a DB with migrations 001-008 already applied and custom messages
    await db_mod.init_db()
    # Now simulate: remove rtcm_engine so migration 009 re-runs
    async with db_mod.get_db() as conn:
        await conn.execute("DELETE FROM config WHERE key = 'rtcm_engine'")
        await conn.execute("DELETE FROM config WHERE key = 'rtklib_local_messages'")
        await conn.execute("DELETE FROM config WHERE key = 'rtklib_outbound_messages'")
        # Set a custom native message set
        await conn.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES ('rtcm_messages', '1005,1077,1087', datetime('now'))"
        )
        await conn.commit()

    await db_mod.init_db()
    local = await db_mod.get_config("rtklib_local_messages")
    outbound = await db_mod.get_config("rtklib_outbound_messages")
    assert local == "1005,1077,1087"
    assert outbound == "1005,1077,1087"


@pytest.mark.asyncio
async def test_default_native_messages_not_overwritten():
    """If rtcm_messages is the old default, RTKLIB keys should not be overwritten."""
    await db_mod.init_db()
    async with db_mod.get_db() as conn:
        await conn.execute("DELETE FROM config WHERE key = 'rtcm_engine'")
        await conn.execute("DELETE FROM config WHERE key = 'rtklib_local_messages'")
        await conn.execute("DELETE FROM config WHERE key = 'rtklib_outbound_messages'")
        await conn.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES ('rtcm_messages', '1005,1077,1087,1097,1127,1230(10)', datetime('now'))"
        )
        await conn.commit()

    await db_mod.init_db()
    local = await db_mod.get_config("rtklib_local_messages")
    # Should get the RTKBase default, not the native default
    assert local is not None
    assert "1004" in local
