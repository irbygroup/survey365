"""Tests for ntrip_caster.py — native and proxy mode behavior."""

import asyncio

import pytest
import pytest_asyncio

from app.gnss.ntrip_caster import NTRIPCaster


@pytest_asyncio.fixture
async def native_caster():
    """Start a native-mode caster on an OS-assigned port."""
    caster = NTRIPCaster(port=0, mountpoint="TEST", upstream_port=None)
    await caster.start()
    sockets = caster._server.sockets
    assert sockets
    actual_port = sockets[0].getsockname()[1]
    caster._port = actual_port
    yield caster
    await caster.close()


@pytest.mark.asyncio
async def test_native_source_table(native_caster):
    """GET / should return a source table without needing an upstream."""
    reader, writer = await asyncio.open_connection("127.0.0.1", native_caster._port)
    try:
        writer.write(b"GET / HTTP/1.0\r\nUser-Agent: test\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(4096), timeout=2.0)
        text = data.decode("ascii", errors="replace")
        assert "SOURCETABLE 200 OK" in text
        assert "TEST" in text
        assert "ENDSOURCETABLE" in text
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_native_stream_receives_rtcm(native_caster):
    """A rover connecting to the mountpoint should receive RTCM via write()."""
    reader, writer = await asyncio.open_connection("127.0.0.1", native_caster._port)
    try:
        writer.write(b"GET /TEST HTTP/1.0\r\nUser-Agent: test\r\n\r\n")
        await writer.drain()
        # Read ICY response
        response = await asyncio.wait_for(reader.readline(), timeout=2.0)
        assert b"ICY 200 OK" in response
        # Consume the blank line
        await asyncio.wait_for(reader.readline(), timeout=2.0)
        await asyncio.sleep(0.1)

        # Broadcast RTCM data
        test_rtcm = b"\xd3\x00\x05HELLO\xaa\xbb\xcc"
        await native_caster.write(test_rtcm)
        await asyncio.sleep(0.15)

        data = await asyncio.wait_for(reader.read(100), timeout=2.0)
        assert data == test_rtcm
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_native_gga_capture(native_caster):
    """Inbound GGA from a rover should be captured in the session."""
    reader, writer = await asyncio.open_connection("127.0.0.1", native_caster._port)
    try:
        writer.write(b"GET /TEST HTTP/1.0\r\nUser-Agent: test\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=2.0)
        await asyncio.wait_for(reader.readline(), timeout=2.0)
        await asyncio.sleep(0.1)

        # Send a GGA sentence as the rover would
        gga = b"$GPGGA,120000.00,3041.6700,N,08802.5900,W,4,12,1.0,15.000,M,0.0,M,,*XX\r\n"
        writer.write(gga)
        await writer.drain()
        await asyncio.sleep(0.3)

        snap = native_caster.snapshot_clients()
        assert snap["running"] is True
        assert snap["upstream_active"] is False
        assert snap["upstream_port"] is None
        assert len(snap["active_clients"]) == 1
        session = snap["active_clients"][0]
        assert session["last_gga"] is not None
        assert session["last_gga"]["type"] == "GGA"
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_snapshot_shape_native(native_caster):
    """snapshot_clients must have the same top-level keys in both modes."""
    snap = native_caster.snapshot_clients()
    required_keys = {
        "running", "port", "mountpoint", "bytes_served",
        "active_clients", "recent_clients",
        "upstream_active", "upstream_port", "last_proxy_error",
    }
    assert required_keys <= set(snap.keys())
    # Native mode neutral upstream values
    assert snap["upstream_active"] is False
    assert snap["upstream_port"] is None
    assert snap["last_proxy_error"] is None
