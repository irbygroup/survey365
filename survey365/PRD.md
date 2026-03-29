# Survey365 — Product Requirements Document

## Overview

Survey365 is a field operations controller for RTK GNSS base stations. It runs on a Raspberry Pi alongside RTKBase, providing a map-centric mobile UI for field crews to operate in base station or rover modes, manage known points, track multiple rovers in real-time, and control all device infrastructure (WiFi, cellular modem, hotspot, remote access).

The primary interface is a full-screen map. Everything else is secondary.

---

## Users

| Role | Description | Access |
|------|-------------|--------|
| **Field crew** | Non-technical operators. Phone in sun, possibly gloves. Need one-tap operation. | Map + modes + status. No password. |
| **Admin (Jared)** | Remote management via Tailscale or Cloudflare Tunnel. Full device control. | All features. Simple password. |

---

## Hardware Platform

| Component | Detail |
|-----------|--------|
| SBC | Raspberry Pi 4 Model B (2GB RAM) |
| GNSS Receiver | ArduSimple simpleRTK2B (u-blox ZED-F9P) |
| Antenna | ArduSimple Calibrated Survey Tripleband (IP67) |
| Cellular Modem | Waveshare SIM7600G-H 4G USB Dongle |
| WiFi (internal) | wlan0 — onboard Pi 4 (fallback) |
| WiFi (external) | wlan1 — Alfa AWUS036ACH (AP mode + long range) |
| Power | Anker 523 10,000mAh USB-C PD |
| Enclosure | Pelican 1200 |
| Tripod | AdirPro 5/8" flat head survey tripod |

---

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Backend | **FastAPI** (Python) | Lightweight, async, matches existing Python tooling on Pi |
| Database | **SQLite + SpatiaLite** | No separate server process, spatial queries for point proximity |
| Frontend | **HTMX + Alpine.js** | No build step, same pattern as GoldDigger365 |
| Styling | **Pico.css** | Classless responsive, mobile-friendly, single import |
| Map | **MapLibre GL JS v4** | Open-source, vector tiles, no API key required |
| Basemaps | **MapTiler** (street/satellite/topo) + offline tile cache | Works without internet via cached tiles |
| Real-time | **WebSocket** (via FastAPI) | Live satellite status, rover positions, mode transitions |
| File parsing | **GDAL/OGR** (KML, DXF, DWG, GeoJSON, Shapefile) | Industry-standard geospatial format support |
| Reverse proxy | **Nginx** | Routes: / → Survey365, /rtkbase → RTKBase |
| GNSS engine | **RTKBase** (existing) + **RTKLIB rtkrcv** | Base station services + rover positioning engine |
| Remote access | **Cloudflare Tunnel** (cloudflared) | Public HTTPS URL, no client install needed |
| Process mgmt | **systemd** | All services as units, boot automation |

---

## Architecture

```
Phone / Laptop browser
         |
    ┌────▼────────────────────────────────────┐
    │  Nginx (port 80)                        │
    │  ├── /           → Survey365 (:8080)    │
    │  ├── /rtkbase    → RTKBase (:8000)      │
    │  └── /ws         → WebSocket (:8080)    │
    └────┬────────────────────────────────────┘
         │
    Cloudflare Tunnel (survey365.irbygroup.com)
         │
    ┌────▼────────────────────────────────────┐
    │  Survey365 (FastAPI :8080)              │
    │                                         │
    │  ├── /api/status      GNSS + NTRIP live │
    │  ├── /api/sites       Point DB CRUD     │
    │  ├── /api/mode        Operating mode    │
    │  ├── /api/rovers      Rover tracking    │
    │  ├── /api/layers      KML/DXF/DWG mgmt │
    │  ├── /api/wifi        nmcli wrapper     │
    │  ├── /api/modem       mmcli + AT cmds   │
    │  ├── /api/hotspot     AP + NAT control  │
    │  ├── /api/tunnel      Cloudflare mgmt   │
    │  ├── /api/ntrip       NTRIP profiles    │
    │  ├── /api/system      Health + updates  │
    │  ├── /api/config      All settings      │
    │  └── /ws/live         WebSocket feed    │
    └────┬──────┬──────┬──────┬───────────────┘
         │      │      │      │
         ▼      ▼      ▼      ▼
    RTKBase  rtkrcv  nmcli  mmcli
    settings         +      + AT
    .conf +        hostapd  port
    systemctl      dnsmasq
    + TCP:5015     nftables
```

---

## Primary Interface: The Map

The map is the home screen. It fills the viewport. Everything else floats on top of it or slides in from the edges.

### Map Features

| Feature | Detail |
|---------|--------|
| **Basemaps** | Street (MapTiler), Satellite (MapTiler/Google), Topo (USGS), Hybrid. Toggle via basemap picker (bottom-right). |
| **Base station marker** | Pin at the current base position. Color indicates mode: green=broadcasting, yellow=establishing, gray=idle. |
| **Rover markers** | Real-time position of all connected rovers. Different color per rover. Name label. Trail/breadcrumb of recent positions. |
| **Saved points** | All points from the database shown as markers. Tap to select, long-press for details. Nearest point highlighted. |
| **Imported overlays** | KML/DXF/DWG/GeoJSON/Shapefile layers rendered on map. Toggle visibility per layer. Supports: lines, polygons, points, labels, fills. |
| **Accuracy circle** | Translucent circle around base/rover showing current horizontal accuracy estimate. |
| **Distance/bearing** | Tap any two points to see distance and bearing between them. |
| **Phone GPS** | Blue dot for the user's phone position (browser geolocation). Used for "Find My Nail" proximity sorting. |
| **Offline tiles** | Tile cache for areas previously viewed. Works without internet. |
| **Zoom to fit** | Auto-zoom to show all active elements (base + rovers + saved points). |

### Map Controls (floating over map)

```
┌─────────────────────────────────────────┐
│ [Search]                    [Layers] [≡]│  ← top bar
│                                         │
│                                         │
│           (full-screen map)             │
│                                         │
│                                         │
│ [Mode: Broadcasting ●]                  │  ← bottom-left status
│ [SVs: 28] [NTRIP: ●] [Rovers: 2]       │
│                                         │
│                    [+][-] [Basemap] [⊕]│  ← bottom-right controls
└─────────────────────────────────────────┘
```

**Top bar:**
- Search: address, coordinates, saved point name
- Layers: toggle imported overlays, point visibility, rover trails
- Hamburger menu: mode selection, admin, settings

**Bottom-left status strip:**
- Current mode + indicator color
- Satellite count
- NTRIP status (broadcasting / receiving / off)
- Connected rover count

**Bottom-right:**
- Zoom controls
- Basemap picker
- Center on base / center on me

---

## Operating Modes

### Mode 1: Known Point Base

**Trigger:** User selects a saved point from the map or list.

**Flow:**
1. User taps a saved point on the map (or picks from list sorted by proximity)
2. Confirm: "Start base at [point name]?"
3. System writes coordinates to RTKBase settings.conf
4. Restarts str2str + NTRIP services
5. Map shows base marker (green), status strip shows "Broadcasting"

**Services active:** str2str_tcp, str2str_ntrip_A (Emlid Caster), str2str_local_ntrip_caster, str2str_file (RINEX logging)

### Mode 2: CORS Establish → Base

**Trigger:** User taps "Establish New Point" → "Quick (ALDOT CORS)"

**Flow:**
1. System stops base services
2. Starts rtkrcv as rover, connects to ALDOT CORS NTRIP
3. Map shows rover marker (yellow pulsing) at current position
4. Status: "Locking... 28 SVs, waiting for RTK fix"
5. RTK fix achieved → "Fixed — 0.02m — Averaging 60s..."
6. Progress bar counts down
7. "Save as:" prompt → user names the point
8. System saves to DB → switches to Known Point Base mode
9. Map marker turns green → "Broadcasting from [name]"

**Services active during establish:** str2str_tcp, rtkrcv (rover mode)
**Services active after establish:** same as Mode 1

### Mode 3: OPUS Establish → Base

**Trigger:** User taps "Establish New Point" → "Precise (OPUS)"

**Flow:**
1. System averages position for 60 seconds
2. Starts broadcasting with averaged coords (yellow indicator: "TEMPORARY")
3. RINEX logging begins
4. Timer shows: "Logging for OPUS... 0:24:00 / 2:00:00 minimum"
5. After 2+ hours: "Ready to submit to OPUS" notification
6. User taps "Submit" (or auto-submit if enabled)
7. System trims RINEX → submits to OPUS → polls for results
8. When results arrive: auto-updates position, saves to DB
9. Yellow → green: "Position refined — [name] (OPUS, +/-0.02m)"

**User can work the entire time.** Rovers receive corrections throughout. Position just gets more accurate when OPUS results arrive.

### Mode 4: Relative Base

**Trigger:** User taps "Establish New Point" → "Relative (quick)"

**Flow:**
1. System averages for 120 seconds
2. Starts broadcasting
3. Orange indicator: "RELATIVE — not tied to datum"
4. Optionally save the point

### Mode 5: Rover

**Trigger:** User taps "Rover Mode"

**Flow:**
1. System stops all base services
2. User selects NTRIP source (ALDOT CORS, or custom profile)
3. Starts rtkrcv with selected correction source
4. Map shows rover position in real-time with accuracy circle
5. Status: "Rover — RTK Fixed — 0.014m"
6. Position displayed on map + in coordinate readout panel
7. Can log points: tap "Mark Point" → saves current position to DB with name/notes

**Output:** NMEA on TCP port (configurable) for external apps to connect.

### Mode 6: Resume Last Session

**Trigger:** User taps "Resume Last" (or auto-resume on boot if enabled)

**Flow:**
1. Loads last active mode + site from database
2. Starts appropriate services
3. Resumes as if never stopped

---

## Multi-Rover Tracking

### How Rovers Connect

Rovers (Emlid Reach RX, other NTRIP clients) connect to the base station's NTRIP caster to receive corrections. Survey365 tracks them.

**Tracking methods (in priority order):**

1. **NTRIP client connections** — RTKBase's local NTRIP caster logs connected clients (IP + mount point). Survey365 reads this to know how many rovers are connected. No position data from this method alone.

2. **Rover position reporting** — Rovers that can output NMEA on a TCP port (Reach RX supports this). Survey365 connects to each rover's NMEA stream to get real-time position. Requires rovers to be on the same network (hotspot).

3. **Survey365 rover client** — A rover running Survey365 in their phone browser uses browser geolocation to send position updates via WebSocket. Lower accuracy than method 2 (phone GPS vs RTK), but works with any rover.

### Rover Map Display

| Element | Detail |
|---------|--------|
| Marker | Colored dot per rover, unique color auto-assigned |
| Label | Rover name (user-configurable, or auto: "Rover 1", "Rover 2") |
| Trail | Breadcrumb line of last N positions (fades with age) |
| Accuracy | Circle around marker sized to reported accuracy |
| Status | Fix type badge: RTK Fixed (green), RTK Float (yellow), Autonomous (red) |
| Stale | Marker grays out if no update in 30 seconds |

### Rover Management (admin)

- Name/rename connected rovers
- View position, accuracy, fix type, last update
- Kick rover from NTRIP caster
- Set rover NMEA TCP endpoint for position tracking

---

## File Import: KML / DXF / DWG / GeoJSON / Shapefile

Field crews need to see site plans, property boundaries, grading contours, and design files on the map while they work.

### Supported Formats

| Format | Extension | Parser | Notes |
|--------|-----------|--------|-------|
| KML/KMZ | .kml, .kmz | GDAL/OGR (osgeo.ogr) | Google Earth format. Points, lines, polygons, labels. |
| DXF | .dxf | ezdxf + GDAL | AutoCAD exchange format. Lines, polylines, circles, text, blocks. |
| DWG | .dwg | GDAL/OGR (with libdwg) | Native AutoCAD. Convert to DXF internally if GDAL can't read directly. |
| GeoJSON | .geojson, .json | Native (json.load) | Web standard. Direct to MapLibre. |
| Shapefile | .shp (+.dbf, .prj) | GDAL/OGR | ESRI format. Upload as zip containing all component files. |

### Import Flow

1. User taps "Layers" → "Import File"
2. File picker (or drag-and-drop on desktop)
3. System parses file → extracts geometries → reprojects to WGS84 if needed
4. Preview on map with default styling
5. User names the layer, sets color/opacity
6. Layer saved to database (GeoJSON internally)
7. Appears in layer toggle list

### Layer Management

- Toggle visibility per layer
- Reorder layers (draw order)
- Edit style: line color, fill color, opacity, line width
- Delete layer
- View feature attributes on tap (from DXF/KML metadata)
- Zoom to layer extent

### Storage

Imported files converted to GeoJSON and stored in SQLite. Original file also kept on disk for re-export. Layers table:

```
layers:
  id, name, file_type, geojson (TEXT), style_json,
  original_filename, original_path, visible (BOOL),
  draw_order (INT), created_at, notes
```

---

## Projects

All work is organized into projects. Sites, sessions, and (future) layers are scoped to the active project. The app gates on startup — the user must select or create a project before the map becomes usable.

### Schema

```
projects:
  id            INTEGER PRIMARY KEY
  name          TEXT NOT NULL
  description   TEXT
  client        TEXT
  created_at    TEXT
  updated_at    TEXT
  last_accessed TEXT
```

### Behavior

- **Gate on load:** If no active project, a full-screen overlay requires project selection before the app is usable.
- **Auto-open last:** On subsequent loads, the last-used project is auto-activated (no gate shown).
- **Switch project:** Available in the hamburger menu. Stops any active mode before switching.
- **Project tag:** The active project name appears in the top bar. Tapping it opens the switcher.
- **Admin filter:** The admin panel can filter sites by project or view all projects.
- **Migration:** On upgrade from a pre-project database, existing sites are auto-assigned to a "Default Project" which is auto-activated.

### API

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/projects | List all projects (sorted by last_accessed) |
| GET | /api/projects/active | Get the currently active project |
| POST | /api/projects | Create project. Body: {name, description?, client?} |
| PUT | /api/projects/:id | Update project |
| DELETE | /api/projects/:id | Delete project (fails if has sites) |
| POST | /api/projects/:id/activate | Set as active project |

---

## Saved Points Database

### Schema

```
sites:
  id              INTEGER PRIMARY KEY
  name            TEXT NOT NULL
  lat             REAL NOT NULL
  lon             REAL NOT NULL
  height          REAL                -- ellipsoidal height (meters)
  ortho_height    REAL                -- orthometric height (meters, if known)
  datum           TEXT DEFAULT 'NAD83(2011)'
  epoch           TEXT DEFAULT '2010.0'
  source          TEXT                -- 'manual' | 'cors_rtk' | 'opus' | 'averaged' | 'imported'
  accuracy_h      REAL                -- horizontal accuracy (meters)
  accuracy_v      REAL                -- vertical accuracy (meters)
  established     TEXT                -- ISO date
  last_used       TEXT                -- ISO date
  notes           TEXT
  photo_path      TEXT                -- path to photo on disk
  opus_job_id     TEXT                -- OPUS submission ID if pending
  project_id      INTEGER REFERENCES projects(id)
  geom            POINT               -- SpatiaLite geometry for spatial queries
```

### Features

- **Project-scoped:** Sites are scoped to the active project. Creating a site auto-assigns it. Queries filter by active project unless `all_projects=true` is passed.
- **Proximity sort:** Uses phone GPS (browser geolocation) + SpatiaLite distance query to sort by nearest first.
- **Find My Nail:** Compass bearing + distance to selected point using phone GPS. Updates in real-time as user walks.
- **Map markers:** All saved points shown on map. Tap to select, long-press for detail panel.
- **Import/Export:** CSV, GeoJSON. Bulk import from spreadsheet.
- **Photo:** Camera capture of PK nail / monument location. Stored on Pi disk, displayed in detail view.
- **Search:** By name, notes, date range, source type, proximity radius.
- **OPUS auto-update:** When OPUS results arrive, site record auto-updates with precise coordinates.

---

## NTRIP Profile Management

### Schema

```
ntrip_profiles:
  id              INTEGER PRIMARY KEY
  name            TEXT NOT NULL         -- "Emlid Caster", "ALDOT CORS", "Local"
  type            TEXT NOT NULL         -- 'outbound_caster' | 'inbound_cors' | 'local_caster'
  host            TEXT
  port            INTEGER
  mountpoint      TEXT
  username        TEXT
  password         TEXT
  is_default      BOOL DEFAULT FALSE
  notes           TEXT
```

### Profile Types

| Type | Direction | Use |
|------|-----------|-----|
| outbound_caster | Base → Caster | Emlid Caster, custom NTRIP caster |
| inbound_cors | CORS → Rover | ALDOT CORS, any correction source |
| local_caster | Base → WiFi rovers | Local NTRIP on Pi |

---

## Cell Hotspot Mode

The Alfa WiFi adapter runs as an access point while the SIM7600 modem provides internet uplink. The Pi acts as a NAT router.

### Network Topology

```
             Cell tower
                 |
         SIM7600 (wwan0)
                 |
          Pi 4 (NAT routing)
                 |
         Alfa adapter (wlan1) — AP mode
                 |
    ┌────────────┼────────────┐
  Crew         Reach RX     Laptop
  phones       (NTRIP)
```

### Components

| Service | Role |
|---------|------|
| hostapd | Runs AP on wlan1 (SSID: "Survey365", configurable) |
| dnsmasq | DHCP (192.168.50.x) + DNS on AP interface |
| nftables | NAT masquerade from wlan1 → wwan0 |

### Hotspot Config

```
hotspot:
  ssid            TEXT DEFAULT 'Survey365'
  password        TEXT DEFAULT 'survey365'
  channel         INTEGER DEFAULT 6
  band            TEXT DEFAULT '2.4ghz'     -- or '5ghz'
  enabled         BOOL DEFAULT FALSE
  auto_enable     BOOL DEFAULT TRUE         -- auto-enable when no known WiFi in range
```

### Behavior

- **Manual toggle:** Admin can enable/disable hotspot from UI.
- **Auto-enable:** If no known WiFi network connects within 60 seconds of boot, automatically switch Alfa to AP mode and enable NAT.
- **Simultaneous:** When hotspot is active, Pi still has internet via cell modem. Rovers get NTRIP corrections over WiFi AND phones get internet for Emlid Flow, maps, etc.
- **Captive portal:** When a phone connects to "Survey365" WiFi, auto-redirect to the Survey365 web UI.

---

## WiFi Management

### Features

- View all configured networks (from rtkbase.conf WIFI_n entries)
- Add new network: SSID, password, priority, metric
- Remove network
- View connection status per adapter (wlan0, wlan1)
- View signal strength of connected network
- Scan for available networks
- Re-run setup-wifi.sh after changes

### Display

| Field | Source |
|-------|--------|
| SSID | rtkbase.conf |
| Connected | nmcli device status |
| Signal | nmcli -f SSID,SIGNAL device wifi list |
| Interface | wlan0 / wlan1 |
| IP address | ip addr show |

---

## Cellular Modem Management

### Features

| Feature | Implementation |
|---------|---------------|
| Connection status | mmcli -m 0 |
| Signal strength | mmcli -m 0 --signal-get, AT+CSQ |
| Carrier name | mmcli -m 0 |
| Network type (LTE/3G) | mmcli -m 0 |
| SIM status | mmcli -i 0 |
| IMEI (current) | mmcli -m 0 \| grep imei |
| IMEI (original) | rtkbase.conf ORIGINAL_IMEI |
| Generate new IMEI | Run imei-generator/generate.py |
| Set IMEI on modem | AT+SIMEI=xxx via serial |
| APN settings | mmcli -m 0 --simple-connect |
| Data usage | AT+CGDCONT? or track via iptables counters |
| Modem reboot | AT+CFUN=1,1 |

### Display

Simple status card:
```
Cellular: Connected
Carrier: T-Mobile LTE
Signal: ████░░ -71 dBm (Good)
IMEI: 352741384997469 (Samsung Galaxy A10e)
Data: ~12 MB today
```

---

## Remote Access

### Cloudflare Tunnel

| Setting | Detail |
|---------|--------|
| Service | cloudflared (systemd) |
| URL | survey365.irbygroup.com (configurable) |
| Routes | localhost:80 (Nginx) |
| Auth | Cloudflare Access (email OTP) or app-level password |

### Admin Controls

- Toggle tunnel on/off
- View tunnel status (connected/disconnected)
- View public URL
- View connected clients

### Tailscale (existing)

- View Tailscale IP and hostname
- View connection status
- Already configured, no changes needed

---

## System Management

### Update Flow

```
"Check for Updates" button
    |
    v
git fetch origin main
git log HEAD..origin/main --oneline  →  show commit list
    |
    v  (user confirms)
git pull origin main
pip install -r requirements.txt  (if changed)
systemctl restart survey365
systemctl restart rtkbase_web  (if needed)
    |
    v
Page auto-reconnects → "Updated to [commit hash]"
```

### Auto-Update on Boot (optional toggle)

On every boot, before starting Survey365:
1. If internet available, `git pull origin main`
2. If requirements.txt changed, `pip install -r requirements.txt`
3. Start services

### System Controls

| Action | Command | Confirmation |
|--------|---------|-------------|
| Restart services | systemctl restart survey365 rtkbase_web str2str_tcp | "Restarting... reconnecting in 5s" |
| Reboot device | sudo reboot | "Reboot? You'll reconnect in ~30 seconds." |
| Shut down | sudo shutdown now | "Shut down? You'll need physical access to power back on." |

### System Health Display

```
CPU: 47°C
Disk: 4.5 GB / 59 GB (8%)
RAM: 253 MB / 3.7 GB
Uptime: 4 days, 7 hours
Battery: ~4.2 hours remaining (estimated)
RTKBase: v2.7.0
Survey365: v0.1.0 (commit abc1234)
Last update: 2026-03-28 14:22
```

---

## Configuration (Admin Panel)

Everything that would otherwise require SSH is accessible from the admin panel.

### GNSS / RTKBase

- Base position (manual entry or select from saved point)
- Receiver settings (port, baud rate, format)
- Antenna info string
- RTCM message selection (checkboxes per message type)
- Update rate

### NTRIP

- Outbound caster profiles (Emlid, custom)
- Inbound CORS profiles (ALDOT, custom)
- Local caster settings (port, mountpoint, password)
- Default profiles per mode

### RINEX / Logging

- Log rotation interval (hours)
- Archive retention (days)
- Minimum free disk space (MB)
- Auto-submit to OPUS after N hours (toggle + threshold)

### WiFi

- Network list (add/edit/remove)
- Priority and metric per network
- Hotspot settings (SSID, password, channel, auto-enable)

### Cellular

- APN settings
- IMEI management
- Data usage reset

### Remote Access

- Cloudflare Tunnel toggle + URL
- Tailscale status (read-only)

### System

- Hostname
- Auto-update on boot (toggle)
- F9P antenna voltage auto-enable on boot (toggle)
- Web UI password

### Import / Export

- Export all saved points (CSV, GeoJSON)
- Import points (CSV, GeoJSON)
- Export all config (JSON backup)
- Import config (JSON restore)

---

## Authentication

**Simple password.** No user accounts, no roles, no sessions management beyond a cookie.

- **Field screens** (map, status, mode selection): **No password.** Open access. Anyone on the network can use the base station.
- **Admin screens** (config, WiFi, modem, system, shutdown): **Password required.** Single shared password, stored hashed in SQLite. Set on first boot.
- **Implementation:** Session cookie set after password entry. Expires after 24 hours or on browser close. HTMX handles the login form inline.

---

## Boot Automation

On power-on, the following happens automatically (systemd):

1. Pi boots (15-20 seconds)
2. NetworkManager connects WiFi (wlan0 + wlan1) or enables hotspot if no WiFi found
3. ModemManager connects cell modem
4. Tailscale connects
5. cloudflared connects tunnel
6. RTKBase starts (str2str_tcp, rtkbase_web)
7. **Survey365 starts:**
   a. Enable F9P antenna voltage (UBX-CFG-VALSET)
   b. Load last session from database
   c. If auto-resume enabled: start last mode
   d. Else: wait on map screen (IDLE)
8. **Within 30-60 seconds of power-on:** satellites locked, corrections flowing (if auto-resume)

---

## API Endpoints

### Status & GNSS

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/status | Current GNSS status (fix, SVs, position, accuracy, mode) |
| GET | /api/satellites | Satellite detail (per-SV C/N0, constellation, used/visible) |
| WS | /ws/live | WebSocket: real-time status + rover positions + mode transitions |

### Operating Mode

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/mode | Current mode + state |
| POST | /api/mode/known-base | Start known point base. Body: {site_id} |
| POST | /api/mode/cors-establish | Start CORS establish. Body: {ntrip_profile_id} |
| POST | /api/mode/opus-establish | Start OPUS establish |
| POST | /api/mode/relative-base | Start relative base |
| POST | /api/mode/rover | Start rover. Body: {ntrip_profile_id} |
| POST | /api/mode/stop | Stop current mode → IDLE |
| POST | /api/mode/resume | Resume last session |

### Projects

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/projects | List all projects (sorted by last_accessed) |
| GET | /api/projects/active | Get currently active project |
| POST | /api/projects | Create project. Body: {name, description?, client?} |
| PUT | /api/projects/:id | Update project |
| DELETE | /api/projects/:id | Delete project (fails if has sites) |
| POST | /api/projects/:id/activate | Set as active project |

### Sites (Points)

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/sites | List sites (scoped to active project). Query: ?near_lat=&near_lon=&all_projects=true |
| GET | /api/sites/:id | Get site detail |
| POST | /api/sites | Create site |
| PUT | /api/sites/:id | Update site |
| DELETE | /api/sites/:id | Delete site |
| POST | /api/sites/import | Import CSV or GeoJSON |
| GET | /api/sites/export | Export all as CSV or GeoJSON |

### Rovers

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/rovers | List connected rovers + positions |
| PUT | /api/rovers/:id | Rename rover |
| DELETE | /api/rovers/:id | Kick rover from caster |

### Layers (File Import)

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/layers | List imported layers |
| POST | /api/layers | Upload + import file (KML/DXF/DWG/GeoJSON/SHP) |
| GET | /api/layers/:id/geojson | Get layer as GeoJSON (for MapLibre) |
| PUT | /api/layers/:id | Update name, style, visibility, draw order |
| DELETE | /api/layers/:id | Delete layer |

### NTRIP Profiles

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/ntrip | List profiles |
| POST | /api/ntrip | Create profile |
| PUT | /api/ntrip/:id | Update profile |
| DELETE | /api/ntrip/:id | Delete profile |

### Infrastructure

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/wifi | List networks + connection status |
| POST | /api/wifi | Add network |
| DELETE | /api/wifi/:id | Remove network |
| GET | /api/modem | Modem status (signal, carrier, IMEI, SIM) |
| POST | /api/modem/imei | Generate + set new IMEI |
| GET | /api/hotspot | Hotspot status + config |
| POST | /api/hotspot/toggle | Enable/disable hotspot |
| PUT | /api/hotspot | Update hotspot settings |
| GET | /api/tunnel | Cloudflare Tunnel status |
| POST | /api/tunnel/toggle | Enable/disable tunnel |

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/system | CPU temp, disk, RAM, uptime, versions |
| POST | /api/system/update | Git pull + restart |
| POST | /api/system/restart | Restart services |
| POST | /api/system/reboot | Reboot device |
| POST | /api/system/shutdown | Shut down device |
| GET | /api/config | All config settings |
| PUT | /api/config | Update config settings |

### Auth

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/auth/login | Verify password, set session cookie |
| POST | /api/auth/logout | Clear session |
| PUT | /api/auth/password | Change password |

---

## Database Schema

```sql
-- Saved survey points
CREATE TABLE sites (
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
    project_id      INTEGER REFERENCES projects(id),
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
SELECT AddGeometryColumn('sites', 'geom', 4326, 'POINT', 'XY');
CREATE INDEX idx_sites_geom ON sites USING rtree(geom);

-- Imported map layers
CREATE TABLE layers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    file_type       TEXT NOT NULL,
    geojson         TEXT NOT NULL,
    style_json      TEXT DEFAULT '{}',
    original_filename TEXT,
    original_path   TEXT,
    visible         INTEGER DEFAULT 1,
    draw_order      INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    notes           TEXT
);

-- NTRIP connection profiles
CREATE TABLE ntrip_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK(type IN ('outbound_caster','inbound_cors','local_caster')),
    host            TEXT,
    port            INTEGER,
    mountpoint      TEXT,
    username        TEXT,
    password        TEXT,
    is_default      INTEGER DEFAULT 0,
    notes           TEXT
);

-- Survey projects (group sites + sessions)
CREATE TABLE projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    client          TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    last_accessed   TEXT
);

-- Session history (for resume + logging)
CREATE TABLE sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mode            TEXT NOT NULL,
    site_id         INTEGER REFERENCES sites(id),
    ntrip_profile_id INTEGER REFERENCES ntrip_profiles(id),
    project_id      INTEGER REFERENCES projects(id),
    started_at      TEXT DEFAULT (datetime('now')),
    ended_at        TEXT,
    rinex_path      TEXT,
    opus_submitted  INTEGER DEFAULT 0,
    opus_result     TEXT,
    notes           TEXT
);

-- Connected rover tracking
CREATE TABLE rovers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    ip_address      TEXT,
    nmea_port       INTEGER,
    last_lat        REAL,
    last_lon        REAL,
    last_height     REAL,
    last_accuracy   REAL,
    fix_type        TEXT,
    last_seen       TEXT,
    color           TEXT
);

-- WiFi networks (mirror of rtkbase.conf WIFI_n entries)
CREATE TABLE wifi_networks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ssid            TEXT NOT NULL,
    priority        INTEGER DEFAULT 10,
    metric          INTEGER DEFAULT 50,
    -- password stored in NetworkManager, not here
    created_at      TEXT DEFAULT (datetime('now'))
);

-- App configuration (key-value)
CREATE TABLE config (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Default config entries:
-- 'web_password_hash', 'hotspot_ssid', 'hotspot_password', 'hotspot_channel',
-- 'hotspot_auto_enable', 'auto_resume', 'auto_update_on_boot',
-- 'antenna_voltage_on_boot', 'opus_auto_submit_hours',
-- 'maptiler_key', 'cloudflare_tunnel_enabled', 'f9p_update_rate'
```

---

## Build Phases

| Phase | Scope | Outcome |
|-------|-------|---------|
| **1** | Map UI + status + known point base + relative base + point DB + project organization + simple password auth | Field-usable base station with map interface. Project gate, switcher, project-scoped sites/sessions. **DONE.** |
| **2** | CORS establish mode (rtkrcv) + NTRIP profile management | Auto-precise positioning from ALDOT CORS |
| **3** | File import (KML/DXF/DWG/GeoJSON/SHP) + layer management | Site plans and design files on the map |
| **4** | OPUS pipeline (auto-trim RINEX + submit + result import) | Survey-grade accuracy automation |
| **5** | Admin panel (WiFi, modem, IMEI, system health, config) | Complete device management from browser |
| **6** | Cloudflare Tunnel + remote access | Office monitoring and remote control |
| **7** | System management (update, reboot, shutdown) + boot automation | Self-maintaining field unit |
| **8** | Offline tile cache + PWA manifest + Find My Nail | Production polish for daily field use |
| **9** | Multi-rover tracking on map + rover management | See all crew positions in real-time |
| **10** | Rover mode (Pi as rover) + point marking | Full survey capability without Reach RX |
| **11** | Cell hotspot (hostapd + NAT + dnsmasq) + captive portal | Self-contained field unit, no external WiFi needed |

---

## File Structure

```
survey365/
├── PRD.md                      # This document
├── app/
│   ├── main.py                 # FastAPI app, registers routes
│   ├── db.py                   # SQLite + SpatiaLite connection
│   ├── config.py               # App config loader
│   ├── auth.py                 # Password check + session middleware
│   ├── gnss.py                 # F9P serial communication (UBX parser)
│   ├── rtkbase.py              # RTKBase settings.conf read/write + service control
│   ├── rtkrcv.py               # RTKLIB rtkrcv process management
│   ├── modem.py                # mmcli + AT command wrappers
│   ├── wifi.py                 # nmcli wrappers
│   ├── hotspot.py              # hostapd + dnsmasq + NAT management
│   ├── tunnel.py               # cloudflared management
│   ├── opus.py                 # OPUS submission + result parsing
│   ├── importers/
│   │   ├── kml.py              # KML/KMZ parser
│   │   ├── dxf.py              # DXF parser (ezdxf)
│   │   ├── dwg.py              # DWG parser (GDAL or convert→DXF)
│   │   ├── geojson.py          # GeoJSON passthrough
│   │   └── shapefile.py        # Shapefile parser (GDAL)
│   ├── routes/
│   │   ├── status.py           # /api/status, /api/satellites
│   │   ├── mode.py             # /api/mode/*
│   │   ├── projects.py         # /api/projects CRUD + activate
│   │   ├── sites.py            # /api/sites CRUD (project-scoped)
│   │   ├── rovers.py           # /api/rovers
│   │   ├── layers.py           # /api/layers
│   │   ├── ntrip.py            # /api/ntrip
│   │   ├── wifi.py             # /api/wifi
│   │   ├── modem.py            # /api/modem
│   │   ├── hotspot.py          # /api/hotspot
│   │   ├── tunnel.py           # /api/tunnel
│   │   ├── system.py           # /api/system
│   │   ├── config.py           # /api/config
│   │   └── auth.py             # /api/auth
│   └── ws/
│       └── live.py             # WebSocket handler (status + rover positions)
├── ui/
│   ├── index.html              # Map shell (primary interface)
│   ├── admin.html              # Admin panel
│   ├── login.html              # Password entry
│   ├── js/
│   │   ├── map-core.js         # MapLibre setup, layer registry, basemaps
│   │   ├── map-rovers.js       # Rover marker management
│   │   ├── map-layers.js       # Imported layer rendering
│   │   ├── map-sites.js        # Saved point markers
│   │   ├── mode-panel.js       # Mode selection + status UI
│   │   └── find-nail.js        # Compass bearing + distance
│   ├── css/
│   │   └── survey365.css       # Custom styles (minimal, on top of Pico)
│   └── assets/
│       ├── icons/              # Map markers, mode icons
│       └── manifest.json       # PWA manifest
├── migrations/
│   ├── 001_initial.sql         # Core tables (sites, sessions, config, ntrip_profiles)
│   └── 002_projects.sql        # Projects table + project_id on sites/sessions
├── systemd/
│   ├── survey365.service       # Main app
│   ├── survey365-hotspot.service # Hotspot management
│   └── survey365-boot.service  # Boot automation (antenna voltage, auto-resume)
├── nginx/
│   └── survey365.conf          # Reverse proxy config
├── requirements.txt            # Python dependencies
└── install.sh                  # Installer script for Pi
```

---

## Constraints

| Constraint | Detail |
|------------|--------|
| RAM | 2GB shared with RTKBase, OS, services. Target <200MB for Survey365. |
| CPU | Pi 4 quad-core ARM. Avoid heavy computation. GDAL file parsing is the heaviest operation. |
| Storage | 59GB SD card. RINEX logs are the main consumer (~50MB/day). |
| Network | Cell modem may be slow/unreliable. All UI assets must be bundled locally. No CDN. |
| Offline | Must work fully offline when in hotspot mode with no cell signal. Only external services (OPUS, Emlid Caster, ALDOT CORS, Cloudflare) require internet. |
| Sunlight | UI must be readable in direct Alabama sun. High contrast, large touch targets. |
| Gloves | Touch targets minimum 48px. No hover-dependent interactions. |
| No build step | Plain HTML/JS/CSS. No webpack, no npm, no compilation for frontend. |
