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
        self._queue: asyncio.Queue[bytes] | None = None
        self._publish_task: asyncio.Task | None = None
        self._running = False
        self._dropped_chunks = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        if self._server is not None:
            return
        self._running = True
        self._queue = asyncio.Queue(maxsize=512)
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self._publish_task = asyncio.create_task(self._publish_loop())
        logger.info("Raw GNSS relay listening on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        self._running = False
        if self._publish_task is not None:
            self._publish_task.cancel()
            try:
                await self._publish_task
            except asyncio.CancelledError:
                pass
            self._publish_task = None
        for writer in list(self._clients):
            await self._close_client(writer)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._queue = None
        logger.info("Raw GNSS relay stopped")

    def publish_nowait(self, data: bytes) -> None:
        if not data or not self._running or self._queue is None:
            return
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            self._dropped_chunks += 1
            if self._dropped_chunks == 1 or self._dropped_chunks % 100 == 0:
                logger.warning("Raw GNSS relay queue full; dropped %d chunks", self._dropped_chunks)

    async def _publish_loop(self) -> None:
        while self._running:
            try:
                data = await self._queue.get()
            except asyncio.CancelledError:
                break
            await self._broadcast(data)

    async def _broadcast(self, data: bytes) -> None:
        """Write data to all clients, then drain all at once.

        Uses a write-all-then-drain pattern so one slow client does not
        block writes to the others.  Expected consumer count is <=3
        (local caster, outbound, log) so this is sufficient.
        """
        dead: list[asyncio.StreamWriter] = []
        # Phase 1: buffer the write to every client (non-blocking)
        for writer in list(self._clients):
            try:
                writer.write(data)
            except Exception:
                dead.append(writer)
        # Phase 2: drain all clients concurrently
        drain_tasks = []
        for writer in list(self._clients):
            if writer in dead:
                continue
            drain_tasks.append(self._drain_or_mark_dead(writer, dead))
        if drain_tasks:
            await asyncio.gather(*drain_tasks)
        for writer in dead:
            await self._close_client(writer)

    @staticmethod
    async def _drain_or_mark_dead(
        writer: asyncio.StreamWriter,
        dead: list[asyncio.StreamWriter],
    ) -> None:
        try:
            await asyncio.wait_for(writer.drain(), timeout=2.0)
        except Exception:
            dead.append(writer)

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
