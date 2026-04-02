"""
NTRIP push client: send RTCM3 corrections to a remote NTRIP caster.

Supports NTRIP 1.0 SOURCE protocol (used by Emlid Caster, RTK2Go, etc.):
  SOURCE <password> /<mountpoint>\r\n

Implements the RTCMOutput protocol. Auto-reconnects on disconnect.
"""

import asyncio
import logging

logger = logging.getLogger("survey365.gnss.ntrip_push")


class NTRIPPush:
    """Push RTCM3 data to a remote NTRIP caster."""

    name: str = "ntrip_push"

    def __init__(
        self,
        host: str,
        port: int,
        mountpoint: str,
        password: str,
    ):
        self._host = host
        self._port = port
        self._mountpoint = mountpoint
        self._password = password
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._reconnect_task: asyncio.Task | None = None
        self._bytes_sent = 0

    async def connect(self):
        """Connect to the remote NTRIP caster and send SOURCE request."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=10.0,
            )

            # NTRIP 1.0 SOURCE request
            source_req = f"SOURCE {self._password} /{self._mountpoint}\r\n"
            source_req += f"Source-Agent: Survey365/1.0\r\n"
            source_req += "\r\n"
            writer.write(source_req.encode())
            await writer.drain()

            # Read response (expect "ICY 200 OK")
            response = await asyncio.wait_for(reader.readline(), timeout=10.0)
            response_str = response.decode().strip()

            if "200" in response_str:
                self._writer = writer
                self._connected = True
                self._bytes_sent = 0
                logger.info(
                    "NTRIP push connected to %s:%d/%s",
                    self._host, self._port, self._mountpoint,
                )
            else:
                writer.close()
                logger.error("NTRIP caster rejected connection: %s", response_str)

        except Exception as exc:
            logger.warning("NTRIP push connect failed: %s", exc)
            self._connected = False

    async def write(self, data: bytes) -> None:
        """Send RTCM3 data to the caster. Reconnect on failure."""
        if not self._connected or self._writer is None:
            # Try to reconnect (non-blocking)
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect())
            return

        try:
            self._writer.write(data)
            await self._writer.drain()
            self._bytes_sent += len(data)
        except Exception as exc:
            logger.warning("NTRIP push write error: %s", exc)
            self._connected = False
            self._writer = None
            # Schedule reconnect
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect())

    async def close(self) -> None:
        """Disconnect from the caster."""
        self._connected = False
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

        logger.info(
            "NTRIP push disconnected from %s:%d/%s (%d bytes sent)",
            self._host, self._port, self._mountpoint, self._bytes_sent,
        )

    async def _reconnect(self):
        """Reconnect with backoff."""
        await asyncio.sleep(5.0)
        if not self._connected:
            await self.connect()
