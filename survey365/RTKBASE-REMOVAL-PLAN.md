# Survey365: RTKBase Removal & Native GNSS Control

## Goal

Remove all RTKBase dependency. Survey365 directly controls the F9P (and future LG290P) via serial, handles RTCM3 fan-out, NTRIP client/server, and RINEX logging natively. No more str2str processes, no settings.conf, no RTKBase web UI.

---

## Current State (What RTKBase Does)

### Active Services on Pi

| Service | What It Does | Replacement |
|---------|-------------|-------------|
| `str2str_tcp` | Reads F9P `/dev/ttyGNSS`, serves on TCP:5015 | `app/gnss/serial_reader.py` — direct serial read |
| `str2str_file` | Reads TCP:5015, writes RINEX logs | `app/gnss/rinex_logger.py` — file output in fan-out |
| `rtkbase_web` | Web UI on port 8000 | **Delete** — Survey365 IS the UI |
| str2str_ntrip_A | Push RTCM3 to Emlid Caster (currently stopped) | `app/gnss/ntrip_push.py` |
| str2str_local_ntrip_caster | Local NTRIP server (currently stopped) | `app/gnss/ntrip_caster.py` |

### Files That Reference RTKBase

| File | Reference | Change |
|------|-----------|--------|
| `app/rtkbase.py` | Reads/writes `settings.conf`, calls `systemctl` for str2str services | **Rewrite** → `app/gnss/base_station.py` |
| `app/boot.py` | Stops `str2str_tcp` to access serial port, then restarts it | **Simplify** — Survey365 owns serial from start |
| `app/routes/mode.py` | Imports `write_position`, `start_base_services`, `stop_base_services` from rtkbase | Point to new `base_station.py` |
| `app/gnss.py` | Connects to TCP:5015 to read UBX | **Rewrite** — read `/dev/ttyGNSS` directly |
| `systemd/survey365.service` | `After=str2str_tcp.service`, `ReadWritePaths=~/rtkbase/settings.conf`, `Environment=RTKBASE_DIR` | Remove RTKBase refs |
| `systemd/survey365-boot.service` | `After=str2str_tcp.service`, `Wants=str2str_tcp.service` | Remove str2str dependency |
| `nginx/survey365.conf` | Proxies `/rtkbase/` to port 8000 | Remove rtkbase upstream and location blocks |
| `install.sh` | Step 4 moves RTKBase to port 8000, prints RTKBase URLs | Remove RTKBase steps entirely |
| `CLAUDE.md` (project) | References RTKBase, settings.conf, str2str | Update |
| `PRD.md` | References RTKBase throughout | Update |

---

## New Architecture

```
/dev/ttyGNSS (F9P USB, or /dev/ttyACM0 for LG290P)
       │
       ▼
  GNSSManager (app/gnss/manager.py)
       │
       ├── Backend (receiver-specific):
       │     ├── UBloxBackend (app/gnss/ublox.py) — F9P via pyubx2
       │     └── QuectelBackend (app/gnss/quectel.py) — LG290P via NMEA (future)
       │
       ├── Reads from serial:
       │     ├── UBX/NMEA frames → parsed into GNSSState (position, sats, fix)
       │     └── RTCM3 frames → forwarded to RTCMFanout
       │
       ├── Writes to serial:
       │     ├── UBX config commands (set position, enable RTCM, antenna voltage)
       │     └── RTCM3 corrections IN (from NTRIP client, for rover/establish mode)
       │
       └── RTCMFanout (app/gnss/rtcm_fanout.py):
             ├── NTRIPPush (app/gnss/ntrip_push.py) — push to Emlid Caster
             ├── NTRIPCaster (app/gnss/ntrip_caster.py) — local server for LAN rovers
             ├── RINEXLogger (app/gnss/rinex_logger.py) — file logging
             ├── SerialOutput (future) — LoRa radio
             └── TCPOutput (optional) — TCP:5015 for backward compat
```

---

## New File Structure

```
survey365/app/gnss/
  __init__.py
  manager.py          # GNSSManager: owns serial port, dispatches frames, orchestrates
  serial_reader.py    # Async serial port reader, frame detection (UBX/NMEA/RTCM3)
  state.py            # GNSSState dataclass (position, sats, fix — extracted from current gnss.py)
  ublox.py            # UBloxBackend: UBX parser + config commands (pyubx2)
  quectel.py          # QuectelBackend: NMEA parser + config commands (future, stub only)
  rtcm_fanout.py      # Distributes RTCM3 bytes to registered outputs
  ntrip_client.py     # NTRIP client: connect to CORS, receive RTCM3 corrections
  ntrip_push.py       # NTRIP push: send RTCM3 to remote caster (Emlid, rtk2go)
  ntrip_caster.py     # Local NTRIP server: serve corrections to LAN rovers
  rinex_logger.py     # Write raw data to timestamped files for RINEX conversion
```

---

## Implementation Steps

### Step 1: Create `app/gnss/` Package — Serial Reader & State

**Files:** `__init__.py`, `serial_reader.py`, `state.py`

Move `GNSSState` from current `gnss.py` into `state.py` (unchanged).

`serial_reader.py`:
- Open `/dev/ttyGNSS` (or configured port) via `pyserial-asyncio` or threaded `pyserial`
- Read raw bytes continuously
- Detect frame boundaries:
  - UBX: sync bytes `0xB5 0x62`, class, id, length, payload, checksum
  - NMEA: `$` to `\r\n`
  - RTCM3: preamble `0xD3`, 10-bit length, payload, CRC24Q
- Emit typed frames to callbacks: `on_ubx(frame)`, `on_nmea(sentence)`, `on_rtcm3(frame)`

Frame detection is the core of this step. The current `gnss.py` already does UBX frame detection from a TCP stream — same logic, different transport. RTCM3 frame detection is new but simple (~30 lines).

**Dependencies:** `pyserial>=3.5`

**Tests (local, no hardware):**
- Feed known UBX/NMEA/RTCM3 byte sequences into the frame detector
- Verify correct frame boundary detection
- Verify mixed streams (UBX + RTCM3 interleaved) are separated correctly

### Step 2: Create UBlox Backend

**File:** `ublox.py`

UBX parsing: use `pyubx2` library for parsing received UBX frames. This replaces the manual struct.unpack parsing in current `gnss.py` with a cleaner library-based approach, but the manual parser can be kept if preferred (it works fine).

UBX configuration commands (using `pyubx2.UBXMessage` builder or the existing `boot.py` helpers):
- `configure_base_mode(lat, lon, height)` — UBX-CFG-VALSET for TMODE3 fixed position
- `configure_rover_mode()` — UBX-CFG-VALSET to disable TMODE3
- `enable_rtcm_output(messages)` — Enable RTCM3 message output on USB port
- `set_update_rate(hz)` — UBX-CFG-VALSET for measurement rate
- `enable_antenna_voltage()` — Move from boot.py, UBX-CFG-VALSET for ANT_CFG_VOLTCTRL
- `set_dynamic_model(model)` — Stationary for base, pedestrian/automotive for rover

Key UBX-CFG-VALSET key IDs needed:
```
CFG-TMODE-MODE          = 0x20030001  # 0=disabled, 1=survey-in, 2=fixed
CFG-TMODE-POS_TYPE      = 0x20030002  # 0=ECEF, 1=LLH
CFG-TMODE-LAT           = 0x40030009  # Fixed latitude (1e-7 deg, I4)
CFG-TMODE-LAT_HP        = 0x2003000A  # High-precision latitude (1e-9 deg, I1)
CFG-TMODE-LON           = 0x4003000B  # Fixed longitude
CFG-TMODE-LON_HP        = 0x2003000C  # High-precision longitude
CFG-TMODE-HEIGHT        = 0x4003000D  # Fixed height (cm, I4)
CFG-TMODE-HEIGHT_HP     = 0x2003000E  # High-precision height (0.1mm, I1)
CFG-MSGOUT-RTCM_3X_TYPE1005_USB = 0x209102BE  # RTCM 1005 on USB
CFG-MSGOUT-RTCM_3X_TYPE1077_USB = 0x209102CC  # RTCM 1077 (GPS MSM7)
CFG-MSGOUT-RTCM_3X_TYPE1087_USB = 0x209102D1  # RTCM 1087 (GLONASS MSM7)
CFG-MSGOUT-RTCM_3X_TYPE1097_USB = 0x20910318  # RTCM 1097 (Galileo MSM7)
CFG-MSGOUT-RTCM_3X_TYPE1127_USB = 0x209102D6  # RTCM 1127 (BeiDou MSM7)
CFG-MSGOUT-RTCM_3X_TYPE1230_USB = 0x20910303  # RTCM 1230 (GLONASS biases)
CFG-RATE-MEAS            = 0x30210001  # Measurement rate (ms)
CFG-HW-ANT_CFG_VOLTCTRL  = 0x10A3002E  # Antenna voltage control
```

**Dependencies:** `pyubx2>=1.2.43`

**Tests:**
- Build UBX-CFG-VALSET messages, verify bytes match expected
- Parse sample UBX-NAV-PVT and UBX-NAV-SAT frames
- Verify ACK/NAK handling

### Step 3: Create GNSS Manager

**File:** `manager.py`

The central orchestrator. Owns the serial port, instantiates the backend, routes frames.

```python
class GNSSManager:
    def __init__(self, port: str, baud: int, backend: str = "ublox"):
        self.serial_reader = SerialReader(port, baud)
        self.backend = UBloxBackend() if backend == "ublox" else QuectelBackend()
        self.state = GNSSState()
        self.rtcm_fanout = RTCMFanout()

    async def start(self):
        """Open serial, configure receiver, start reading."""
        await self.serial_reader.open()
        await self.backend.configure_initial(self.serial_reader)
        asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        """Continuously read frames, dispatch to parser or fan-out."""
        async for frame_type, frame_data in self.serial_reader.frames():
            if frame_type == "ubx":
                await self.backend.parse_ubx(frame_data, self.state)
            elif frame_type == "nmea":
                await self.backend.parse_nmea(frame_data, self.state)
            elif frame_type == "rtcm3":
                await self.rtcm_fanout.broadcast(frame_data)

    async def configure_base(self, lat, lon, height):
        """Configure receiver as fixed-position base station."""
        await self.backend.configure_base_mode(self.serial_reader, lat, lon, height)
        await self.backend.enable_rtcm_output(self.serial_reader)

    async def inject_rtcm(self, data: bytes):
        """Write RTCM3 corrections into receiver (for rover mode)."""
        await self.serial_reader.write(data)
```

This replaces both `gnss.py` (reader) and `rtkbase.py` (config/control).

### Step 4: Create RTCM Fan-out

**File:** `rtcm_fanout.py`

Simple broadcaster pattern (same as `ws/live.py`):

```python
class RTCMFanout:
    outputs: list[RTCMOutput]  # Each has async write(data: bytes)

    async def broadcast(self, data: bytes):
        for output in self.outputs:
            try:
                await output.write(data)
            except Exception:
                pass  # Log, don't crash

    def add_output(self, output: RTCMOutput): ...
    def remove_output(self, output: RTCMOutput): ...
```

### Step 5: Create RTCM Outputs

**File:** `rinex_logger.py` (~80 lines)
- Write raw bytes to timestamped files in `data/rinex/`
- Rotate files every N hours (configurable, default 24)
- Compress old files (gzip)
- Track total bytes / duration for OPUS submission readiness

**File:** `ntrip_push.py` (~100 lines)
- Connect to remote NTRIP caster (Emlid, rtk2go, etc.)
- NTRIP 1.0: `SOURCE password /mountpoint\r\n` then stream RTCM3
- NTRIP 2.0: HTTP POST with chunked transfer encoding
- Auto-reconnect on disconnect
- Configurable: host, port, mountpoint, password from NTRIP profiles table

**File:** `ntrip_caster.py` (~200 lines)
- Async HTTP server on configurable port (default 2101)
- Source table response for `GET /` with `Ntrip-Version` header
- Correction stream for `GET /MOUNTPOINT` — streams RTCM3 to connected clients
- Track connected clients (like WebSocket client set in `ws/live.py`)
- Configurable: port, mountpoint, password from config DB

**File:** `ntrip_client.py` (~150 lines)
- Connect to NTRIP caster as client (for CORS establish / rover mode)
- `GET /mountpoint HTTP/1.1` with `Ntrip-Version` and Basic auth
- Read RTCM3 stream, feed into `GNSSManager.inject_rtcm()`
- Send GGA position feedback (required by VRS casters like ALDOT CORS)
- Auto-reconnect on disconnect
- Configurable from NTRIP profiles table (type=`inbound_cors`)

### Step 6: Create Base Station Controller

**File:** `app/gnss/base_station.py` (~150 lines)

Replaces `app/rtkbase.py`. No settings.conf, no systemctl calls.

```python
async def start_base(manager: GNSSManager, lat, lon, height, outputs: list[str]):
    """Configure F9P as base, start RTCM fan-out to specified outputs."""
    await manager.configure_base(lat, lon, height)

    if "ntrip" in outputs:
        push = NTRIPPush(host, port, mountpoint, password)
        await push.connect()
        manager.rtcm_fanout.add_output(push)

    if "local_caster" in outputs:
        caster = NTRIPCaster(port=2101, mountpoint="SURVEY365")
        await caster.start()
        manager.rtcm_fanout.add_output(caster)

    if "rinex" in outputs:
        logger = RINEXLogger(data_dir="data/rinex/")
        manager.rtcm_fanout.add_output(logger)

    if "serial" in outputs:  # LoRa radio (future)
        serial_out = SerialOutput(port="/dev/ttyRADIO", baud=9600)
        manager.rtcm_fanout.add_output(serial_out)

async def stop_base(manager: GNSSManager):
    """Stop all RTCM outputs, disable RTCM generation on receiver."""
    manager.rtcm_fanout.clear_outputs()
    await manager.backend.disable_rtcm_output(manager.serial_reader)
```

### Step 7: Update `app/routes/mode.py`

Replace imports and calls:

```python
# OLD:
from ..rtkbase import start_base_services, stop_base_services, write_position

# NEW:
from ..gnss.base_station import start_base, stop_base
from ..gnss.manager import gnss_manager  # singleton
```

In `start_known_base`:
```python
# OLD:
await write_position(lat, lon, height)
await start_base_services()

# NEW:
await start_base(gnss_manager, lat, lon, height, outputs=["rinex", "local_caster"])
```

In `stop_mode`:
```python
# OLD:
await stop_base_services()

# NEW:
await stop_base(gnss_manager)
```

Similarly update `_run_relative_base`.

### Step 8: Update `app/main.py`

```python
# OLD:
from .gnss import gnss_reader
# ...
gnss_reader.start()  # connects to TCP:5015

# NEW:
from .gnss.manager import gnss_manager
# ...
await gnss_manager.start()  # opens /dev/ttyGNSS directly
```

Remove `RTKBASE_DIR` environment variable usage.

### Step 9: Update `app/boot.py`

Simplify dramatically. No more stop/start str2str_tcp dance:

```python
def main():
    # Survey365 now owns the serial port directly.
    # Antenna voltage is configured by GNSSManager on startup.
    # This boot service just ensures the udev rule is in place
    # and the serial port exists.
    if not os.path.exists("/dev/ttyGNSS"):
        log_warn("F9P not detected at /dev/ttyGNSS")
    else:
        log_info("F9P detected at /dev/ttyGNSS")
    log_info("Boot tasks complete (antenna voltage handled by Survey365 on startup)")
```

Or remove `boot.py` entirely and move antenna voltage into `GNSSManager.start()`.

### Step 10: Update `ws/live.py`

Change service status check — no more `rtkbase_web` or `str2str_*` services:

```python
# OLD:
services = ["str2str_tcp", "str2str_ntrip_A", "str2str_local_ntrip_caster", "rtkbase_web"]

# NEW:
# Report GNSS manager component status instead
services_status = {
    "gnss_connected": gnss_manager.serial_reader.is_connected,
    "rtcm_outputs": len(gnss_manager.rtcm_fanout.outputs),
    "ntrip_push": gnss_manager.rtcm_fanout.has_output("ntrip_push"),
    "local_caster": gnss_manager.rtcm_fanout.has_output("local_caster"),
    "rinex_logging": gnss_manager.rtcm_fanout.has_output("rinex"),
}
```

### Step 11: Update Nginx Config

Remove RTKBase proxy:

```nginx
# DELETE: upstream rtkbase_backend { ... }
# DELETE: location /rtkbase/ { ... }
# DELETE: location = /rtkbase { ... }
```

Only Survey365 remains.

### Step 12: Update systemd Services

**`survey365.service`:**
```ini
[Unit]
Description=Survey365 Field Controller
After=network.target
Wants=network.target
# REMOVED: After=str2str_tcp.service

[Service]
Type=simple
User={user}
Group=dialout
WorkingDirectory={home}/rtk-surveying/survey365
ExecStart={home}/rtk-surveying/survey365/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

Environment=SURVEY365_DB={home}/rtk-surveying/survey365/data/survey365.db
Environment=GNSS_PORT=/dev/ttyGNSS
Environment=GNSS_BAUD=115200
Environment=GNSS_BACKEND=ublox
# REMOVED: Environment=RTKBASE_DIR={home}/rtkbase

StandardOutput=journal
StandardError=journal
SyslogIdentifier=survey365

NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths={home}/rtk-surveying/survey365/data
# REMOVED: ReadWritePaths={home}/rtkbase/settings.conf
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Note: `Group=dialout` gives access to `/dev/ttyGNSS` without root.

**`survey365-boot.service`:**
Either simplify to just check hardware, or delete entirely (antenna voltage config moves into `GNSSManager.start()`).

### Step 13: Update `install.sh`

Remove all RTKBase references:
- Delete Step 4 (move RTKBase to port 8000)
- Remove `RTKBASE_DIR` and `RTKBASE_SETTINGS` variables
- Remove RTKBase sudoers rules (`str2str_*`, `rtkbase_web`)
- Remove RTKBase URL printing at end
- Add `dialout` group membership for serial port access: `usermod -aG dialout $TARGET_USER`
- Add udev rule deployment for `/dev/ttyGNSS` (currently managed by RTKBase installer)
- Ensure `pyubx2` and `pyserial` are in requirements.txt

New sudoers (simplified):
```
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active survey365
```

### Step 14: Add udev Rule

The current udev rule at `/etc/udev/rules.d/` was created by RTKBase's installer. We need to deploy our own:

```bash
# /etc/udev/rules.d/99-survey365-gnss.rules
# u-blox ZED-F9P
SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a9", SYMLINK+="ttyGNSS", GROUP="dialout", MODE="0660"
```

Add to `install.sh`.

### Step 15: Database Migration `003_gnss_config.sql`

Move GNSS and output configuration from settings.conf to the Survey365 database:

```sql
-- GNSS receiver configuration
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_port', '/dev/ttyGNSS');
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_baud', '115200');
INSERT OR IGNORE INTO config (key, value) VALUES ('gnss_backend', 'ublox');

-- RTCM output configuration
INSERT OR IGNORE INTO config (key, value) VALUES ('rtcm_messages', '1005(10),1077,1087,1097,1127,1230(10)');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_enabled', 'true');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_rotate_hours', '24');
INSERT OR IGNORE INTO config (key, value) VALUES ('rinex_data_dir', 'data/rinex');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_enabled', 'false');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_port', '2101');
INSERT OR IGNORE INTO config (key, value) VALUES ('local_caster_mountpoint', 'SURVEY365');
```

NTRIP profiles table already exists (`ntrip_profiles` in 001_initial.sql). It already has `type` field for `outbound_caster`, `inbound_cors`, `local_caster`.

### Step 16: Update `requirements.txt`

```
# Add:
pyubx2>=1.2.43
pyserial>=3.5
pynmeagps>=1.0.40
```

### Step 17: Delete Old Files

- `app/rtkbase.py` — replaced by `app/gnss/base_station.py`
- `app/gnss.py` — replaced by `app/gnss/manager.py` + `state.py` + `ublox.py`

### Step 18: Disable RTKBase Services on Pi

```bash
sudo systemctl disable --now str2str_tcp str2str_file str2str_ntrip_A \
    str2str_local_ntrip_caster rtkbase_web
```

RTKBase stays installed on disk (no harm) but nothing runs. Can be removed later with `rm -rf ~/rtkbase` if desired.

### Step 19: Update CLAUDE.md

Remove all RTKBase references. Update architecture description, API table, service list.

### Step 20: Frontend Updates

**`ui/index.html`** and **`ui/js/mode-panel.js`**:
- Status detail panel: replace service status badges (str2str_tcp, NTRIP Out, Local Caster) with new GNSS manager status (Serial Connected, RTCM Outputs, NTRIP Push, Local Caster)
- No functional changes to mode selection, site picking, or establish flow

**`ui/admin.html`**:
- Add NTRIP profile management section (table + add/edit/delete — the routes exist but the UI currently just links to RTKBase)
- Add GNSS configuration section (serial port, baud, backend type)
- Remove "Open RTKBase Settings" link

---

## Dependencies Between Steps

```
Step 1 (serial reader + state)
  └─► Step 2 (ublox backend) — needs frame reader
       └─► Step 3 (GNSS manager) — needs backend + state
            ├─► Step 4 (RTCM fan-out) — needs manager
            │    └─► Step 5 (outputs: rinex, ntrip push, caster, client)
            │         └─► Step 6 (base station controller)
            │              └─► Step 7 (update mode.py)
            └─► Step 8 (update main.py)

Step 9 (update boot.py)         — independent
Step 10 (update ws/live.py)     — after Step 3
Step 11 (update nginx)          — independent
Step 12 (update systemd)        — independent
Step 13 (update install.sh)     — after Steps 11, 12, 14
Step 14 (udev rule)             — independent
Step 15 (migration)             — independent
Step 16 (requirements.txt)      — independent
Step 17 (delete old files)      — after Steps 7, 8
Step 18 (disable RTKBase)       — after deployment
Step 19 (update CLAUDE.md)      — after all code changes
Step 20 (frontend updates)      — after Step 10
```

**Parallelizable work:**
- Steps 1-3 (core GNSS) are sequential
- Steps 4-5 (fan-out + outputs) can be done in parallel
- Steps 9, 11, 12, 14, 15, 16 are all independent of each other
- Frontend updates (Step 20) are independent of backend

---

## Testing Plan

### Local (no Pi, no F9P)

- Frame detector: feed known byte sequences, verify parsing
- UBX message builder: verify output bytes match expected
- NTRIP protocol: mock HTTP server, verify client connects and reads
- RTCM fan-out: verify broadcast to multiple outputs
- Mode state machine: verify transitions with mocked GNSS manager

### On Pi (with F9P)

1. **Serial connection**: verify Survey365 opens `/dev/ttyGNSS`, reads UBX frames
2. **Position/satellite display**: verify status strip and detail panel work
3. **Antenna voltage**: verify F9P antenna LED turns on at startup
4. **Known Base mode**: configure base position via UBX, verify RTCM3 output
5. **RINEX logging**: verify files written to `data/rinex/`
6. **Local NTRIP caster**: connect Emlid Reach or rtklib client to `Pi:2101/SURVEY365`, verify corrections flow
7. **NTRIP push**: configure Emlid Caster profile, verify corrections appear on caster
8. **Stop mode**: verify RTCM3 output stops, outputs disconnect cleanly
9. **Resume mode**: verify last session resumes correctly
10. **Boot cycle**: reboot Pi, verify Survey365 starts, connects to F9P, resumes

### Integration

- CORS establish (Phase 2): NTRIP client connects to ALDOT CORS, feeds RTCM3 into F9P, achieves RTK fix
- LoRa output (future): serial output to LoRa HAT

---

## Estimated Line Counts

| File | Lines | Notes |
|------|-------|-------|
| `app/gnss/__init__.py` | 5 | Exports |
| `app/gnss/serial_reader.py` | 180 | Frame detection + async serial read |
| `app/gnss/state.py` | 80 | GNSSState (moved from gnss.py) |
| `app/gnss/ublox.py` | 250 | UBX parse + config (pyubx2 + boot.py helpers) |
| `app/gnss/quectel.py` | 50 | Stub for LG290P (future) |
| `app/gnss/manager.py` | 150 | Orchestrator |
| `app/gnss/rtcm_fanout.py` | 60 | Broadcaster |
| `app/gnss/ntrip_client.py` | 150 | CORS client |
| `app/gnss/ntrip_push.py` | 120 | Caster push |
| `app/gnss/ntrip_caster.py` | 200 | Local NTRIP server |
| `app/gnss/rinex_logger.py` | 80 | File logger |
| `app/gnss/base_station.py` | 120 | Start/stop base |
| **New code total** | **~1,445** | |
| **Deleted code** | **~530** | gnss.py (340) + rtkbase.py (252) |
| **Net new** | **~915** | |

---

## Config / Environment After Migration

```ini
# systemd environment (survey365.service)
SURVEY365_DB=/home/jaredirby/rtk-surveying/survey365/data/survey365.db
GNSS_PORT=/dev/ttyGNSS
GNSS_BAUD=115200
GNSS_BACKEND=ublox

# No more:
# RTKBASE_DIR=/home/jaredirby/rtkbase
```

```
# Config DB keys (in survey365.db config table)
gnss_port = /dev/ttyGNSS
gnss_baud = 115200
gnss_backend = ublox
rtcm_messages = 1005(10),1077,1087,1097,1127,1230(10)
rinex_enabled = true
local_caster_enabled = false
local_caster_port = 2101
```

---

## Rollback Plan

If something goes wrong during migration:

```bash
# Re-enable RTKBase services
sudo systemctl enable --now str2str_tcp str2str_file rtkbase_web

# Revert to old Survey365 code
cd ~/rtk-surveying
git checkout HEAD~1  # or specific commit hash
bash survey365/scripts/update.sh
```

RTKBase is never uninstalled, just disabled. Full rollback is one git revert + systemctl enable.
