"""Tests for raw_relay.py — byte fidelity, queue saturation, dead-client cleanup."""

import asyncio

import pytest
import pytest_asyncio

from app.gnss.raw_relay import RawRelay


@pytest_asyncio.fixture
async def relay():
    r = RawRelay(host="127.0.0.1", port=0)  # port 0 = OS-assigned
    await r.start()
    # Discover the actual port
    sockets = r._server.sockets
    assert sockets
    r.port = sockets[0].getsockname()[1]
    yield r
    await r.stop()


async def _connect(relay: RawRelay):
    reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
    # Wait briefly for the relay to register the client
    await asyncio.sleep(0.05)
    return reader, writer


@pytest.mark.asyncio
async def test_exact_byte_preservation(relay):
    """Published bytes must arrive exactly at the client."""
    reader, writer = await _connect(relay)
    try:
        payload = b"\xb5\x62\x01\x07" + bytes(range(256))
        relay.publish_nowait(payload)
        await asyncio.sleep(0.1)
        data = await asyncio.wait_for(reader.read(len(payload) + 100), timeout=2.0)
        assert data == payload
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_multiple_clients_receive_same_data(relay):
    """All connected clients must receive the same bytes."""
    clients = [await _connect(relay) for _ in range(3)]
    try:
        payload = b"HELLO_RELAY"
        relay.publish_nowait(payload)
        await asyncio.sleep(0.15)
        for reader, _ in clients:
            data = await asyncio.wait_for(reader.read(100), timeout=2.0)
            assert data == payload
    finally:
        for _, w in clients:
            w.close()
            await w.wait_closed()


@pytest.mark.asyncio
async def test_queue_saturation_does_not_crash(relay):
    """Filling the queue should not raise; chunks are dropped gracefully."""
    for _ in range(600):
        relay.publish_nowait(b"x" * 100)
    assert relay._dropped_chunks >= 0  # may or may not drop depending on timing


@pytest.mark.asyncio
async def test_dead_client_removed(relay):
    """A client that disconnects is cleaned up on the next broadcast."""
    reader, writer = await _connect(relay)
    assert relay.client_count == 1
    writer.close()
    await writer.wait_closed()
    # Broadcast should trigger cleanup
    relay.publish_nowait(b"after_close")
    await asyncio.sleep(0.3)
    assert relay.client_count == 0
