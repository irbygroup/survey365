"""
Loopback TCP relay for the raw GNSS receiver stream.
"""

import asyncio
import logging

logger = logging.getLogger("survey365.gnss.raw_relay")


class RawRelay:
    def __init__(self, host: str = "127.0.0.1", port: int = 5015):
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._running = False

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        if self._server is not None:
            return
        self._running = True
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        logger.info("Raw GNSS relay listening on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        self._running = False
        for writer in list(self._clients):
            await self._close_client(writer)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("Raw GNSS relay stopped")

    async def publish(self, data: bytes) -> None:
        dead: list[asyncio.StreamWriter] = []
        for writer in list(self._clients):
            try:
                writer.write(data)
                await writer.drain()
            except Exception:
                dead.append(writer)
        for writer in dead:
            await self._close_client(writer)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self._clients.add(writer)
        logger.info("Raw relay client connected: %s", peer)
        try:
            while self._running:
                data = await reader.read(1024)
                if not data:
                    break
                # Loopback relay is publish-only. Ignore inbound data.
        except Exception:
            pass
        finally:
            await self._close_client(writer)
            logger.info("Raw relay client disconnected: %s", peer)

    async def _close_client(self, writer: asyncio.StreamWriter) -> None:
        self._clients.discard(writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
