# Survey365

Map-centric field operations controller for RTK GNSS base stations. Runs on a Raspberry Pi 4 with native GNSS control.

## Tech Stack

- **Backend**: FastAPI (Python 3.13), SQLite + SpatiaLite, async
- **Frontend**: MapLibre GL JS v4, HTMX, Alpine.js, Pico.css — no build step
- **Map tiles**: MapTiler (key stored in config DB)
- **GNSS control**: Native (pyubx2 + pyserial) — direct F9P serial I/O
- **Reverse proxy**: nginx (port 80 → Survey365 :8080)
- **Process manager**: systemd

## Project Structure

```
survey365/
  app/
    main.py              # FastAPI entry point, lifespan, router registration
    db.py                # SQLite + SpatiaLite connection, config helpers
    auth.py              # PBKDF2 password hashing, session cookies
    boot.py              # Boot automation (hardware check)
    gnss/
      __init__.py        # Package exports: gnss_manager, gnss_state
      manager.py         # GNSSManager: owns serial port, dispatches frames
      serial_reader.py   # Async serial reader with UBX/NMEA/RTCM3 frame detection
      state.py           # GNSSState dataclass (position, sats, fix)
      ublox.py           # UBloxBackend: UBX parser + F9P config commands
      quectel.py         # QuectelBackend: LG290P stub (future)
      rtcm_fanout.py     # Distributes RTCM3 bytes to registered outputs
      base_station.py    # Start/stop base mode with RTCM output management
      ntrip_client.py    # NTRIP client for CORS corrections
      ntrip_push.py      # Push RTCM3 to remote caster (Emlid, rtk2go)
      ntrip_caster.py    # Local NTRIP server for LAN rovers
      rinex_logger.py    # Raw data file logging for RINEX conversion
    routes/
      status.py          # GET /api/status, GET /api/satellites
      mode.py            # POST /api/mode/{known-base,relative-base,stop,resume}
      sites.py           # Sites CRUD with proximity sorting
      ntrip.py           # NTRIP profiles CRUD
      config.py          # Config key-value read/write
      auth.py            # Login, logout, password change
    ws/
      live.py            # WebSocket /ws/live — per-client queues, 1Hz status broadcast
  ui/
    index.html           # Main map interface (Alpine.js root component)
    admin.html           # Admin panel (sites, NTRIP profiles, config, password)
    login.html           # Standalone login page
    js/
      map-core.js        # MapLibre map init, basemaps, markers, accuracy circle
      map-sites.js       # Saved point markers with proximity sorting
      status.js          # WebSocket client with HTTP polling fallback
      mode-panel.js      # Alpine.js app component (all reactive state)
    css/
      survey365.css      # All custom styles, mobile-first, high contrast
  migrations/
    001_initial.sql      # DB schema (sites, sessions, config, ntrip_profiles)
    002_projects.sql     # Projects table + project_id columns
    003_gnss_config.sql  # GNSS and RTCM output config keys
  nginx/
    survey365.conf       # Reverse proxy config
  systemd/
    survey365.service    # Main app service (Group=dialout for serial)
    survey365-boot.service # Boot hardware check
  scripts/
    update.sh            # Git pull + pip install + stamp version + restart
    stamp-version.sh     # Inject git hash into HTML for cache busting
  install.sh             # First-time Pi installer
  requirements.txt       # Python deps (pyubx2, pyserial, pynmeagps)
  data/
    survey365.db         # SQLite database (created by install, gitignored)
    rinex/               # Raw GNSS data logs (gitignored)
```

## Deploy

```bash
# On the Pi (or via SSH):
cd ~/rtk-surveying
bash survey365/scripts/update.sh
```

## First-Time Install

```bash
sudo bash survey365/install.sh --user=jaredirby
```

This installs system deps, creates venv, inits DB, deploys udev rule + nginx + systemd, starts services.

## Development

```bash
# Run locally (won't connect to F9P, but API works):
cd survey365
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
SURVEY365_DB=./data/survey365.db uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

The GNSS manager gracefully handles connection failures — it retries the serial port every 2 seconds.

## API Overview

All routes return JSON. No auth required for field endpoints (status, mode, sites). Admin endpoints (config, password) require session cookie from `/api/auth/login`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /api/status | No | GNSS + mode + services |
| GET | /api/satellites | No | Per-satellite detail |
| GET | /api/mode | No | Current operating mode |
| POST | /api/mode/known-base | No | Start base at site_id |
| POST | /api/mode/relative-base | No | Average position + start |
| POST | /api/mode/stop | No | Stop broadcasting |
| POST | /api/mode/resume | No | Resume last session |
| GET | /api/sites | No | List sites (proximity sort) |
| POST | /api/sites | Admin | Create site |
| PUT | /api/sites/:id | Admin | Update site |
| DELETE | /api/sites/:id | Admin | Delete site |
| GET | /api/ntrip | Admin | List NTRIP profiles |
| POST | /api/ntrip | Admin | Create NTRIP profile |
| PUT | /api/ntrip/:id | Admin | Update NTRIP profile |
| DELETE | /api/ntrip/:id | Admin | Delete NTRIP profile |
| GET | /api/config/public | No | MapTiler key + defaults |
| GET | /api/config | Admin | All config |
| PUT | /api/config | Admin | Update config |
| POST | /api/auth/login | No | Login (body: {password}) |
| WS | /ws/live | No | Real-time status (1Hz) |

Default admin password: `survey365`

## Key Design Decisions

- **No bcrypt**: passlib bcrypt is broken on Python 3.13. Uses stdlib `hashlib.pbkdf2_hmac` instead.
- **No SpatiaLite hard dep**: All spatial ops wrapped in try/except. Falls back to equirectangular distance calculation.
- **Single process**: All state (GNSS, mode, WS clients) is in-process. No Redis/IPC needed.
- **Native GNSS control**: Direct serial I/O to F9P via pyubx2/pyserial. No relay services, no external base-station UI.
- **Atomic mode transitions**: asyncio.Lock prevents concurrent mode changes.
- **WebSocket + polling**: Frontend tries WebSocket first, falls back to HTTP polling after 3 failures.
- **Cache busting**: `stamp-version.sh` injects git hash into HTML `?v=` params.

## GNSS Architecture

```
/dev/ttyGNSS (F9P USB)
       |
  GNSSManager (app/gnss/manager.py)
       |
       +-- UBloxBackend: UBX parse + config
       |
       +-- Reads serial -> GNSSState (position, sats, fix)
       |
       +-- RTCM3 frames -> RTCMFanout:
             +-- RINEXLogger (file output)
             +-- NTRIPPush (remote caster)
             +-- NTRIPCaster (local server)
```

## Phase 1 Scope (current)

Map UI, GNSS status, Known Point Base, Relative Base, Sites DB, NTRIP profile management, simple password auth.

## Future Phases

See PRD.md for full roadmap: CORS establish, cell hotspot, multi-rover tracking, file import (KML/DXF/DWG), OPUS auto-submit, rover mode, admin panel (WiFi/modem/system), Cloudflare Tunnel, PWA.
