"""
Local NTRIP caster proxy for RTKLIB's internal local caster.

Survey365 keeps the external LAN-facing socket so rover metadata and GGA traffic
remain visible in the admin API, while RTKLIB remains the encoder and upstream
stream source.
"""

import asyncio
import logging
from datetime import datetime, timezone
from itertools import count

logger = logging.getLogger("survey365.gnss.ntrip_caster")

MAX_CAPTURE_TEXT = 65536
MAX_CAPTURE_LINES = 200
MAX_CAPTURE_EVENTS = 200
MAX_CAPTURE_NMEA = 100


class NTRIPCaster:
    """Transparent reverse proxy for an internal RTKLIB NTRIP caster."""

    name: str = "local_caster"

    def __init__(
        self,
        port: int = 2101,
        mountpoint: str = "SURVEY365",
        upstream_host: str = "127.0.0.1",
        upstream_port: int = 2110,
    ):
        self._port = port
        self._mountpoint = mountpoint
        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self._server: asyncio.Server | None = None
        self._running = False
        self._bytes_served = 0
        self._client_sessions: dict[asyncio.StreamWriter, dict] = {}
        self._session_history: list[dict] = []
        self._session_ids = count(1)
        self._upstream_active = False
        self._last_proxy_error: str | None = None

    async def start(self):
        self._running = True
        self._server = await asyncio.start_server(self._handle_client, "0.0.0.0", self._port)
        logger.info(
            "Local NTRIP proxy listening on port %d -> %s:%d/%s",
            self._port,
            self._upstream_host,
            self._upstream_port,
            self._mountpoint,
        )

    async def close(self) -> None:
        self._running = False
        for writer in list(self._client_sessions):
            await self._close_client(writer)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("Local NTRIP proxy stopped")

    def snapshot_clients(self) -> dict:
        active = [self._snapshot_session(session) for session in self._client_sessions.values()]
        recent = [self._snapshot_session(session) for session in self._session_history]
        return {
            "running": self._running and self._upstream_active,
            "port": self._port,
            "mountpoint": self._mountpoint,
            "bytes_served": self._bytes_served,
            "active_clients": active,
            "recent_clients": recent,
            "upstream_active": self._upstream_active,
            "upstream_port": self._upstream_port,
            "last_proxy_error": self._last_proxy_error,
        }

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername", ("unknown", 0))
        session = self._new_session(peer)
        self._client_sessions[writer] = session
        logger.info("NTRIP proxy client connected from %s:%d", peer[0], peer[1])

        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            request_str = request_line.decode("ascii", errors="replace").strip()
            session["request_line"] = request_str
            session["raw_request"] += request_line.decode("ascii", errors="replace")
            self._append_event(session, {"type": "request_line", "value": request_str})

            headers: list[str] = []
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line.strip() == b"":
                    session["raw_request"] += "\r\n"
                    break
                header = line.decode("ascii", errors="replace").strip()
                headers.append(header)
                session["request_headers"].append(header)
                session["raw_request"] += line.decode("ascii", errors="replace")
                self._append_event(session, {"type": "header", "value": header})

            parts = request_str.split()
            if len(parts) < 2:
                await self._send_error(writer, 400, "Bad Request")
                return

            method, path = parts[0], parts[1]
            session["method"] = method
            session["path"] = path

            if method != "GET":
                await self._send_error(writer, 405, "Method Not Allowed")
                return

            if path == "/":
                await self._proxy_source_table(writer)
                return

            if path.lstrip("/") == self._mountpoint:
                await self._proxy_stream(reader, writer, headers, session)
                return

            await self._send_error(writer, 404, "Not Found")
        except asyncio.TimeoutError:
            self._last_proxy_error = "client handshake timed out"
        except Exception as exc:
            self._last_proxy_error = str(exc)
            logger.debug("NTRIP proxy client error: %s", exc, exc_info=True)
        finally:
            await self._close_client(writer)

    async def _proxy_source_table(self, writer: asyncio.StreamWriter) -> None:
        upstream_reader, upstream_writer = await self._open_upstream("/")
        try:
            while True:
                chunk = await upstream_reader.read(4096)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
            self._upstream_active = True
        finally:
            upstream_writer.close()
            await upstream_writer.wait_closed()

    async def _proxy_stream(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        request_headers: list[str],
        session: dict,
    ) -> None:
        upstream_reader, upstream_writer = await self._open_upstream(f"/{self._mountpoint}", request_headers)
        status_line = await asyncio.wait_for(upstream_reader.readline(), timeout=10.0)
        client_writer.write(status_line)

        while True:
            header_line = await asyncio.wait_for(upstream_reader.readline(), timeout=5.0)
            client_writer.write(header_line)
            if header_line.strip() == b"":
                break
        await client_writer.drain()

        session["streaming"] = True
        session["streaming_started_at"] = _utc_now()
        self._upstream_active = True

        async def upstream_to_client():
            while self._running:
                chunk = await upstream_reader.read(4096)
                if not chunk:
                    break
                client_writer.write(chunk)
                await client_writer.drain()
                self._bytes_served += len(chunk)
                session["bytes_served"] += len(chunk)

        async def client_to_upstream():
            while self._running:
                try:
                    data = await asyncio.wait_for(client_reader.read(4096), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    break
                self._capture_incoming(session, data)
                upstream_writer.write(data)
                await upstream_writer.drain()

        tasks = [
            asyncio.create_task(upstream_to_client()),
            asyncio.create_task(client_to_upstream()),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            upstream_writer.close()
            await upstream_writer.wait_closed()

    async def _open_upstream(
        self,
        path: str,
        request_headers: list[str] | None = None,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self._upstream_host,
                self._upstream_port,
            )
        except Exception as exc:
            self._upstream_active = False
            self._last_proxy_error = str(exc)
            raise

        request = [f"GET {path} HTTP/1.0", "User-Agent: Survey365/1.0"]
        for header in request_headers or []:
            lower = header.lower()
            if lower.startswith("host:") or lower.startswith("user-agent:"):
                continue
            request.append(header)
        request.append("")
        request.append("")
        upstream_writer.write("\r\n".join(request).encode("ascii", errors="replace"))
        await upstream_writer.drain()
        return upstream_reader, upstream_writer

    async def _send_error(self, writer: asyncio.StreamWriter, code: int, message: str):
        writer.write(f"HTTP/1.1 {code} {message}\r\n\r\n".encode())
        await writer.drain()

    async def _close_client(self, writer: asyncio.StreamWriter) -> None:
        session = self._client_sessions.pop(writer, None)
        if session is not None:
            session["disconnected_at"] = _utc_now()
            self._session_history.insert(0, self._snapshot_session(session))
            del self._session_history[20:]
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    def _new_session(self, peer: tuple) -> dict:
        return {
            "client_id": next(self._session_ids),
            "peer_host": peer[0],
            "peer_port": peer[1],
            "connected_at": _utc_now(),
            "disconnected_at": None,
            "streaming": False,
            "streaming_started_at": None,
            "method": None,
            "path": None,
            "request_line": "",
            "request_headers": [],
            "raw_request": "",
            "bytes_received": 0,
            "bytes_served": 0,
            "incoming_text": "",
            "incoming_lines": [],
            "incoming_events": [],
            "nmea_sentences": [],
            "last_gga": None,
            "gga_messages": [],
            "_line_buffer": "",
        }

    def _capture_incoming(self, session: dict, data: bytes) -> None:
        session["bytes_received"] += len(data)
        text = data.decode("ascii", errors="replace")
        session["incoming_text"] = _trim_text(session["incoming_text"] + text)
        self._append_event(session, {"type": "data", "bytes": len(data), "text": text, "hex": data.hex()})

        line_buffer = session["_line_buffer"] + text
        lines = line_buffer.splitlines(keepends=True)
        complete_lines: list[str] = []
        if lines and not lines[-1].endswith(("\r", "\n")):
            session["_line_buffer"] = lines.pop()
        else:
            session["_line_buffer"] = ""

        for line in lines:
            clean = line.rstrip("\r\n")
            if clean:
                complete_lines.append(clean)

        for line in complete_lines:
            session["incoming_lines"].append(line)
            del session["incoming_lines"][:-MAX_CAPTURE_LINES]
            if line.startswith("$"):
                session["nmea_sentences"].append(line)
                del session["nmea_sentences"][:-MAX_CAPTURE_NMEA]
                parsed = _parse_nmea(line)
                self._append_event(session, {"type": "nmea", "sentence": line, "parsed": parsed})
                if parsed and parsed.get("type") == "GGA":
                    session["last_gga"] = parsed
                    session["gga_messages"].append(parsed)
                    del session["gga_messages"][:-MAX_CAPTURE_NMEA]

    def _append_event(self, session: dict, event: dict) -> None:
        session["incoming_events"].append({"timestamp": _utc_now(), **event})
        del session["incoming_events"][:-MAX_CAPTURE_EVENTS]

    def _snapshot_session(self, session: dict) -> dict:
        return {
            "client_id": session["client_id"],
            "peer_host": session["peer_host"],
            "peer_port": session["peer_port"],
            "connected_at": session["connected_at"],
            "disconnected_at": session["disconnected_at"],
            "streaming": session["streaming"],
            "streaming_started_at": session["streaming_started_at"],
            "method": session["method"],
            "path": session["path"],
            "request_line": session["request_line"],
            "request_headers": list(session["request_headers"]),
            "raw_request": session["raw_request"],
            "bytes_received": session["bytes_received"],
            "bytes_served": session["bytes_served"],
            "incoming_text": session["incoming_text"],
            "incoming_lines": list(session["incoming_lines"]),
            "incoming_events": list(session["incoming_events"]),
            "nmea_sentences": list(session["nmea_sentences"]),
            "last_gga": session["last_gga"],
            "gga_messages": list(session["gga_messages"]),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_text(text: str) -> str:
    if len(text) <= MAX_CAPTURE_TEXT:
        return text
    return text[-MAX_CAPTURE_TEXT:]


def _parse_nmea(sentence: str) -> dict | None:
    body = sentence.strip()
    if not body.startswith("$"):
        return None
    if "*" in body:
        body = body[1:body.index("*")]
    else:
        body = body[1:]
    parts = body.split(",")
    if not parts:
        return None

    message_type = parts[0][-3:]
    if message_type != "GGA" or len(parts) < 10:
        return {"type": message_type, "raw": sentence}

    return {
        "type": "GGA",
        "raw": sentence,
        "time": parts[1],
        "latitude": _parse_nmea_coord(parts[2], parts[3]),
        "longitude": _parse_nmea_coord(parts[4], parts[5]),
        "quality": parts[6],
        "satellites": parts[7],
        "hdop": parts[8],
        "altitude_m": parts[9],
    }


def _parse_nmea_coord(value: str, hemisphere: str) -> float | None:
    if not value:
        return None
    try:
        dot = value.index(".")
        degrees_len = dot - 2
        degrees = float(value[:degrees_len])
        minutes = float(value[degrees_len:])
        decimal = degrees + (minutes / 60.0)
        if hemisphere in {"S", "W"}:
            decimal *= -1
        return decimal
    except Exception:
        return None
