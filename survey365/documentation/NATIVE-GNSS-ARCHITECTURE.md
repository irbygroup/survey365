# Survey365 Native GNSS Architecture

Survey365 owns the GNSS receiver directly. The app reads the receiver over `/dev/ttyGNSS`, configures base/rover behavior natively, and manages RTCM distribution without an external relay or companion web UI.

## Components

- `app/gnss/manager.py`:
  Opens the serial port, loads GNSS runtime config from the database, applies startup receiver config, and routes frames to the parser or RTCM outputs.
- `app/gnss/serial_reader.py`:
  Detects UBX, NMEA, and RTCM3 frames from the raw serial stream.
- `app/gnss/ublox.py`:
  Parses UBX navigation messages and sends receiver configuration commands including base mode, RTCM output selection, and antenna voltage.
- `app/gnss/base_station.py`:
  Starts and stops base mode using config-driven outputs.
- `app/gnss/rtcm_fanout.py`:
  Broadcasts RTCM3 frames to enabled outputs.
- `app/gnss/rinex_logger.py`:
  Writes raw RTCM data to rotating log files.
- `app/gnss/ntrip_caster.py`:
  Serves a local NTRIP mountpoint for LAN rovers.
- `app/gnss/ntrip_push.py`:
  Pushes corrections to the default outbound caster profile when configured.
- `app/gnss/ntrip_client.py`:
  Receives inbound corrections for establish/rover workflows.
- `app/gnss/quectel.py`:
  Stub backend reserved for future LG290P support.

## Configuration

Receiver and RTCM settings live in the `config` table in `survey365/data/survey365.db`.

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

Remote caster and inbound correction endpoints live in the `ntrip_profiles` table.

## Runtime Flow

1. `GNSSManager.start()` loads DB-backed GNSS settings and opens the serial port.
2. Startup config enables antenna voltage and begins parsing UBX navigation frames.
3. When base mode starts, Survey365 programs the receiver with fixed coordinates and enables the configured RTCM messages.
4. RTCM output is fanned out to the enabled outputs:
   `RINEXLogger`, `NTRIPCaster`, and `NTRIPPush` when a default outbound profile exists.
5. Status and satellite data are published through `/api/status`, `/api/satellites`, and `/ws/live`.

## Operational Notes

- Survey365 should run under `Group=dialout` to access `/dev/ttyGNSS`.
- A udev rule should provide the stable `/dev/ttyGNSS` symlink.
- The admin UI is the only active configuration surface for GNSS/NTRIP behavior.
