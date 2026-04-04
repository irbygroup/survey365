---
name: survey365-gnss
description: Domain knowledge for the Survey365 GNSS/RTK subsystem. Use when working on serial GNSS control, UBX protocol, RTCM corrections, NTRIP networking, RTKLIB integration, base station modes, or rover positioning. Covers u-blox ZED-F9P, pyubx2/pyserial, and the full data flow from antenna to correction output.
---

# Survey365 GNSS Subsystem

## Architecture Overview

Survey365 implements native GNSS control — it talks directly to a u-blox ZED-F9P receiver over USB serial, bypassing any external relay layer. The data flow is:

```
ZED-F9P antenna
      │
  /dev/ttyGNSS (USB serial, 115200 baud, udev symlink)
      │
  SerialReader (background thread, frame detection)
      │
      ├── UBX frames ──► UBloxBackend.parse_frame() ──► GNSSState (in-memory)
      │                                                       │
      │                                                       ▼
      │                                              WebSocket broadcast
      │                                              (status, satellites, mode)
      │
      ├── RTCM3 frames ──► RTCMFanout ──► NTRIPPush (outbound to caster)
      │                         │     └──► RINEXLogger (raw file logging)
      │                         │     └──► NTRIPCaster proxy (local LAN)
      │                         │
      │                    (native engine only; skipped in rtklib engine mode)
      │
      └── Raw bytes ──► RawRelay (TCP 127.0.0.1:5015)
                              │
                              └──► RTKLIB str2str (encodes UBX→RTCM3)
                                        │
                                        ├──► ntripc:// local caster (:2110 internal)
                                        ├──► ntrips:// outbound push
                                        └──► file:// RINEX logging
```

## Key Source Files

| File | Purpose |
|------|---------|
| `app/gnss/__init__.py` | Package init, exports `gnss_manager` singleton and `gnss_state` |
| `app/gnss/manager.py` | `GNSSManager` — central orchestrator: serial port, backend, state, RTCM fan-out |
| `app/gnss/serial_reader.py` | Background-threaded serial reader with UBX/NMEA/RTCM3 frame detection |
| `app/gnss/state.py` | `GNSSState` dataclass — thread-safe in-memory state (asyncio.Lock) |
| `app/gnss/ublox.py` | `UBloxBackend` — UBX frame parsing and CFG-VALSET configuration commands |
| `app/gnss/rtcm.py` | RTCM helpers: `parse_rtcm_message_type()`, `build_rtcm_1006()`, `llh_to_ecef()` |
| `app/gnss/rtcm_fanout.py` | `RTCMFanout` — distributes RTCM3 frames to registered `RTCMOutput` consumers |
| `app/gnss/raw_relay.py` | `RawRelay` — loopback TCP relay at `127.0.0.1:5015` for RTKLIB consumption |
| `app/gnss/base_station.py` | `start_base()` / `stop_base()` — orchestrates receiver config + output stack |
| `app/gnss/ntrip_caster.py` | `NTRIPCaster` — reverse proxy for RTKLIB's internal caster on port 2110 |
| `app/gnss/ntrip_client.py` | `NTRIPClient` — receives RTCM3 corrections from remote CORS/VRS for establish mode |
| `app/gnss/ntrip_push.py` | `NTRIPPush` — pushes RTCM3 to remote casters (Emlid, RTK2Go, etc.) |
| `app/gnss/rinex_logger.py` | `RINEXLogger` — writes raw RTCM3 bytes to timestamped rotating files |
| `app/gnss/quectel.py` | `QuectelBackend` — stub for future LG290P support |
| `app/rtklib/launcher.py` | Builds and exec's RTKLIB `str2str` commands from `active-base.json` |
| `app/rtklib/runtime.py` | Reads/writes `active-base.json` runtime config for RTKLIB processes |

## Two RTCM Engines

Survey365 supports two correction-encoding engines, selectable via `rtcm_engine` config key:

### Native Engine (`rtcm_engine = "native"`)
- F9P generates RTCM3 directly on USB (enabled via UBX-CFG-VALSET)
- `SerialReader` detects RTCM3 frames (preamble `0xD3`, CRC24Q validation)
- Frames are broadcast through `RTCMFanout` to registered outputs
- The manager injects synthetic RTCM 1006 (reference station with antenna height) when the F9P doesn't emit 1005/1006 natively
- Default RTCM messages: `1005, 1077, 1087, 1097, 1127, 1230(10)`

### RTKLIB Engine (`rtcm_engine = "rtklib"`)
- F9P outputs raw UBX measurements (RXM-RAWX, RXM-SFRBX) instead of RTCM
- `RawRelay` streams raw bytes to `127.0.0.1:5015`
- Separate systemd services run `str2str -in tcpcli://127.0.0.1:5015#ubx -out ...#rtcm3`
- RTKLIB encodes UBX→RTCM3 with full message set: `1004, 1005(10), 1006, 1008(10), 1012, 1019, 1020, 1033(10), 1042, 1045, 1046, 1077, 1087, 1097, 1107, 1127, 1230`
- Survey365 still runs a `NTRIPCaster` proxy on port 2101 that reverse-proxies RTKLIB's internal caster on port 2110

## UBX Protocol Reference

The u-blox Binary protocol (UBX) uses this frame format:
```
Sync1(0xB5) Sync2(0x62) Class(1) ID(1) Length(2, little-endian) Payload(N) CK_A(1) CK_B(1)
```

Checksum: Fletcher-8 over Class + ID + Length + Payload bytes.

### Messages Used by Survey365

| Class | ID | Name | Direction | Purpose |
|-------|-----|------|-----------|---------|
| 0x01 | 0x07 | NAV-PVT | Output | Position, velocity, time — primary navigation solution (92-byte payload) |
| 0x01 | 0x35 | NAV-SAT | Output | Per-satellite info: constellation, elevation, azimuth, C/N0, used flag |
| 0x06 | 0x8A | CFG-VALSET | Input | Configuration: write key-value pairs to RAM/BBR/Flash layers |
| 0x06 | 0x01 | CFG-MSG | Input | Legacy message rate configuration per port |
| 0x05 | 0x01 | ACK-ACK | Output | Command acknowledged |
| 0x05 | 0x00 | ACK-NAK | Output | Command rejected |
| 0x02 | 0x15 | RXM-RAWX | Output | Raw measurements for RTKLIB (enabled in rtklib engine mode) |
| 0x02 | 0x13 | RXM-SFRBX | Output | Subframe buffer data for RTKLIB |

### NAV-PVT Payload Layout (92 bytes)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | U4 | iTOW (ms) |
| 4 | 2 | U2 | year |
| 6 | 1 | U1 | month |
| 7 | 1 | U1 | day |
| 8 | 1 | U1 | hour |
| 9 | 1 | U1 | minute |
| 10 | 1 | U1 | second |
| 11 | 1 | X1 | valid flags |
| 12 | 4 | U4 | tAcc (ns) |
| 16 | 4 | I4 | nano (ns) |
| 20 | 1 | U1 | fixType (0=none, 2=2D, 3=3D) |
| 21 | 1 | X1 | flags |
| 22 | 1 | X1 | flags2 |
| 23 | 1 | U1 | numSV |
| 24 | 4 | I4 | lon (1e-7 deg) |
| 28 | 4 | I4 | lat (1e-7 deg) |
| 32 | 4 | I4 | height (mm above ellipsoid) |
| 36 | 4 | I4 | hMSL (mm above mean sea level) |
| 40 | 4 | U4 | hAcc (mm horizontal accuracy) |
| 44 | 4 | U4 | vAcc (mm vertical accuracy) |
| 60 | 4 | I4 | gSpeed (mm/s ground speed) |
| 64 | 4 | I4 | headMot (1e-5 deg heading) |
| 76 | 2 | U2 | pDOP (0.01 scale) |

### CFG-VALSET Key IDs Used

| Key ID | Name | Size | Purpose |
|--------|------|------|---------|
| 0x20030001 | CFG-TMODE-MODE | U1 | 0=disabled, 2=fixed position |
| 0x20030002 | CFG-TMODE-POS_TYPE | U1 | 1=LLH |
| 0x40030009 | CFG-TMODE-LAT | I4 | Latitude (1e-7 deg, integer part) |
| 0x2003000A | CFG-TMODE-LAT_HP | I1 | Latitude high-precision (1e-9 deg) |
| 0x4003000B | CFG-TMODE-LON | I4 | Longitude |
| 0x2003000C | CFG-TMODE-LON_HP | I1 | Longitude high-precision |
| 0x4003000D | CFG-TMODE-HEIGHT | I4 | Height (cm, integer part) |
| 0x2003000E | CFG-TMODE-HEIGHT_HP | I1 | Height high-precision (0.1mm) |
| 0x209102C0 | CFG-MSGOUT-RTCM_1005_USB | U1 | RTCM 1005 rate on USB |
| 0x209102CF | CFG-MSGOUT-RTCM_1077_USB | U1 | GPS MSM7 rate on USB |
| 0x209102D4 | CFG-MSGOUT-RTCM_1087_USB | U1 | GLONASS MSM7 rate on USB |
| 0x2091031B | CFG-MSGOUT-RTCM_1097_USB | U1 | Galileo MSM7 rate on USB |
| 0x209102D9 | CFG-MSGOUT-RTCM_1127_USB | U1 | BeiDou MSM7 rate on USB |
| 0x20910306 | CFG-MSGOUT-RTCM_1230_USB | U1 | GLONASS biases rate on USB |
| 0x10780004 | CFG-USBOUTPROT-RTCM3X | U1 | Enable/disable RTCM3 on USB |
| 0x10A3002E | CFG-HW-ANT_CFG_VOLTCTRL | U1 | Antenna voltage control |
| 0x20110021 | CFG-NAVSPG-DYNMODEL | U1 | Dynamic platform model |
| 0x30210001 | CFG-RATE-MEAS | U2 | Measurement period (ms) |

## RTCM 3.x Message Types

RTCM (Radio Technical Commission for Maritime Services) defines the correction data format:

### Legacy Messages (RTCM 3.1)
| Type | Content |
|------|---------|
| 1004 | Extended L1&L2 GPS observables — the primary legacy RTK message |
| 1005 | Reference station ARP (ECEF coordinates, no antenna height) |
| 1006 | Reference station ARP + antenna height |
| 1008 | Antenna descriptor + serial number |
| 1012 | Extended L1&L2 GLONASS observables |
| 1019 | GPS broadcast ephemeris |
| 1020 | GLONASS broadcast ephemeris |
| 1033 | Receiver and antenna descriptors |

### MSM7 Messages (RTCM 3.2+) — Modern Multi-Signal
| Type | Constellation |
|------|---------------|
| 1077 | GPS MSM7 (full observables, all signals) |
| 1087 | GLONASS MSM7 |
| 1097 | Galileo MSM7 |
| 1107 | SBAS MSM7 |
| 1127 | BeiDou MSM7 |

### Other
| Type | Content |
|------|---------|
| 1042 | BeiDou broadcast ephemeris |
| 1045 | Galileo F/NAV ephemeris |
| 1046 | Galileo I/NAV ephemeris |
| 1230 | GLONASS code-phase bias (critical for inter-frequency bias correction) |

MSM7 messages contain the richest data: full pseudorange, carrier phase, Doppler, and C/N0 for all tracked signals. They are the modern replacement for legacy 1001-1004/1009-1012.

## NTRIP Protocol

NTRIP (Networked Transport of RTCM via Internet Protocol) has three roles:

### Client (rover receives corrections)
- HTTP GET to `/{mountpoint}` with Basic auth
- NTRIP 1.0: caster replies `ICY 200 OK` then streams binary RTCM3
- NTRIP 2.0: standard HTTP/1.1 `200 OK` with optional chunked encoding
- VRS casters require periodic GGA feedback (Survey365 sends every 10s)

### Server/Push (base publishes corrections)
- NTRIP 1.0: `SOURCE <password> /<mountpoint>` + `Source-Agent: ...`
- Caster replies `ICY 200 OK` then accepts binary RTCM3 stream
- Used by Survey365's `NTRIPPush` and RTKLIB `ntrips://` output

### Caster (routes streams between servers and clients)
- Serves sourcetable at `/` listing available mountpoints
- Survey365 runs a local caster proxy on port 2101 that reverse-proxies RTKLIB's internal caster on port 2110, preserving visibility of rover GGA traffic

### Common NTRIP Casters
- **RTK2Go** (rtk2go.com:2101) — free community caster
- **Emlid Caster** (caster.emlid.com:2101) — free for Emlid users
- **ALDOT CORS** (aldotcors.dot.state.al.us:2101) — Alabama DOT reference stations

## Operating Modes

### Known Point Base (`POST /api/mode/known-base`)
1. Look up site coordinates from the `sites` table
2. Configure F9P with CFG-TMODE-MODE=2 (fixed position) via CFG-VALSET
3. Start enabled outputs (RINEX, local caster, outbound push)
4. Create session record

### Relative Base (`POST /api/mode/relative-base`)
1. Average current GNSS position for N seconds (default 120s)
2. Save averaged point as a site with `source='averaged'`
3. Switch to Known Point Base mode at that position

### CORS Establish (`POST /api/mode/cors-establish`)
1. Connect to a CORS NTRIP caster as client (receive corrections)
2. Inject RTCM3 corrections into F9P for RTK positioning
3. Wait for RTK fixed solution (accuracy < 50mm)
4. Average RTK-fixed positions for N seconds
5. Save as site with `source='cors_rtk'`
6. Disconnect CORS, switch to Known Point Base mode

### Stop (`POST /api/mode/stop`)
1. Stop all RTCM outputs
2. Disable TMODE3 (return to rover mode)
3. End session, set mode to idle

## RTK Quality Thresholds

Survey365 classifies position quality by horizontal accuracy:

| Quality | hAcc Range | Description |
|---------|-----------|-------------|
| fixed | < 50mm | RTK fixed — centimeter-level, survey-grade |
| float | 50mm – 500mm | RTK float — decimeter-level |
| dgps | 500mm – 2m | Differential correction applied |
| autonomous | > 2m | Standalone GNSS, no corrections |
| none | No fix | No position solution |

## GGA Generation

Survey365 generates NMEA GGA sentences from current position state for VRS feedback:
- Quality indicator: 1=GPS, 4=RTK Fixed (hAcc < 50mm), 5=RTK Float (hAcc < 500mm)
- Sent to CORS casters every 10 seconds via `NTRIPClient._gga_feedback_loop()`
- Format: `$GPGGA,HHMMSS.SS,DDMM.MMMM,N/S,DDDMM.MMMM,E/W,Q,NN,H.H,HHH.HHH,M,...*CS`

## Common Failure Modes

| Symptom | Likely Cause | Check |
|---------|-------------|-------|
| `connected: false` | Serial port gone — USB disconnected or udev rule missing | `ls -la /dev/ttyGNSS`, `dmesg \| grep tty` |
| `fix_type: "No Fix"` | Antenna obstructed, antenna voltage not enabled, bad cable | Check sky view, `journalctl -u survey365 \| grep antenna` |
| Survey-in not converging | Poor multipath environment or short observation | Use CORS establish instead for precise position |
| NTRIP auth failure | Wrong credentials or mountpoint | Check `ntrip_profiles` table, verify with `curl` |
| No RTCM output | Wrong engine mode, USB protocol disabled | Check `rtcm_engine` config, verify `CFG-USBOUTPROT-RTCM3X` |
| RTKLIB services not starting | `active-base.json` missing or malformed | Check `data/rtklib/active-base.json`, journal for launcher errors |
| Synthetic 1006 not injected | Manager's reference frame cleared prematurely | Verify base mode is active and `_synthetic_reference_frame` is set |
