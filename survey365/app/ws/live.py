"""
WebSocket endpoint for live GNSS and mode updates.

Sends:
- "status" messages every 1 second with GNSS state
- "mode_change" messages immediately when mode transitions occur
- "establish_progress" messages during relative base averaging
- "pong" in response to client "ping"

Uses per-client queues to avoid concurrent send/receive on the same
WebSocket (starlette is not safe for concurrent access from different tasks).
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..gnss import gnss_state

logger = logging.getLogger("survey365.ws")

router = APIRouter()

# Per-client outbound message queues
_client_queues: dict[WebSocket, asyncio.Queue] = {}


async def broadcast_event(event: dict):
    """Broadcast a JSON event to all connected WebSocket clients.

    Puts the message into each client's queue. The per-client send loop
    picks it up and sends it on the WebSocket.
    """
    if not _client_queues:
        return

    message = json.dumps(event)
    dead = []
    for ws, queue in _client_queues.items():
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead.append(ws)

    for ws in dead:
        _client_queues.pop(ws, None)


async def _status_broadcast_loop():
    """Background loop: broadcast GNSS status to all clients every 1 second."""
    tick = 0
    last_services = {}

    while True:
        try:
            if _client_queues:
                from ..routes.mode import get_mode_state
                from ..rtkbase import get_service_status

                gnss = await gnss_state.snapshot()
                mode_state = get_mode_state()

                tick += 1
                if tick % 5 == 1:
                    last_services = await get_service_status()

                message = {
                    "type": "status",
                    "gnss": gnss,
                    "mode": mode_state["mode"],
                    "mode_label": mode_state["mode_label"],
                    "services": last_services,
                }

                await broadcast_event(message)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Status broadcast error: %s", exc)

        await asyncio.sleep(1.0)


_broadcast_task: asyncio.Task | None = None


def start_broadcast():
    """Start the background status broadcast loop."""
    global _broadcast_task
    if _broadcast_task is None or _broadcast_task.done():
        _broadcast_task = asyncio.create_task(_status_broadcast_loop())
        logger.info("WebSocket status broadcast started")


async def stop_broadcast():
    """Stop the background status broadcast loop."""
    global _broadcast_task
    if _broadcast_task is not None:
        _broadcast_task.cancel()
        try:
            await _broadcast_task
        except asyncio.CancelledError:
            pass
        _broadcast_task = None
        logger.info("WebSocket status broadcast stopped")


@router.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """WebSocket endpoint for live status updates.

    Accepts any connection (no auth -- field crew access).
    Uses two concurrent tasks:
    - sender: drains the per-client queue and sends messages
    - receiver: reads client messages (ping/pong)
    """
    await ws.accept()

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _client_queues[ws] = queue
    logger.info("WebSocket client connected (%d total)", len(_client_queues))

    async def sender():
        """Send queued messages to the client."""
        try:
            while True:
                msg = await queue.get()
                await ws.send_text(msg)
        except Exception as exc:
            logger.warning("WS sender error: %s: %s", type(exc).__name__, exc)

    async def receiver():
        """Read client messages."""
        try:
            while True:
                data = await ws.receive()
                if data["type"] == "websocket.disconnect":
                    break
                if data["type"] == "websocket.receive":
                    raw = data.get("text", "")
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") == "ping":
                        queue.put_nowait(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("WS receiver error: %s: %s", type(exc).__name__, exc)

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())

    try:
        # Wait for either task to finish (receiver finishes on disconnect)
        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        _client_queues.pop(ws, None)
        logger.info("WebSocket client disconnected (%d remaining)", len(_client_queues))
