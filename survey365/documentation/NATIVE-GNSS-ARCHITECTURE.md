# Survey365 Native GNSS Architecture

Survey365 owns the GNSS receiver directly. The app reads the receiver over `/dev/ttyGNSS`, configures base/rover behavior natively, and manages RTCM distribution without an external relay or companion web UI.

## Components

- `app/gnss/manager.py`:
  Opens the serial port, loads GNSS runtime config from the database, applies startup receiver config, and routes frames to the parser or RTCM outputs.
- `app/gnss/state.py`:
  GNSSState dataclass holding live position, satellite counts, fix type, and RTK quality. Provides async-safe `snapshot()` and `satellite_snapshot()` methods consumed by the status API and WebSocket.
- `app/gnss/serial_reader.py`:
  Detects UBX, NMEA, and RTCM3 frames from the raw serial stream using a background thread with an asyncio queue bridge.
- `app/gnss/ublox.py`:
  Parses UBX navigation messages and sends receiver configuration commands including base mode, RTCM output selection, and antenna voltage.
- `app/gnss/base_station.py`:
  Starts and stops base mode using config-driven outputs. Resolves enabled outputs from config and default NTRIP profiles.
- `app/gnss/rtcm_fanout.py`:
  Broadcasts RTCM3 frames to enabled outputs.
- `app/gnss/rinex_logger.py`:
  Writes raw RTCM data to timestamped rotating log files with automatic gzip compression on rotation.
- `app/gnss/ntrip_caster.py`:
  Serves a local NTRIP 1.0 mountpoint for LAN rovers.
- `app/gnss/ntrip_push.py`:
  Pushes corrections to the default outbound caster profile when configured. Auto-reconnects on failure.
- `app/gnss/ntrip_client.py`:
  Receives inbound CORS corrections for establish workflows. Sends GGA feedback for VRS casters.
- `app/gnss/quectel.py`:
  Stub backend reserved for future LG290P support.

## Configuration

Receiver and RTCM settings live in the `config` table in the Survey365 database (path set by `SURVEY365_DB` environment variable).

Key settings:

- `gnss_port`
- `gnss_baud`
- `gnss_backend`
- `rtcm_messages`
- `rinex_enabled`
- `rinex_rotate_hours`
- `rinex_data_dir`
- `local_caster_enabled`
- `local_caster_port`
- `local_caster_mountpoint`
- `antenna_height_m`

Remote caster and inbound correction endpoints live in the `ntrip_profiles` table.

## Runtime Flow

1. `GNSSManager.start()` loads DB-backed GNSS settings and opens the serial port.
2. Startup config enables antenna voltage and begins parsing UBX navigation frames. Position, satellite, and fix data are written to `GNSSState`.
3. When base mode starts (known-point or relative), Survey365 programs the receiver with fixed coordinates and enables the configured RTCM messages.
4. RTCM output is fanned out to the enabled outputs:
   `RINEXLogger`, `NTRIPCaster`, and `NTRIPPush` when a default outbound profile exists.
5. In CORS establish mode, `NTRIPClient` receives corrections from a remote caster, waits for RTK fix, averages the position, and saves the result as a site.
6. Status and satellite data are published through `/api/status`, `/api/satellites`, and `/ws/live`.

## Operational Notes

- Survey365 should run under `Group=dialout` to access `/dev/ttyGNSS`.
- A udev rule should provide the stable `/dev/ttyGNSS` symlink.
- The admin UI is the only active configuration surface for GNSS/NTRIP behavior.
