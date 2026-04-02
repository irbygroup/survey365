"""
WebSocket endpoint for live GNSS and mode updates.

Uses a simple send-loop pattern: the handler accepts the connection,
then loops sending status every second. Client messages are read
non-blockingly between sends.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger("survey365.ws")

router = APIRouter()

_clients: set[WebSocket] = set()


async def broadcast_event(event: dict):
    """Broadcast a JSON event to all connected WebSocket clients."""
    if not _clients:
        return
    message = json.dumps(event)
    dead = set()
    for ws in list(_clients):
        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


_broadcast_task: asyncio.Task | None = None


async def _status_broadcast_loop():
    """Background loop: broadcast GNSS status to all clients every 1 second."""

    while True:
        try:
            if _clients:
                from ..routes.status import build_status_payload

                payload = await build_status_payload()
                message = json.dumps({
                    "type": "status",
                    **payload,
                })

                dead = set()
                for ws in list(_clients):
                    try:
                        if ws.client_state == WebSocketState.CONNECTED:
                            await ws.send_text(message)
                    except Exception:
                        dead.add(ws)
                _clients.difference_update(dead)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Status broadcast error: %s", exc)

        await asyncio.sleep(1.0)


def start_broadcast():
    global _broadcast_task
    if _broadcast_task is None or _broadcast_task.done():
        _broadcast_task = asyncio.create_task(_status_broadcast_loop())
        logger.info("WebSocket status broadcast started")


async def stop_broadcast():
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

    Simple pattern: accept, add to client set, keep alive by reading.
    The broadcast loop handles sending to all clients.
    """
    await ws.accept()
    _clients.add(ws)
    client_count = len(_clients)
    logger.info("WebSocket client connected (%d total)", client_count)

    try:
        # Just keep reading — this keeps the connection alive.
        # The broadcast loop sends status messages directly.
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            # Handle ping from client
            if msg["type"] == "websocket.receive":
                text = msg.get("text", "")
                if text:
                    try:
                        parsed = json.loads(text)
                        if parsed.get("type") == "ping":
                            await ws.send_text(json.dumps({"type": "pong"}))
                    except (json.JSONDecodeError, Exception):
                        pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket error: %s: %s", type(exc).__name__, exc)
    finally:
        _clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_clients))
