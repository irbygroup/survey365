"""
WebSocket endpoint for live GNSS and mode updates.

Sends:
- "status" messages every 1 second with GNSS state
- "mode_change" messages immediately when mode transitions occur
- "establish_progress" messages during relative base averaging
- "pong" in response to client "ping"

Maintains a set of connected WebSocket clients. Broadcast functions
are called by the mode routes when transitions happen.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..gnss import gnss_state

logger = logging.getLogger("survey365.ws")

router = APIRouter()

# Connected WebSocket clients
_clients: set[WebSocket] = set()
_broadcast_lock = asyncio.Lock()


async def broadcast_event(event: dict):
    """Broadcast a JSON event to all connected WebSocket clients.

    Called by mode routes for mode_change and establish_progress events.
    """
    if not _clients:
        return

    message = json.dumps(event)
    disconnected = set()

    async with _broadcast_lock:
        for ws in _clients:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(ws)

    # Clean up disconnected clients outside the broadcast lock
    _clients.difference_update(disconnected)


async def _status_broadcast_loop():
    """Background loop: broadcast GNSS status to all clients every 1 second."""
    while True:
        try:
            if _clients:
                # Import here to avoid circular import at module level
                from ..routes.mode import get_mode_state
                from ..rtkbase import get_service_status

                gnss = await gnss_state.snapshot()
                mode_state = get_mode_state()

                # Only fetch service status every 5 seconds to reduce subprocess calls
                # Use a simple counter approach
                if not hasattr(_status_broadcast_loop, "_tick"):
                    _status_broadcast_loop._tick = 0
                _status_broadcast_loop._tick += 1

                if _status_broadcast_loop._tick % 5 == 1:
                    services = await get_service_status()
                    _status_broadcast_loop._last_services = services
                else:
                    services = getattr(_status_broadcast_loop, "_last_services", {})

                message = {
                    "type": "status",
                    "gnss": gnss,
                    "mode": mode_state["mode"],
                    "mode_label": mode_state["mode_label"],
                    "services": services,
                }

                await broadcast_event(message)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Status broadcast error: %s", exc)

        await asyncio.sleep(1.0)


# Reference to the background broadcast task
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
    Clients can send {"type": "ping"} and receive {"type": "pong"}.
    """
    await ws.accept()
    _clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_clients))

    try:
        while True:
            # Read client messages (ping/pong, subscribe, etc.)
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a keepalive ping if no message received in 30s
                try:
                    await ws.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket client error: %s", exc)
    finally:
        _clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_clients))
