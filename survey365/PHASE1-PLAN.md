Now I have all the context I need. Let me produce the comprehensive implementation plan.

---

# Survey365 Phase 1 -- Detailed Implementation Plan

## Phase 1 Scope Recap

From the PRD: "Map UI + status + known point base + relative base + point DB + simple password auth -- Field-usable base station with map interface"

This means we are building:
- Full-screen MapLibre map as primary interface
- GNSS status display (satellite count, fix type, position) read from TCP:5015
- Known Point Base mode (write coords to RTKBase `settings.conf`, restart services)
- Relative Base mode (average position for 120s, then broadcast)
- Sites CRUD with SpatiaLite proximity queries
- Simple password auth (session cookie, admin-only gating)
- Config read/write (MapTiler key, base position, password)
- WebSocket for live status updates
- Nginx reverse proxy, systemd service, install script

What is explicitly OUT of scope for Phase 1: CORS establish, OPUS, rover mode, multi-rover tracking, file import (KML/DXF/DWG), cell hotspot, modem management, WiFi management, Cloudflare tunnel, system management (reboot/shutdown/update), offline tiles, PWA.

---

## 1. Project Scaffolding

### 1.1 File Structure

All files live under `/Users/jaredirby/projects/rtk-surverying/survey365/`:

```
survey365/
  PRD.md                          # Already exists
  requirements.txt                # Python deps
  app/
    __init__.py                   # Empty
    main.py                       # FastAPI app entry point
    db.py                         # SQLite + SpatiaLite connection helper
    config.py                     # App config loader (reads config table)
    auth.py                       # Password hashing + session cookie middleware
    gnss.py                       # UBX parser, reads TCP:5015 stream
    rtkbase.py                    # Read/write ~/rtkbase/settings.conf + systemctl
    models.py                     # Pydantic models for request/response shapes
    routes/
      __init__.py
      status.py                   # GET /api/status, GET /api/satellites
      mode.py                     # GET /api/mode, POST /api/mode/known-base, etc.
      sites.py                    # GET/POST/PUT/DELETE /api/sites
      config_routes.py            # GET/PUT /api/config
      auth_routes.py              # POST /api/auth/login, /logout, PUT /api/auth/password
    ws/
      __init__.py
      live.py                     # WebSocket /ws/live handler
  ui/
    index.html                    # Full-screen map shell (HTMX + Alpine.js)
    login.html                    # Password entry page (HTMX partial)
    css/
      survey365.css               # Custom styles on top of Pico.css
    js/
      map-core.js                 # MapLibre setup, basemap switching, site markers
      mode-panel.js               # Mode selection + status Alpine component
      ws-client.js                # WebSocket client, dispatches events to Alpine
  migrations/
    001_initial.sql               # All Phase 1 tables
  systemd/
    survey365.service             # Main app systemd unit
  nginx/
    survey365.conf                # Reverse proxy config
  install.sh                      # Automated Pi setup script
  tests/
    __init__.py
    conftest.py                   # pytest fixtures (test DB, test client)
    test_status.py
    test_sites.py
    test_mode.py
    test_auth.py
    test_gnss.py
    test_rtkbase.py
```

### 1.2 requirements.txt

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
websockets==14.1
pydantic==2.10.4
aiosqlite==0.20.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.18
itsdangerous==2.2.0
httpx==0.28.1
pytest==8.3.4
pytest-asyncio==0.25.0
```

Key decisions:
- **aiosqlite** for async SQLite access (FastAPI is async-first). SpatiaLite will be loaded as an extension via `conn.enable_load_extension(True)` then `conn.load_extension("mod_spatialite")`.
- **passlib[bcrypt]** for password hashing. bcrypt is slow by design, which is fine for a single-password system.
- **itsdangerous** for signed session cookies (same pattern as Flask sessions -- `URLSafeTimedSerializer`). Avoids needing a full session store.
- **python-multipart** required by FastAPI for form data (login form).
- **httpx** for testing (FastAPI `TestClient` alternative for async tests).
- No GDAL yet (Phase 5). No pyserial (str2str owns the serial port; we read from TCP:5015).

### 1.3 Virtual Environment

The install script will create it at `/opt/survey365/venv`. On the Pi:

```bash
python3 -m venv /opt/survey365/venv
/opt/survey365/venv/bin/pip install -r /opt/survey365/requirements.txt
```

SpatiaLite is a system package (`libspatialite-dev`), not a pip package. The install script installs it via apt.

### 1.4 Dependencies Between Steps

- requirements.txt must exist before venv creation
- migrations/001_initial.sql must exist before first app start
- db.py must be complete before any route can be written
- gnss.py must be complete before status routes and WebSocket
- rtkbase.py must be complete before mode routes
- auth.py must be complete before config routes (admin-gated)

### 1.5 Parallelizable Work

These can be developed simultaneously by different developers (or in parallel branches):
- **Track A**: db.py + migrations + sites routes (pure CRUD, no hardware)
- **Track B**: gnss.py + status routes + WebSocket (TCP stream parsing)
- **Track C**: Frontend HTML/JS (can mock API responses)
- **Track D**: rtkbase.py + mode routes (needs Pi access for testing)

---

## 2. Database -- SQLite + SpatiaLite

### 2.1 File: `migrations/001_initial.sql`

```sql
-- Enable SpatiaLite
SELECT load_extension('mod_spatialite');
SELECT InitSpatialMetaData(1);

-- Saved survey points
CREATE TABLE IF NOT EXISTS sites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    height          REAL,
    ortho_height    REAL,
    datum           TEXT DEFAULT 'NAD83(2011)',
    epoch           TEXT DEFAULT '2010.0',
    source          TEXT CHECK(source IN ('manual','cors_rtk','opus','averaged','imported')),
    accuracy_h      REAL,
    accuracy_v      REAL,
    established     TEXT,
    last_used       TEXT,
    notes           TEXT,
    photo_path      TEXT,
    opus_job_id     TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
SELECT AddGeometryColumn('sites', 'geom', 4326, 'POINT', 'XY');
SELECT CreateSpatialIndex('sites', 'geom');

-- Session history (for resume + logging)
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    mode             TEXT NOT NULL CHECK(mode IN ('known_base','relative_base','cors_establish','opus_establish','rover','idle')),
    site_id          INTEGER REFERENCES sites(id),
    ntrip_profile_id INTEGER,
    started_at       TEXT DEFAULT (datetime('now')),
    ended_at         TEXT,
    rinex_path       TEXT,
    notes            TEXT
);

-- App configuration (key-value store)
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Seed default config
INSERT OR IGNORE INTO config (key, value) VALUES
    ('web_password_hash', ''),
    ('auto_resume', 'false'),
    ('maptiler_key', ''),
    ('antenna_voltage_on_boot', 'true'),
    ('f9p_update_rate', '1'),
    ('default_lat', '30.69'),
    ('default_lon', '-88.05'),
    ('default_zoom', '11');
```

Design decisions:
- **SpatiaLite R*Tree index** on `sites.geom` enables fast proximity queries. The `SELECT AddGeometryColumn` + `SELECT CreateSpatialIndex` are SpatiaLite-specific DDL functions.
- **sessions table** tracks mode history for resume. Phase 1 uses it minimally (just the last active session for resume-last).
- **config table** is a simple key-value store. No schema migration needed when adding new settings -- just insert a new row. Mirrors the GoldDigger365 `settings` table pattern from `db.js`.
- Passwords are stored as bcrypt hashes in the `config` table. An empty hash means "first boot, no password set yet."
- `ntrip_profiles` table is NOT included in Phase 1. The known-base and relative-base modes don't need NTRIP profile selection -- they use whatever is already configured in RTKBase's `settings.conf`. NTRIP profile management is Phase 2.

### 2.2 File: `app/db.py`

This module provides:

```python
# Singleton connection pool (actually a single connection for SQLite)
# - get_db() -> async context manager yielding aiosqlite.Connection
# - run_migration(path) -> execute a .sql file
# - The connection loads mod_spatialite on every open
```

Key implementation details:
- Use `aiosqlite.connect()` with `isolation_level=None` for explicit transaction control.
- Call `conn.enable_load_extension(True)` then `await conn.execute("SELECT load_extension('mod_spatialite')")` on connection open.
- DB file location: `/opt/survey365/data/survey365.db` (configurable via `SURVEY365_DB` env var). Data directory is outside the code repo so `git pull` doesn't touch it.
- Include a `row_factory` that returns `sqlite3.Row` (dict-like access).
- Provide `execute`, `fetchone`, `fetchall` convenience wrappers.
- On startup, check if `sites` table exists; if not, run `001_initial.sql`.

**Pi 4 concern**: SQLite with WAL mode is ideal for the Pi -- one writer, multiple readers, no contention. Enable WAL on first connection: `PRAGMA journal_mode=WAL`.

---

## 3. FastAPI Backend

### 3.1 File: `app/main.py`

```python
# FastAPI application setup
# - Mounts static files: /ui/ directory served at /
# - Registers API routers: /api/status, /api/sites, /api/mode, /api/config, /api/auth
# - Registers WebSocket: /ws/live
# - Startup event: initialize DB, start GNSS reader background task
# - Shutdown event: close GNSS reader, close DB
# - CORS: disabled (single-origin, served behind Nginx)
# - Runs on 0.0.0.0:8080
```

Pattern note: Following the GoldDigger365 pattern where `server.js` registers all routes and serves static files. The FastAPI equivalent uses `APIRouter` for each route group and `StaticFiles` mount.

The app startup will:
1. Ensure data directory exists
2. Run migrations if needed
3. Start the GNSS reader as a background `asyncio.Task` (continuously reads TCP:5015)
4. Store the GNSS state in an in-memory object accessible by status routes and WebSocket

### 3.2 File: `app/auth.py`

**Password Storage:**
- bcrypt hash stored in `config` table under key `web_password_hash`
- If hash is empty string, first POST to `/api/auth/password` sets it (first-boot flow)

**Session Cookie:**
- Signed cookie using `itsdangerous.URLSafeTimedSerializer` with a secret key
- Secret key stored in `config` table under key `session_secret` (auto-generated on first boot, 32 random bytes hex-encoded)
- Cookie name: `s365_session`
- Max age: 86400 seconds (24 hours)
- Cookie value: signed timestamp (no user ID needed -- single user system)

**Middleware/Dependency:**
```python
# require_admin: FastAPI Depends() that checks the cookie
# - If valid session cookie exists and is not expired -> pass through
# - Otherwise -> raise HTTPException(401) with JSON body
# 
# Field-facing endpoints (status, mode, sites) do NOT use require_admin
# Admin endpoints (config, password change) DO use require_admin
```

### 3.3 File: `app/routes/auth_routes.py`

```
POST /api/auth/login
  Request:  { "password": "string" }
  Response: { "ok": true }
  Cookie:   Sets s365_session
  Errors:   401 { "error": "Invalid password" }
            400 { "error": "No password set. Set one first." }

POST /api/auth/logout
  Response: { "ok": true }
  Cookie:   Clears s365_session

PUT /api/auth/password
  Request:  { "current": "string"|null, "new_password": "string" }
  Response: { "ok": true }
  Notes:    If no password set (first boot), current can be null
  Auth:     require_admin (unless first boot)
  Errors:   401 { "error": "Current password incorrect" }
            400 { "error": "New password must be at least 4 characters" }

GET /api/auth/check
  Response: { "authenticated": true|false, "password_set": true|false }
  Notes:    No auth required. Frontend calls this on load to decide
            whether to show login prompt for admin features.
```

### 3.4 File: `app/routes/status.py`

```
GET /api/status
  Response: {
    "mode": "known_base" | "relative_base" | "idle",
    "mode_label": "Broadcasting from Oak Street PK" | "IDLE",
    "site": { "id": 1, "name": "Oak Street PK" } | null,
    "gnss": {
      "fix_type": "3D" | "2D" | "No Fix" | "Time Only",
      "satellites_used": 28,
      "satellites_visible": 35,
      "latitude": 30.694512,
      "longitude": -88.043281,
      "height": 12.345,
      "accuracy_h": 1.2,
      "accuracy_v": 2.1,
      "pdop": 1.8,
      "age": 0.5
    },
    "services": {
      "str2str_tcp": true,
      "str2str_ntrip_A": false,
      "str2str_local_ntrip_caster": false,
      "rtkbase_web": true
    },
    "uptime_seconds": 3600,
    "session": {
      "id": 5,
      "started_at": "2026-03-28T14:22:00Z",
      "mode": "known_base"
    } | null
  }

GET /api/satellites
  Response: {
    "satellites": [
      {
        "constellation": "GPS",
        "svid": 12,
        "elevation": 45,
        "azimuth": 120,
        "cn0": 42.0,
        "used": true
      },
      ...
    ],
    "summary": {
      "gps": { "used": 12, "visible": 15 },
      "glonass": { "used": 8, "visible": 10 },
      "galileo": { "used": 6, "visible": 8 },
      "beidou": { "used": 2, "visible": 4 }
    }
  }
```

Implementation: Both endpoints read from the in-memory GNSS state object that `gnss.py` continuously updates. No database hit. The `services` status is obtained by calling `systemctl is-active` via `asyncio.create_subprocess_exec`.

### 3.5 File: `app/routes/sites.py`

```
GET /api/sites
  Query params:
    near_lat (float, optional) - user's latitude for proximity sort
    near_lon (float, optional) - user's longitude for proximity sort
    search (str, optional) - name/notes text search
    limit (int, default 100)
    offset (int, default 0)
  Response: {
    "sites": [
      {
        "id": 1,
        "name": "Oak Street PK",
        "lat": 30.694512,
        "lon": -88.043281,
        "height": 12.345,
        "ortho_height": null,
        "datum": "NAD83(2011)",
        "epoch": "2010.0",
        "source": "manual",
        "accuracy_h": null,
        "accuracy_v": null,
        "established": "2026-03-28",
        "last_used": "2026-03-28",
        "notes": "PK nail in sidewalk",
        "distance_m": 125.4  // only present when near_lat/near_lon provided
      },
      ...
    ],
    "total": 42
  }

GET /api/sites/{id}
  Response: { ...full site object... }
  Errors: 404

POST /api/sites
  Request: {
    "name": "string (required)",
    "lat": 30.694512 (required),
    "lon": -88.043281 (required),
    "height": 12.345,
    "ortho_height": null,
    "datum": "NAD83(2011)",
    "epoch": "2010.0",
    "source": "manual",
    "accuracy_h": null,
    "accuracy_v": null,
    "established": "2026-03-28",
    "notes": ""
  }
  Response: { "id": 1, ...full site object... }
  Notes: Also creates SpatiaLite POINT geometry via
         MakePoint(lon, lat, 4326)

PUT /api/sites/{id}
  Request: { ...any subset of fields... }
  Response: { ...updated site... }
  Notes: If lat/lon changed, update geom too
  Errors: 404

DELETE /api/sites/{id}
  Response: { "ok": true }
  Errors: 404
```

**Proximity query implementation** (when `near_lat` and `near_lon` provided):

```sql
SELECT *, ST_Distance(geom, MakePoint(?, ?, 4326), 1) as distance_m
FROM sites
ORDER BY distance_m ASC
LIMIT ? OFFSET ?
```

The `ST_Distance(..., 1)` parameter `1` means "compute distance in meters using the ellipsoid" (SpatiaLite GEOS). This is the key spatial feature that justifies SpatiaLite over plain SQLite.

**SpatiaLite insert**:

```sql
INSERT INTO sites (name, lat, lon, height, ..., geom)
VALUES (?, ?, ?, ?, ..., MakePoint(?, ?, 4326))
```

### 3.6 File: `app/routes/mode.py`

```
GET /api/mode
  Response: {
    "mode": "known_base" | "relative_base" | "idle",
    "site": { "id": 1, "name": "..." } | null,
    "session_id": 5 | null,
    "started_at": "2026-03-28T14:22:00Z" | null,
    "establishing": false,
    "establish_progress": null
  }

POST /api/mode/known-base
  Request: { "site_id": 1 }
  Response: { "ok": true, "session_id": 6 }
  Flow:
    1. Look up site by id -> get lat, lon, height
    2. Write position to RTKBase settings.conf (via rtkbase.py)
    3. Restart str2str services (via rtkbase.py)
    4. Create session record in DB
    5. Update in-memory mode state
    6. Broadcast mode change via WebSocket
  Errors:
    404 { "error": "Site not found" }
    409 { "error": "Mode change in progress" }

POST /api/mode/relative-base
  Request: { "duration_seconds": 120 }  (optional, default 120)
  Response: { "ok": true, "session_id": 7 }
  Flow:
    1. Start averaging current GNSS position (read from gnss.py state)
    2. Set mode to "relative_base" with establishing=true
    3. Background task: collect positions for duration_seconds
    4. Compute mean lat/lon/height
    5. Write averaged position to RTKBase settings.conf
    6. Restart str2str services
    7. Create session record, save averaged point as site (source='averaged')
    8. Set establishing=false
    9. Broadcast via WebSocket at each step
  Errors:
    409 { "error": "Mode change in progress" }
    400 { "error": "No GNSS fix available for averaging" }

POST /api/mode/stop
  Response: { "ok": true }
  Flow:
    1. End current session (set ended_at)
    2. Stop base services (str2str_ntrip_A, str2str_local_ntrip_caster)
    3. Set mode to "idle"
    4. Broadcast via WebSocket
  Notes: str2str_tcp stays running (it relays F9P data, needed for status)

POST /api/mode/resume
  Response: { "ok": true, "session_id": 8 } | { "ok": false, "error": "No previous session" }
  Flow:
    1. Find last session with mode != 'idle'
    2. If known_base: re-start with same site
    3. If relative_base: re-start with last averaged position
```

**Critical design decision**: Mode changes must be serialized. Use an `asyncio.Lock` to prevent concurrent mode transitions. The mode state is held in memory (a simple Python dataclass) and persisted to the sessions table.

### 3.7 File: `app/routes/config_routes.py`

```
GET /api/config
  Auth: require_admin
  Response: {
    "maptiler_key": "...",
    "auto_resume": false,
    "antenna_voltage_on_boot": true,
    "f9p_update_rate": 1,
    "default_lat": 30.69,
    "default_lon": -88.05,
    "default_zoom": 11,
    "password_set": true
  }
  Notes: Never returns password hash or session secret

PUT /api/config
  Auth: require_admin
  Request: { "maptiler_key": "abc123", "auto_resume": true, ... }
  Response: { "ok": true }
  Notes: Only accepts known keys. Rejects unknown keys.
```

### 3.8 File: `app/ws/live.py`

```
WS /ws/live
  Auth: None (field crew access)
  
  Server sends JSON messages:
  
  Type "status" (every 1 second):
  {
    "type": "status",
    "gnss": { ...same as GET /api/status gnss object... },
    "mode": "known_base",
    "services": { ... }
  }

  Type "mode_change":
  {
    "type": "mode_change",
    "mode": "known_base",
    "site": { "id": 1, "name": "..." },
    "session_id": 6
  }

  Type "establish_progress":
  {
    "type": "establish_progress",
    "elapsed_seconds": 45,
    "total_seconds": 120,
    "current_position": { "lat": ..., "lon": ..., "height": ... },
    "samples": 45
  }
  
  Client can send:
  {
    "type": "ping"
  }
  -> server responds { "type": "pong" }
```

Implementation: Use FastAPI's `WebSocket` endpoint. Maintain a set of connected clients. A background task reads from the GNSS state and broadcasts every 1 second. Mode change events are pushed immediately when they occur.

**Pi 4 concern**: Keep WebSocket message size small. The 1-second status update is ~300 bytes JSON. With 5 connected clients, that is 1.5 KB/s -- negligible.

---

## 4. RTKBase Integration

### 4.1 File: `app/rtkbase.py`

This module wraps all interactions with RTKBase's `settings.conf` and systemd services.

**RTKBase settings.conf format** (INI-like, located at `~/rtkbase/settings.conf`):
The file uses a standard INI format with sections. The key fields we need to read/write for Phase 1:

```ini
[main]
position= 30.694512 -88.043281 12.345    # lat lon height (space-separated)
com_port=/dev/ttyGNSS
com_port_settings=115200:8:n:1
receiver=ublox_zed-f9p

[ntrip_a]
svr_addr_a=caster.emlid.com
svr_port_a=2101
svr_pwd_a=...
mnt_name_a=...
rtcm_msg_a=1005(10),1077(1),1087(1),1097(1),1127(1)
ntrip_a_autostart=no

[local_ntrip_caster]
local_ntripc_port=2101
local_ntripc_mnt_name=SURVEY365
local_ntripc_pwd=...
local_ntripc_autostart=no
```

**Functions needed:**

```python
async def read_settings() -> dict:
    """Parse ~/rtkbase/settings.conf into a dict of dicts."""
    
async def write_position(lat: float, lon: float, height: float):
    """Update [main] position in settings.conf.
    Format: 'position= {lat} {lon} {height}'"""
    
async def restart_service(service_name: str):
    """systemctl restart {service_name}
    Services: str2str_tcp, str2str_ntrip_A, 
              str2str_local_ntrip_caster, rtkbase_web"""
    
async def stop_service(service_name: str):
    """systemctl stop {service_name}"""
    
async def service_is_active(service_name: str) -> bool:
    """systemctl is-active {service_name}, returns True if 'active'"""

async def start_base_services():
    """Start str2str_tcp + str2str_ntrip_A + str2str_local_ntrip_caster.
    Called after writing new position."""
    
async def stop_base_services():
    """Stop str2str_ntrip_A + str2str_local_ntrip_caster.
    str2str_tcp stays running (needed for GNSS status)."""
```

**Critical path**: `settings.conf` is read/written by RTKBase's own web UI too. Our writes must use the same format. The safest approach is to use Python's `configparser` with `RawConfigParser` (RTKBase uses `=` without spaces around it in some places, but `configparser` handles this). Read the entire file, modify only the `position` field, write back.

**Permissions concern**: The RTKBase services run as the `jaredirby` user. Survey365 will also run as `jaredirby`. Both need write access to `~/rtkbase/settings.conf`. Since they are the same user, this is not an issue.

**systemctl concern**: Restarting services requires either running Survey365 as root (bad) or using `sudo` without a password for specific commands. The install script will add a sudoers entry:

```
jaredirby ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart str2str_*, /usr/bin/systemctl stop str2str_*, /usr/bin/systemctl start str2str_*, /usr/bin/systemctl is-active str2str_*, /usr/bin/systemctl restart rtkbase_web, /usr/bin/systemctl is-active rtkbase_web
```

---

## 5. F9P Communication -- UBX Parser for TCP:5015

### 5.1 File: `app/gnss.py`

RTKBase's `str2str_tcp` service reads raw data from the F9P serial port (`/dev/ttyGNSS`) and relays it to TCP port 5015. This is a raw binary stream containing mixed RTCM3 and UBX messages.

**We do NOT open the serial port directly.** str2str owns it. We connect to `localhost:5015` as a TCP client and parse the stream.

**Architecture:**

```python
class GNSSState:
    """Thread-safe in-memory state updated by the background reader."""
    fix_type: str = "No Fix"
    satellites_used: int = 0
    satellites_visible: int = 0
    latitude: float = 0.0
    longitude: float = 0.0
    height: float = 0.0
    accuracy_h: float = 0.0
    accuracy_v: float = 0.0
    pdop: float = 0.0
    satellite_details: list[dict] = []
    last_update: float = 0.0  # time.time()

class GNSSReader:
    """Async background task that connects to TCP:5015 and parses UBX."""
    
    async def run(self):
        """Main loop: connect, read, parse, update state. Reconnect on error."""
        
    def _parse_ubx_frame(self, data: bytes) -> tuple[int, int, bytes]:
        """Extract class, id, payload from UBX frame."""
        
    def _handle_nav_pvt(self, payload: bytes):
        """UBX-NAV-PVT (0x01 0x07): position, velocity, time.
        Contains: fix type, lat, lon, height, hAcc, vAcc, numSV, pDOP."""
        
    def _handle_nav_sat(self, payload: bytes):
        """UBX-NAV-SAT (0x01 0x35): per-satellite info.
        Contains: svId, constellation, elev, azim, cn0, flags (used)."""
```

**UBX protocol details:**

UBX frames start with `0xB5 0x62`, followed by class (1 byte), ID (1 byte), length (2 bytes LE), payload, checksum (2 bytes).

Key messages to parse:

1. **UBX-NAV-PVT (class=0x01, id=0x07)**: 92-byte payload
   - Offset 20: fixType (uint8) -- 0=no fix, 2=2D, 3=3D
   - Offset 23: numSV (uint8) -- satellites used
   - Offset 24: lon (int32, 1e-7 degrees)
   - Offset 28: lat (int32, 1e-7 degrees)
   - Offset 32: height (int32, mm above ellipsoid)
   - Offset 40: hAcc (uint32, mm)
   - Offset 44: vAcc (uint32, mm)
   - Offset 76: pDOP (uint16, 0.01 scale)

2. **UBX-NAV-SAT (class=0x01, id=0x35)**: variable length
   - Offset 5: numSvs (uint8)
   - Then 12-byte records per SV:
     - Offset 0: gnssId (uint8) -- 0=GPS, 1=SBAS, 2=Galileo, 3=BeiDou, 6=GLONASS
     - Offset 1: svId (uint8)
     - Offset 2: cno (uint8) -- C/N0 in dBHz
     - Offset 3: elev (int8) -- elevation in degrees
     - Offset 4-5: azim (int16) -- azimuth in degrees
     - Offset 8-11: flags (uint32) -- bit 3: used in navigation solution

**Stream parsing strategy**: Read bytes from TCP socket into a ring buffer. Scan for `0xB5 0x62` sync bytes. Validate length and checksum before processing. Discard RTCM3 frames (start with `0xD3`) -- we only need UBX.

**Reconnection**: If the TCP connection drops (str2str_tcp restarting during mode change), wait 2 seconds and reconnect. The reader must be resilient to brief outages.

**Pi 4 concern**: The TCP:5015 stream at 1 Hz update rate is ~500 bytes/second. Parsing cost is negligible. The reader should use `asyncio.open_connection` for non-blocking I/O.

**Important note**: str2str_tcp relays the F9P's default output. The F9P must be configured to output UBX-NAV-PVT and UBX-NAV-SAT messages. RTKBase's configure-gnss step enables these by default. If they are not present in the stream, we need to send UBX-CFG-VALSET commands to enable them. However, this requires serial port access, which str2str owns. The install script should verify these messages are enabled by checking the TCP stream for a few seconds. If missing, temporarily stop str2str_tcp, configure the F9P, and restart.

---

## 6. Frontend

### 6.1 Philosophy

Following the GoldDigger365 pattern exactly:
- No build step. Plain HTML, JS, CSS served as static files.
- HTMX for server-driven partial updates (login form, admin panels).
- Alpine.js for client-side reactivity (map state, mode panel, WebSocket data).
- Pico.css for base styling. Minimal custom CSS.
- MapLibre GL JS v4 for the map. Loaded from local copy (offline requirement).

**Deviation from GoldDigger365**: The GoldDigger365 index.html is a nav-based shell that swaps page content via HTMX. Survey365's primary interface is a full-screen map with NO navigation bar. Everything floats on top of the map. The "shell" is the map itself.

### 6.2 File: `ui/index.html`

```html
<!-- Survey365 Main Interface -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <title>Survey365</title>
  <!-- Pico.css (served locally for offline) -->
  <link rel="stylesheet" href="/css/pico.min.css">
  <!-- MapLibre CSS (served locally) -->
  <link rel="stylesheet" href="/css/maplibre-gl.css">
  <!-- Custom styles -->
  <link rel="stylesheet" href="/css/survey365.css">
  <!-- HTMX (served locally) -->
  <script src="/js/vendor/htmx.min.js"></script>
  <!-- Alpine.js (served locally) -->
  <script defer src="/js/vendor/alpine.min.js"></script>
</head>
<body x-data="survey365App()" x-init="init()">

  <!-- Full-screen map -->
  <div id="map" style="position:fixed;top:0;left:0;right:0;bottom:0;z-index:0;"></div>

  <!-- Top bar: hamburger menu + search (future) -->
  <div class="s365-top-bar">
    <button class="s365-menu-btn" @click="menuOpen = !menuOpen" aria-label="Menu">&#9776;</button>
    <span class="s365-title">Survey365</span>
    <span class="s365-auth-indicator" x-show="!authenticated" 
          @click="showLogin = true" style="cursor:pointer">&#128274;</span>
  </div>

  <!-- Hamburger menu slide-out -->
  <div class="s365-menu" x-show="menuOpen" x-transition @click.outside="menuOpen = false">
    <h4>Mode</h4>
    <button @click="openModePanel('known_base'); menuOpen = false">Known Point Base</button>
    <button @click="startRelativeBase(); menuOpen = false">Relative Base (Quick)</button>
    <button @click="stopMode(); menuOpen = false">Stop</button>
    <button @click="resumeMode(); menuOpen = false">Resume Last</button>
    <hr>
    <h4>Points</h4>
    <button @click="showSiteList = true; menuOpen = false">Saved Points</button>
    <button @click="showAddSite = true; menuOpen = false">Add Point</button>
    <hr>
    <button @click="showLogin = true; menuOpen = false" x-show="!authenticated">Admin Login</button>
    <button @click="showAdmin = true; menuOpen = false" x-show="authenticated">Settings</button>
  </div>

  <!-- Bottom-left status strip -->
  <div class="s365-status-strip">
    <div class="s365-mode-indicator" :class="modeColor">
      <span x-text="modeLabel"></span>
    </div>
    <div class="s365-stats">
      <span>SVs: <strong x-text="gnss.satellites_used"></strong></span>
      <span>Fix: <strong x-text="gnss.fix_type"></strong></span>
      <span x-show="gnss.accuracy_h > 0">Acc: <strong x-text="gnss.accuracy_h.toFixed(3) + 'm'"></strong></span>
    </div>
  </div>

  <!-- Bottom-right: basemap picker + zoom to base -->
  <div class="s365-basemap-picker">
    <button @click="basemapMenuOpen = !basemapMenuOpen" aria-label="Basemap">&#127758;</button>
    <div x-show="basemapMenuOpen" class="s365-basemap-menu" @click.outside="basemapMenuOpen = false">
      <label><input type="radio" name="bm" value="street" x-model="basemap" @change="switchBasemap()"> Street</label>
      <label><input type="radio" name="bm" value="satellite" x-model="basemap" @change="switchBasemap()"> Satellite</label>
      <label><input type="radio" name="bm" value="topo" x-model="basemap" @change="switchBasemap()"> Topo</label>
    </div>
  </div>
  <div class="s365-center-btn">
    <button @click="centerOnBase()" aria-label="Center on base">&#9678;</button>
  </div>

  <!-- Mode panel overlay (known point selection) -->
  <div class="s365-overlay" x-show="showModePanel" x-transition>
    <!-- Site list sorted by proximity -->
    <h3>Select Base Point</h3>
    <div x-show="sitesLoading" aria-busy="true">Loading points...</div>
    <div class="s365-site-list">
      <template x-for="site in sites" :key="site.id">
        <div class="s365-site-item" @click="confirmKnownBase(site)">
          <strong x-text="site.name"></strong>
          <span x-text="site.distance_m ? (site.distance_m < 1000 ? site.distance_m.toFixed(0) + 'm' : (site.distance_m / 1000).toFixed(1) + 'km') : ''"></span>
          <small x-text="site.source"></small>
        </div>
      </template>
    </div>
    <button @click="showModePanel = false">Cancel</button>
  </div>

  <!-- Login dialog -->
  <dialog x-ref="loginDialog" :open="showLogin">
    <article>
      <h3>Admin Login</h3>
      <form @submit.prevent="doLogin()">
        <input type="password" x-model="loginPassword" placeholder="Password" autofocus>
        <button type="submit">Login</button>
      </form>
      <p x-show="loginError" style="color:var(--pico-del-color)" x-text="loginError"></p>
      <button class="secondary" @click="showLogin = false">Cancel</button>
    </article>
  </dialog>

  <!-- Establish progress overlay (relative base) -->
  <div class="s365-overlay" x-show="establishing" x-transition>
    <h3>Establishing Position</h3>
    <progress :value="establishProgress" max="100"></progress>
    <p x-text="establishStatus"></p>
    <button class="secondary" @click="stopMode()">Cancel</button>
  </div>

  <!-- MapLibre JS (served locally) -->
  <script src="/js/vendor/maplibre-gl.js"></script>
  <!-- Survey365 modules -->
  <script src="/js/map-core.js"></script>
  <script src="/js/mode-panel.js"></script>
  <script src="/js/ws-client.js"></script>
</body>
</html>
```

**Key differences from GoldDigger365 pattern:**
- No nav bar -- the map IS the app
- All vendor JS/CSS served locally (no CDN -- offline requirement on the PRD constraint list)
- Touch targets are 48px minimum (gloves + sunlight constraints)
- High contrast colors for mode indicators

### 6.3 File: `ui/js/map-core.js`

Simplified version of GoldDigger365's map-core.js. No parcel layers, no ArcGIS overlays. Just basemaps and site markers.

```javascript
// S365MapCore — map module for Survey365
(function() {
  'use strict';

  // Basemap definitions
  const BASEMAPS = {
    street: {
      source: {
        type: 'raster',
        tiles: ['https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key={key}'],
        tileSize: 512
      }
    },
    satellite: {
      source: {
        type: 'raster',
        tiles: ['https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key={key}'],
        tileSize: 512
      }
    },
    topo: {
      source: {
        type: 'raster',
        tiles: ['https://api.maptiler.com/maps/topo-v2/{z}/{x}/{y}.png?key={key}'],
        tileSize: 512
      }
    }
  };

  function createMap(containerId, opts) {
    // opts: { center, zoom, maptilerKey }
    // Returns MapLibre map with street basemap
    // Adds NavigationControl (bottom-right) and ScaleControl (bottom-left)
  }

  function switchBasemap(map, basemapKey, maptilerKey) {
    // Remove current basemap source/layer
    // Add new basemap source/layer
    // Preserve overlay layers (site markers, base marker)
  }

  // Site markers as a GeoJSON source
  function addSiteMarkers(map, sites) {
    // Add/update 'sites' GeoJSON source
    // Circle layer for points, different color by source type
    // Text layer for names (visible at zoom >= 14)
  }

  function updateBaseMarker(map, lat, lon, mode) {
    // Single marker for base station position
    // Color: green=broadcasting, yellow=establishing, gray=idle
    // Pulsing animation when broadcasting
  }

  function updateAccuracyCircle(map, lat, lon, accuracy_m) {
    // Translucent circle sized to horizontal accuracy
    // Uses turf.js circle or manual GeoJSON polygon
    // Only shown when accuracy < 10m (otherwise too large to be useful)
  }

  function addPhoneGPS(map) {
    // navigator.geolocation.watchPosition
    // Blue dot marker for user's phone position
    // Used for proximity sorting
  }

  window.S365MapCore = {
    createMap, switchBasemap, addSiteMarkers,
    updateBaseMarker, updateAccuracyCircle, addPhoneGPS
  };
})();
```

**MapTiler key handling**: The key is stored in the config table. The frontend fetches it from `/api/config` (public subset -- just the MapTiler key, not admin settings) or it can be embedded in the initial page render. For Phase 1, the simplest approach is a dedicated endpoint `GET /api/config/maptiler-key` that returns just the key (no auth required -- it is needed for the map to work for field crew).

**Fallback basemap**: If no MapTiler key is configured, fall back to CARTO Voyager tiles (free, no key needed) -- same as GoldDigger365's default.

### 6.4 File: `ui/js/ws-client.js`

```javascript
// S365WS — WebSocket client for live updates
(function() {
  'use strict';

  let ws = null;
  let reconnectTimer = null;

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + location.host + '/ws/live');
    
    ws.onmessage = function(e) {
      const msg = JSON.parse(e.data);
      // Dispatch custom events that Alpine components listen to
      document.dispatchEvent(new CustomEvent('s365:' + msg.type, { detail: msg }));
    };
    
    ws.onclose = function() {
      // Reconnect after 3 seconds
      reconnectTimer = setTimeout(connect, 3000);
    };
    
    ws.onerror = function() {
      ws.close();
    };
  }

  function disconnect() {
    clearTimeout(reconnectTimer);
    if (ws) ws.close();
  }

  window.S365WS = { connect, disconnect };
})();
```

### 6.5 File: `ui/js/mode-panel.js`

Alpine.js component factory for the main app state:

```javascript
function survey365App() {
  return {
    // Map state
    map: null,
    basemap: 'street',
    basemapMenuOpen: false,
    maptilerKey: '',
    
    // Menu state
    menuOpen: false,
    
    // Auth state
    authenticated: false,
    showLogin: false,
    loginPassword: '',
    loginError: '',
    showAdmin: false,
    
    // Mode state
    mode: 'idle',
    modeLabel: 'IDLE',
    modeSite: null,
    
    // GNSS state (updated by WebSocket)
    gnss: {
      fix_type: 'No Fix',
      satellites_used: 0,
      latitude: 0,
      longitude: 0,
      height: 0,
      accuracy_h: 0,
    },
    
    // Sites
    sites: [],
    sitesLoading: false,
    showModePanel: false,
    showSiteList: false,
    showAddSite: false,
    
    // Establish
    establishing: false,
    establishProgress: 0,
    establishStatus: '',
    
    // Computed
    get modeColor() {
      if (this.mode === 'known_base') return 's365-mode-green';
      if (this.mode === 'relative_base' && !this.establishing) return 's365-mode-orange';
      if (this.establishing) return 's365-mode-yellow';
      return 's365-mode-gray';
    },
    
    async init() {
      // 1. Fetch initial status
      // 2. Fetch maptiler key
      // 3. Initialize map
      // 4. Connect WebSocket
      // 5. Check auth state
      // 6. Listen for WebSocket events
      // 7. Start phone GPS for proximity
    },
    
    // ... methods for each action (doLogin, openModePanel, confirmKnownBase,
    //     startRelativeBase, stopMode, resumeMode, loadSites, centerOnBase, etc.)
  };
}
```

### 6.6 File: `ui/login.html`

A standalone login page (for direct navigation / admin access). Also used as an HTMX partial.

### 6.7 File: `ui/css/survey365.css`

Minimal custom styles on top of Pico.css:

```css
/* High contrast for sunlight readability */
:root {
  --s365-green: #00c853;
  --s365-yellow: #ffd600;
  --s365-orange: #ff6d00;
  --s365-red: #d50000;
  --s365-gray: #9e9e9e;
  --s365-blue: #2979ff;
}

/* Status strip - bottom left floating panel */
.s365-status-strip {
  position: fixed;
  bottom: 16px;
  left: 16px;
  z-index: 10;
  background: rgba(0,0,0,0.85);
  color: white;
  border-radius: 12px;
  padding: 8px 16px;
  font-size: 14px;
  min-width: 200px;
  backdrop-filter: blur(8px);
}

/* Touch targets: minimum 48px */
.s365-menu button,
.s365-site-item,
.s365-menu-btn,
.s365-basemap-picker button,
.s365-center-btn button {
  min-height: 48px;
  min-width: 48px;
}

/* Mode indicator colors */
.s365-mode-green { color: var(--s365-green); }
.s365-mode-yellow { color: var(--s365-yellow); }
.s365-mode-orange { color: var(--s365-orange); }
.s365-mode-gray { color: var(--s365-gray); }

/* Overlay panels (mode selection, site list) */
.s365-overlay {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  max-height: 70vh;
  z-index: 20;
  background: white;
  border-radius: 16px 16px 0 0;
  padding: 16px;
  overflow-y: auto;
  box-shadow: 0 -4px 20px rgba(0,0,0,0.3);
}
```

### 6.8 Vendor JS/CSS (served locally)

The PRD constraint says "All UI assets must be bundled locally. No CDN." The install script will download these files into `ui/js/vendor/` and `ui/css/`:

- `maplibre-gl.js` + `maplibre-gl.css` (MapLibre GL JS v4.7.1, ~800KB gzipped)
- `htmx.min.js` (HTMX 2.0.4, ~16KB gzipped)
- `alpine.min.js` (Alpine.js 3.14.8, ~17KB gzipped)
- `pico.min.css` (Pico.css 2.x, ~10KB gzipped)

Total: ~850KB. Well within the Pi's storage and serves instantly over LAN.

---

## 7. Nginx Configuration

### 7.1 File: `nginx/survey365.conf`

```nginx
server {
    listen 80 default_server;
    server_name _;

    # Survey365 API + WebSocket
    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    # Survey365 static files (map UI)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }

    # RTKBase web UI (passthrough)
    location /rtkbase/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Disable caching for API
    location ~* /api/ {
        add_header Cache-Control "no-cache, no-store";
    }

    # Cache static assets aggressively (bust with query strings)
    location ~* \.(js|css|png|jpg|ico|woff2)$ {
        proxy_pass http://127.0.0.1:8080;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
```

**Key decisions:**
- Nginx on port 80 is the public entry point. FastAPI runs on 8080 (internal only).
- WebSocket upgrade handled explicitly in the `/ws/` location block.
- RTKBase web UI preserved at `/rtkbase/` for direct access when needed.
- `proxy_read_timeout 86400` on WebSocket prevents Nginx from closing idle WebSocket connections.
- The install script will disable the default nginx site and symlink this config.

**Pi 4 concern**: Nginx uses ~5MB RAM. Well within budget.

---

## 8. systemd Service

### 8.1 File: `systemd/survey365.service`

```ini
[Unit]
Description=Survey365 Field Operations Controller
After=network.target str2str_tcp.service
Wants=str2str_tcp.service

[Service]
Type=simple
User=jaredirby
Group=jaredirby
WorkingDirectory=/opt/survey365
Environment=SURVEY365_DB=/opt/survey365/data/survey365.db
Environment=RTKBASE_SETTINGS=/home/jaredirby/rtkbase/settings.conf
ExecStart=/opt/survey365/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1 --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Resource limits for Pi 4 (2GB RAM)
MemoryMax=200M
MemoryHigh=150M

[Install]
WantedBy=multi-user.target
```

**Key decisions:**
- `--workers 1`: Single uvicorn worker. FastAPI's async design handles concurrency via the event loop. Multiple workers would waste RAM on the Pi and complicate shared state (GNSS reader, mode lock).
- `After=str2str_tcp.service`: Ensures the F9P data relay is running before Survey365 tries to connect to TCP:5015.
- `MemoryMax=200M`: Hard limit prevents Survey365 from starving the system. If it exceeds 200MB, systemd kills and restarts it.
- `Restart=always` + `RestartSec=5`: Auto-recover from crashes.

---

## 9. Install Script

### 9.1 File: `install.sh`

```bash
#!/usr/bin/env bash
# Survey365 Installer for Raspberry Pi 4
# Run as: sudo bash install.sh
set -euo pipefail

INSTALL_DIR=/opt/survey365
DATA_DIR=/opt/survey365/data
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
USER=jaredirby
VENV_DIR=$INSTALL_DIR/venv

echo "=== Survey365 Installer ==="

# Step 1: System packages
echo "[1/8] Installing system packages..."
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx \
    libspatialite-dev libsqlite3-mod-spatialite

# Step 2: Create install directory
echo "[2/8] Setting up directories..."
mkdir -p $INSTALL_DIR $DATA_DIR
# Symlink or copy app code
ln -sfn $REPO_DIR/app $INSTALL_DIR/app
ln -sfn $REPO_DIR/ui $INSTALL_DIR/ui
ln -sfn $REPO_DIR/migrations $INSTALL_DIR/migrations
cp $REPO_DIR/requirements.txt $INSTALL_DIR/

# Step 3: Download vendor assets (for offline operation)
echo "[3/8] Downloading vendor assets..."
mkdir -p $INSTALL_DIR/ui/js/vendor $INSTALL_DIR/ui/css
curl -sL "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js" \
     -o $INSTALL_DIR/ui/js/vendor/maplibre-gl.js
curl -sL "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" \
     -o $INSTALL_DIR/ui/css/maplibre-gl.css
curl -sL "https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js" \
     -o $INSTALL_DIR/ui/js/vendor/htmx.min.js
curl -sL "https://unpkg.com/alpinejs@3.14.8/dist/cdn.min.js" \
     -o $INSTALL_DIR/ui/js/vendor/alpine.min.js
curl -sL "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css" \
     -o $INSTALL_DIR/ui/css/pico.min.css

# Step 4: Python virtual environment
echo "[4/8] Setting up Python venv..."
python3 -m venv $VENV_DIR
$VENV_DIR/bin/pip install --upgrade pip
$VENV_DIR/bin/pip install -r $INSTALL_DIR/requirements.txt

# Step 5: File ownership
echo "[5/8] Setting permissions..."
chown -R $USER:$USER $INSTALL_DIR

# Step 6: Sudoers for service control
echo "[6/8] Configuring sudoers..."
cat > /etc/sudoers.d/survey365 << 'SUDOERS'
jaredirby ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart str2str_*
jaredirby ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop str2str_*
jaredirby ALL=(ALL) NOPASSWD: /usr/bin/systemctl start str2str_*
jaredirby ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active str2str_*
jaredirby ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart rtkbase_web
jaredirby ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active rtkbase_web
SUDOERS
chmod 0440 /etc/sudoers.d/survey365

# Step 7: Nginx config
echo "[7/8] Configuring Nginx..."
cp $REPO_DIR/nginx/survey365.conf /etc/nginx/sites-available/survey365
ln -sfn /etc/nginx/sites-available/survey365 /etc/nginx/sites-enabled/survey365
rm -f /etc/nginx/sites-enabled/default
# Preserve RTKBase's nginx config if it exists
nginx -t && systemctl reload nginx

# Step 8: systemd service
echo "[8/8] Installing systemd service..."
cp $REPO_DIR/systemd/survey365.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable survey365
systemctl start survey365

echo ""
echo "=== Survey365 installed ==="
echo "Web UI: http://$(hostname -I | awk '{print $1}')"
echo "Data directory: $DATA_DIR"
echo "Logs: journalctl -u survey365 -f"
```

**Key decisions:**
- Uses symlinks from `/opt/survey365/app` and `/opt/survey365/ui` to the git repo. This means `git pull` in the repo immediately updates the code. A `systemctl restart survey365` picks up changes.
- Downloads vendor JS/CSS during install (requires internet). After install, the system works offline.
- Preserves RTKBase's existing services. Does NOT modify RTKBase installation.
- The sudoers file is scoped tightly to only the specific systemctl commands Survey365 needs.

**Potential issue**: If RTKBase's nginx config already listens on port 80, there will be a conflict. The install script removes the default nginx site but does NOT remove RTKBase's config. The RTKBase installer creates `/etc/nginx/sites-available/rtkbase` which also listens on port 80. Solution: the install script should check for this and either replace it or merge. The safest approach is to replace RTKBase's nginx config with our `survey365.conf` which includes the `/rtkbase/` proxy pass, preserving access to RTKBase's UI.

---

## 10. Testing Plan

### 10.1 Local Development Testing (Mac)

You can develop and test most of the app on macOS without a Pi:

**Database tests** (`tests/test_sites.py`):
- Uses a temporary in-memory SQLite database with SpatiaLite loaded
- Tests CRUD operations on sites
- Tests proximity query with known coordinates
- Tests SpatiaLite geometry creation and distance calculation
- **Prerequisite**: `brew install libspatialite` on macOS

**Auth tests** (`tests/test_auth.py`):
- Test first-boot password set flow
- Test login with correct/incorrect password
- Test session cookie validity and expiry
- Test admin endpoint gating (401 without cookie, 200 with cookie)

**GNSS parser tests** (`tests/test_gnss.py`):
- Unit tests with recorded UBX binary data (capture from the Pi)
- Test NAV-PVT parsing with known payloads
- Test NAV-SAT parsing with known payloads
- Test frame synchronization (finding sync bytes in noise)
- Test checksum validation
- No TCP connection needed -- test the parser functions directly
- **Capture test data**: SSH to Pi, `nc localhost 5015 | head -c 10000 > gnss_sample.bin`

**RTKBase integration tests** (`tests/test_rtkbase.py`):
- Test parsing of `settings.conf` with a sample file
- Test writing position updates (verify file content after write)
- Mock `asyncio.create_subprocess_exec` for systemctl commands
- Use a temp file as the settings.conf path

**API integration tests** (`tests/test_status.py`, `tests/test_mode.py`):
- Use FastAPI's `TestClient` (or `httpx.AsyncClient`)
- Mock the GNSS reader (inject fake GNSS state)
- Mock the RTKBase module (don't actually restart services)
- Test full request/response cycle for every endpoint
- Test WebSocket connection and message format

**Frontend tests** (manual):
- Open `index.html` directly in browser
- Mock API with a simple Python HTTP server returning canned JSON
- Test map rendering, basemap switching, site markers
- Test responsive layout on phone-sized viewport

### 10.2 File: `tests/conftest.py`

```python
# Fixtures:
# - tmp_db: creates a temp SQLite+SpatiaLite database, runs migrations, yields path
# - app: creates FastAPI test app with mocked GNSS reader and temp DB
# - client: httpx.AsyncClient pointed at the test app
# - mock_gnss_state: GNSSState with realistic test values
# - sample_settings_conf: temp file with RTKBase settings format
```

### 10.3 On-Pi Testing

After deploying to the Pi:

1. **Smoke test**: `curl http://localhost:8080/api/status` -- should return JSON with GNSS data
2. **WebSocket test**: `websocat ws://localhost:8080/ws/live` -- should receive JSON messages every second
3. **GNSS stream verification**: Check that `gnss.satellites_used > 0` within 60 seconds of F9P power-on
4. **Mode test**: POST to `/api/mode/known-base` with a test site, verify RTKBase services restart and `settings.conf` is updated
5. **Nginx test**: Access `http://<pi-ip>/` from a phone browser -- should see the map
6. **RTKBase preservation**: Access `http://<pi-ip>/rtkbase/` -- should still work
7. **Memory test**: `systemd-cgtop` -- verify Survey365 stays under 200MB
8. **Sunlight test**: Take the Pi outside, open the UI on a phone, verify readability

### 10.4 Capturing Test Data from the Pi

For offline development of the UBX parser:

```bash
# SSH to Pi
ssh jaredirby@rtkbase-pi

# Capture 30 seconds of raw F9P data from TCP:5015
timeout 30 nc localhost 5015 > /tmp/gnss_capture.bin

# Copy to dev machine
scp jaredirby@rtkbase-pi:/tmp/gnss_capture.bin ./tests/fixtures/
```

This binary file can then be fed to the parser in unit tests.

---

## Implementation Order and Dependencies

```
Week 1: Foundation (parallelizable)
  Track A: db.py + migrations + conftest.py + test_sites.py
  Track B: gnss.py + test_gnss.py (with captured test data)
  Track C: auth.py + test_auth.py
  Track D: models.py (Pydantic models for all request/response shapes)

Week 2: Routes + RTKBase (depends on Week 1)
  Step 1: routes/sites.py (depends on Track A)
  Step 2: routes/status.py (depends on Track B)
  Step 3: routes/auth_routes.py (depends on Track C)
  Step 4: rtkbase.py + test_rtkbase.py (can parallel with Step 1-3)
  Step 5: routes/mode.py (depends on Step 4 + Track B)
  Step 6: routes/config_routes.py (depends on Track C)

Week 3: WebSocket + Frontend (depends on Week 2)
  Step 7: ws/live.py (depends on Track B + Step 5 for mode events)
  Step 8: main.py (wires everything together)
  Step 9: ui/index.html + css/survey365.css (can start in Week 1 with mocked data)
  Step 10: ui/js/map-core.js (can start in Week 1)
  Step 11: ui/js/ws-client.js (depends on Step 7 for message format)
  Step 12: ui/js/mode-panel.js (depends on Step 9, 10, 11)

Week 4: Deployment + Polish
  Step 13: nginx/survey365.conf
  Step 14: systemd/survey365.service
  Step 15: install.sh
  Step 16: On-Pi testing and bug fixes
  Step 17: Sunlight/glove usability testing in the field
```

Steps 9 and 10 (frontend HTML and map JS) can start as early as Week 1 using mocked API data. The frontend is pure static files with no build step, so it can be developed and tested independently in any browser.

---

## Key Design Decisions and Tradeoffs

1. **Single uvicorn worker**: Simpler shared state (GNSS reader, mode lock, WebSocket clients). Tradeoff: no parallel request handling. Mitigation: async handlers never block -- all I/O is awaited. The Pi 4's quad-core is underutilized, but RAM savings matter more.

2. **aiosqlite instead of SQLAlchemy**: Much lighter weight, fewer dependencies, easier to debug raw SQL. SpatiaLite's spatial functions are SQL-level, not ORM-level, so an ORM adds complexity without benefit.

3. **No JWT, no OAuth -- signed cookies**: Single-user system with one password. JWT token management is unnecessary overhead. Signed cookies with `itsdangerous` are simple, secure, and well-tested.

4. **TCP:5015 instead of serial port**: The PRD explicitly requires this. str2str owns `/dev/ttyGNSS`. Reading from TCP avoids contention and means Survey365 can be restarted without disrupting the GNSS data flow.

5. **Config in SQLite instead of a .env file**: Follows the GoldDigger365 pattern (settings table). Allows the web UI to read/write config without file I/O or process restart. The only file-based config is RTKBase's `settings.conf` which we read/write for base station position.

6. **Vendor JS bundled locally**: The PRD requires offline operation. No CDN dependencies. The install script downloads once; everything works afterward without internet.

7. **No HTMX for the map view**: The map is 100% Alpine.js + vanilla JS. HTMX is used only for login form and admin settings panel (where server-rendered HTML partials make sense). The map's real-time updates come via WebSocket, not HTMX polling.

8. **SpatiaLite vs. Haversine in Python**: SpatiaLite's `ST_Distance` is more accurate (uses the ellipsoid, not a sphere) and pushes computation to the database. For Phase 1 with <100 sites, the performance difference is negligible, but it sets up correctly for Phase 11 (offline tile cache spatial queries).

---

## Potential Issues on Pi 4 (2GB RAM)

1. **SpatiaLite memory**: Loading `mod_spatialite` adds ~20MB to the process. With WAL mode and small datasets, SQLite overhead is minimal.

2. **MapLibre on low-end phones**: MapLibre GL JS uses WebGL. Old Android phones may struggle. The Pi itself only serves the tiles/API -- rendering is client-side. This is a phone issue, not a Pi issue. Mitigation: use raster tiles (not vector) for Phase 1 basemaps to reduce client-side rendering cost.

3. **UBX parser CPU**: Parsing binary data at 1Hz is negligible CPU. The main cost is the TCP read loop.

4. **WebSocket with many clients**: Each WebSocket connection is an open file descriptor + coroutine. With <10 clients (realistic for a field crew), this is fine. Set `uvicorn --limit-concurrency 50` as a safety valve.

5. **SD card writes**: SQLite writes go to the SD card. WAL mode batches writes, reducing wear. The data directory should be on the main partition (not a tmpfs) so data persists across reboots.

6. **str2str restart latency**: Restarting str2str services takes 2-5 seconds on the Pi. During this time, the GNSS TCP stream drops. The GNSS reader must handle disconnection gracefully and not crash the WebSocket broadcast.

---

### Critical Files for Implementation
- `/Users/jaredirby/projects/rtk-surverying/survey365/PRD.md`
- `/Users/jaredirby/projects/rtk-surverying/base-station/install.md`
- `/Users/jaredirby/projects/rtk-surverying/base-station/rtkbase.conf`
- `/Users/jaredirby/projects/golddigger365/ui/index.html`
- `/Users/jaredirby/projects/golddigger365/ui/js/map-core.js`