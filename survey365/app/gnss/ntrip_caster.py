"""
Local NTRIP caster: serve RTCM3 corrections to LAN rovers.

Implements a minimal NTRIP 1.0 server:
  - GET / → source table
  - GET /MOUNTPOINT → RTCM3 stream
  - Tracks connected clients

Also implements the RTCMOutput protocol for the RTCM fan-out.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("survey365.gnss.ntrip_caster")


class NTRIPCaster:
    """Local NTRIP server serving RTCM3 corrections to connected clients."""

    name: str = "local_caster"

    def __init__(
        self,
        port: int = 2101,
        mountpoint: str = "SURVEY365",
        password: str = "",
    ):
        self._port = port
        self._mountpoint = mountpoint
        self._password = password
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._latest_data: bytes = b""
        self._running = False
        self._bytes_served = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self):
        """Start the NTRIP caster server."""
        self._running = True
        self._server = await asyncio.start_server(
            self._handle_client,
            "0.0.0.0",
            self._port,
        )
        logger.info("NTRIP caster listening on port %d (mountpoint: %s)", self._port, self._mountpoint)

    async def write(self, data: bytes) -> None:
        """Broadcast RTCM3 data to all connected NTRIP clients."""
        self._latest_data = data
        dead = set()
        for client in list(self._clients):
            try:
                client.write(data)
                await client.drain()
                self._bytes_served += len(data)
            except Exception:
                dead.add(client)

        for client in dead:
            self._clients.discard(client)
            try:
                client.close()
            except Exception:
                pass
        if dead:
            logger.info("Removed %d dead NTRIP clients (%d remaining)", len(dead), len(self._clients))

    async def close(self) -> None:
        """Shut down the caster and disconnect all clients."""
        self._running = False

        # Close all clients
        for client in list(self._clients):
            try:
                client.close()
                await client.wait_closed()
            except Exception:
                pass
        self._clients.clear()

        # Stop server
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("NTRIP caster stopped (%d bytes served)", self._bytes_served)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle an incoming NTRIP client connection."""
        peer = writer.get_extra_info("peername", ("unknown", 0))
        logger.info("NTRIP client connected from %s:%d", peer[0], peer[1])

        try:
            # Read the HTTP request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            request_str = request_line.decode("ascii", errors="replace").strip()

            # Read remaining headers (until empty line)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line.strip() == b"":
                    break

            # Parse request
            parts = request_str.split()
            if len(parts) < 2:
                await self._send_error(writer, 400, "Bad Request")
                return

            method, path = parts[0], parts[1]

            if method == "GET" and path == "/":
                # Source table request
                await self._send_source_table(writer)
                return

            if method == "GET" and path.lstrip("/") == self._mountpoint:
                # Stream RTCM3 corrections
                await self._stream_corrections(reader, writer, peer)
                return

            await self._send_error(writer, 404, "Not Found")

        except asyncio.TimeoutError:
            logger.debug("NTRIP client %s timed out during handshake", peer[0])
        except Exception as exc:
            logger.debug("NTRIP client %s error: %s", peer[0], exc)
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _send_source_table(self, writer: asyncio.StreamWriter):
        """Send NTRIP source table response."""
        now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M:%S UTC")
        body = f"STR;{self._mountpoint};Survey365;RTCM 3.3;;2;GPS+GLO+GAL+BDS;Survey365;USA;0;0;0;0;none;B;N;0;\r\n"
        body += "ENDSOURCETABLE\r\n"

        response = (
            f"SOURCETABLE 200 OK\r\n"
            f"Server: Survey365/1.0\r\n"
            f"Date: {now}\r\n"
            f"Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(response.encode())
        await writer.drain()

    async def _stream_corrections(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: tuple):
        """Stream RTCM3 data to an NTRIP client."""
        # Send 200 OK
        response = (
            "ICY 200 OK\r\n"
            "Server: Survey365/1.0\r\n"
            "Content-Type: application/octet-stream\r\n"
            "\r\n"
        )
        writer.write(response.encode())
        await writer.drain()

        # Add to client set — broadcast() will push data
        self._clients.add(writer)
        logger.info("NTRIP client %s:%d streaming from /%s", peer[0], peer[1], self._mountpoint)

        # Keep connection alive until client disconnects or server stops
        try:
            while self._running:
                # Check if client is still connected by reading (should block)
                try:
                    data = await asyncio.wait_for(reader.read(1), timeout=60.0)
                    if not data:
                        break  # Client disconnected
                except asyncio.TimeoutError:
                    continue  # Keep alive
        except Exception:
            pass

        logger.info("NTRIP client %s:%d disconnected", peer[0], peer[1])

    async def _send_error(self, writer: asyncio.StreamWriter, code: int, message: str):
        """Send an HTTP error response."""
        response = f"HTTP/1.1 {code} {message}\r\n\r\n"
        writer.write(response.encode())
        await writer.drain()
