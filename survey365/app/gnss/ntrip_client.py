"""
NTRIP client: receive RTCM3 corrections from a remote CORS/VRS caster.

Used in establish mode (CORS RTK fix) and future rover mode.
Connects as an NTRIP 1.0 client, reads RTCM3 stream, and feeds
corrections into the GNSSManager for injection into the receiver.
"""

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("survey365.gnss.ntrip_client")


class NTRIPClient:
    """NTRIP client for receiving RTCM3 corrections from a remote caster."""

    def __init__(
        self,
        host: str,
        port: int,
        mountpoint: str,
        username: str = "",
        password: str = "",
        on_rtcm: Callable[[bytes], Awaitable[None]] | None = None,
        gga_provider: Callable[[], Awaitable[str | None]] | None = None,
    ):
        """
        Args:
            host: NTRIP caster hostname
            port: NTRIP caster port
            mountpoint: Mount point name
            username: Authentication username
            password: Authentication password
            on_rtcm: Callback for received RTCM3 data: async (bytes) -> None
            gga_provider: Callback to get current GGA sentence: async () -> str | None
        """
        self._host = host
        self._port = port
        self._mountpoint = mountpoint
        self._username = username
        self._password = password
        self._on_rtcm = on_rtcm
        self._gga_provider = gga_provider
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._gga_task: asyncio.Task | None = None
        self._running = False
        self._connected = False
        self._bytes_received = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self):
        """Start the NTRIP client (connects and reads in background)."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("NTRIP client started for %s:%d/%s", self._host, self._port, self._mountpoint)

    async def stop(self):
        """Stop the NTRIP client."""
        self._running = False
        self._connected = False

        if self._gga_task is not None:
            self._gga_task.cancel()
            try:
                await self._gga_task
            except asyncio.CancelledError:
                pass
            self._gga_task = None

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self._disconnect()
        logger.info(
            "NTRIP client stopped (%d bytes received from %s/%s)",
            self._bytes_received, self._host, self._mountpoint,
        )

    async def _run_loop(self):
        """Main loop: connect, read, reconnect on error."""
        while self._running:
            try:
                await self._connect_and_read()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("NTRIP client error: %s. Reconnecting in 5s...", exc)
                self._connected = False

            if self._running:
                await asyncio.sleep(5.0)

    async def _connect_and_read(self):
        """Connect to NTRIP caster, authenticate, read RTCM3 stream."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=15.0,
        )

        # Build NTRIP request
        auth = ""
        if self._username or self._password:
            credentials = base64.b64encode(f"{self._username}:{self._password}".encode()).decode()
            auth = f"Authorization: Basic {credentials}\r\n"

        request = (
            f"GET /{self._mountpoint} HTTP/1.1\r\n"
            f"Host: {self._host}\r\n"
            f"Ntrip-Version: Ntrip/1.0\r\n"
            f"User-Agent: NTRIP Survey365/1.0\r\n"
            f"{auth}"
            f"\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        # Read response
        response_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        response_str = response_line.decode("ascii", errors="replace").strip()

        if "200" not in response_str:
            writer.close()
            raise ConnectionError(f"NTRIP caster rejected: {response_str}")

        # Read remaining headers
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line.strip() == b"":
                break

        self._reader = reader
        self._writer = writer
        self._connected = True
        logger.info("NTRIP client connected to %s:%d/%s", self._host, self._port, self._mountpoint)

        # Start GGA feedback task (VRS casters need periodic position reports)
        if self._gga_provider is not None:
            self._gga_task = asyncio.create_task(self._gga_feedback_loop())

        try:
            while self._running:
                data = await asyncio.wait_for(reader.read(4096), timeout=30.0)
                if not data:
                    logger.warning("NTRIP stream closed by caster")
                    break

                self._bytes_received += len(data)

                if self._on_rtcm is not None:
                    await self._on_rtcm(data)
        finally:
            self._connected = False
            await self._disconnect()

    async def _gga_feedback_loop(self):
        """Send GGA position updates to VRS casters every 10 seconds."""
        while self._running and self._connected:
            try:
                await asyncio.sleep(10.0)

                if self._gga_provider is not None and self._writer is not None:
                    gga = await self._gga_provider()
                    if gga:
                        self._writer.write((gga + "\r\n").encode())
                        await self._writer.drain()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("GGA feedback error: %s", exc)

    async def _disconnect(self):
        """Close the TCP connection."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        self._reader = None
