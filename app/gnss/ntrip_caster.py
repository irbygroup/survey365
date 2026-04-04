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
from itertools import count

logger = logging.getLogger("survey365.gnss.ntrip_caster")

MAX_CAPTURE_TEXT = 65536
MAX_CAPTURE_LINES = 200
MAX_CAPTURE_EVENTS = 200
MAX_CAPTURE_NMEA = 100


class NTRIPCaster:
    """Local NTRIP server serving RTCM3 corrections to connected clients."""

    name: str = "local_caster"

    def __init__(
        self,
        port: int = 2101,
        mountpoint: str = "SURVEY365",
        latitude: float | None = None,
        longitude: float | None = None,
        password: str = "",
    ):
        self._port = port
        self._mountpoint = mountpoint
        self._latitude = latitude
        self._longitude = longitude
        self._password = password
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._client_sessions: dict[asyncio.StreamWriter, dict] = {}
        self._session_history: list[dict] = []
        self._session_ids = count(1)
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
                session = self._client_sessions.get(client)
                if session is not None:
                    session["bytes_served"] += len(data)
            except Exception:
                dead.add(client)

        for client in dead:
            await self._close_client(client)
        if dead:
            logger.info("Removed %d dead NTRIP clients (%d remaining)", len(dead), len(self._clients))

    async def close(self) -> None:
        """Shut down the caster and disconnect all clients."""
        self._running = False

        # Close all clients
        for client in list(self._clients):
            await self._close_client(client)
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
        session = self._new_session(peer)
        self._client_sessions[writer] = session

        try:
            # Read the HTTP request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            request_str = request_line.decode("ascii", errors="replace").strip()
            session["request_line"] = request_str
            session["raw_request"] += request_line.decode("ascii", errors="replace")
            self._append_event(session, {"type": "request_line", "value": request_str})

            # Read remaining headers (until empty line)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line.strip() == b"":
                    session["raw_request"] += "\r\n"
                    break
                header = line.decode("ascii", errors="replace").strip()
                session["request_headers"].append(header)
                session["raw_request"] += line.decode("ascii", errors="replace")
                self._append_event(session, {"type": "header", "value": header})

            # Parse request
            parts = request_str.split()
            if len(parts) < 2:
                await self._send_error(writer, 400, "Bad Request")
                return

            method, path = parts[0], parts[1]
            session["method"] = method
            session["path"] = path

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
            await self._close_client(writer)

    async def _send_source_table(self, writer: asyncio.StreamWriter):
        """Send NTRIP source table response."""
        now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M:%S UTC")
        lat = f"{self._latitude:.8f}" if self._latitude is not None else "0"
        lon = f"{self._longitude:.8f}" if self._longitude is not None else "0"
        body = (
            f"STR;{self._mountpoint};Survey365;RTCM 3.3;;2;GPS+GLO+GAL+BDS;"
            f"Survey365;USA;{lat};{lon};0;0;none;B;N;0;\r\n"
        )
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
        session = self._client_sessions.get(writer)
        if session is not None:
            session["streaming"] = True
            session["streaming_started_at"] = _utc_now()
        logger.info("NTRIP client %s:%d streaming from /%s", peer[0], peer[1], self._mountpoint)

        # Keep connection alive until client disconnects or server stops
        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                    if not data:
                        break  # Client disconnected
                    self._capture_incoming(writer, data)
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

    def snapshot_clients(self) -> dict:
        """Return active and recent NTRIP client session details."""
        active = [
            self._snapshot_session(session)
            for session in self._client_sessions.values()
        ]
        recent = [self._snapshot_session(session) for session in self._session_history]
        return {
            "running": self._running,
            "port": self._port,
            "mountpoint": self._mountpoint,
            "bytes_served": self._bytes_served,
            "active_clients": active,
            "recent_clients": recent,
        }

    async def _close_client(self, writer: asyncio.StreamWriter) -> None:
        self._clients.discard(writer)
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

    def _capture_incoming(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        session = self._client_sessions.get(writer)
        if session is None:
            return

        session["bytes_received"] += len(data)
        text = data.decode("ascii", errors="replace")
        session["incoming_text"] = _trim_text(session["incoming_text"] + text)
        self._append_event(session, {
            "type": "data",
            "bytes": len(data),
            "text": text,
            "hex": data.hex(),
        })

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

    message = parts[0]
    talker = message[:2] if len(message) >= 2 else ""
    msg_type = message[-3:] if len(message) >= 3 else message
    parsed = {
        "message": message,
        "talker": talker,
        "type": msg_type,
        "fields": parts[1:],
    }
    if msg_type == "GGA":
        parsed.update(_parse_gga_fields(parts))
    return parsed


def _parse_gga_fields(parts: list[str]) -> dict:
    lat = _parse_nmea_latlon(parts[2] if len(parts) > 2 else "", parts[3] if len(parts) > 3 else "", 2)
    lon = _parse_nmea_latlon(parts[4] if len(parts) > 4 else "", parts[5] if len(parts) > 5 else "", 3)
    return {
        "utc_time": parts[1] if len(parts) > 1 else "",
        "latitude": lat,
        "longitude": lon,
        "fix_quality": parts[6] if len(parts) > 6 else "",
        "satellites": parts[7] if len(parts) > 7 else "",
        "hdop": parts[8] if len(parts) > 8 else "",
        "altitude_m": _parse_float(parts[9] if len(parts) > 9 else ""),
        "altitude_units": parts[10] if len(parts) > 10 else "",
        "geoid_separation_m": _parse_float(parts[11] if len(parts) > 11 else ""),
        "geoid_units": parts[12] if len(parts) > 12 else "",
        "age_of_differential": parts[13] if len(parts) > 13 else "",
        "reference_station_id": parts[14] if len(parts) > 14 else "",
    }


def _parse_nmea_latlon(value: str, hemisphere: str, degree_width: int) -> float | None:
    if not value:
        return None
    try:
        degrees = float(value[:degree_width])
        minutes = float(value[degree_width:])
    except ValueError:
        return None
    decimal = degrees + (minutes / 60.0)
    if hemisphere in {"S", "W"}:
        decimal *= -1
    return decimal


def _parse_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
