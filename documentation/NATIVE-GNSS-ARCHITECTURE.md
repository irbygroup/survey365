# Survey365 Native GNSS Architecture

Survey365 owns the GNSS receiver directly, but RTKLIB now owns base-station correction encoding and publishing (when `rtcm_engine=rtklib`, the default). The app reads the receiver over `/dev/ttyGNSS`, polls receiver identity at startup via MON-VER, configures base/rover behavior natively, relays raw UBX bytes on localhost, and uses RTKLIB `str2str` services for local casting, remote push, and raw logging.

A native fallback mode (`rtcm_engine=native`) is preserved for one-release compatibility. In native mode the F9P generates RTCM3 directly, the local caster broadcasts via rtcm_fanout (no upstream proxy), and RTKLIB is not involved.

## Components

- `app/gnss/manager.py`:
  Opens the serial port, loads GNSS runtime config from the database, applies startup receiver config, relays raw bytes to localhost TCP, and routes parsed UBX navigation frames into `GNSSState`.
- `app/gnss/state.py`:
  GNSSState dataclass holding live position, satellite counts, fix type, and RTK quality. Provides async-safe `snapshot()` and `satellite_snapshot()` methods consumed by the status API and WebSocket.
- `app/gnss/serial_reader.py`:
  Detects UBX, NMEA, and RTCM3 frames from the raw serial stream using a background thread with an asyncio queue bridge and tees the untouched raw byte stream into the raw relay.
- `app/gnss/raw_relay.py`:
  Publishes the receiver's raw UBX byte stream on `127.0.0.1:5015` for RTKLIB consumers.
- `app/gnss/ublox.py`:
  Parses UBX navigation messages and sends receiver configuration commands including base mode, raw UBX output selection for RTKLIB, and antenna voltage.
- `app/gnss/base_station.py`:
  Starts and stops base mode, writes the active RTKLIB runtime config, starts/stops RTKLIB systemd units, and manages the local caster proxy.
- `app/gnss/ntrip_caster.py`:
  Dual-mode NTRIP caster: in RTKLIB mode proxies the internal local caster; in native mode acts as a direct-broadcast RTCMOutput attached to rtcm_fanout. Both modes share session bookkeeping, GGA capture, and the admin API shape.
- `app/rtklib/runtime.py`:
  Stores the active base/session runtime config under the writable data volume.
- `app/rtklib/launcher.py`:
  Builds RTKBase-style `str2str` command lines for the local caster, outbound push, and raw file logger.
- `app/gnss/ntrip_client.py`:
  Receives inbound CORS corrections for establish workflows. Sends GGA feedback for VRS casters.
- `app/gnss/quectel.py`:
  Stub backend reserved for future LG290P support. Satisfies the full GNSSBackend contract with explicit NotImplementedError stubs.
- `app/version.py`:
  Application version (`__version__`) used in RTKLIB receiver descriptors and sourcetable metadata.

## Configuration

Receiver and RTCM settings live in the `config` table in the Survey365 database (path set by `SURVEY365_DB` environment variable).

Key settings:

- `gnss_port`
- `gnss_baud`
- `gnss_backend`
- `rtcm_engine`
- `rtklib_local_messages`
- `rtklib_outbound_messages`
- `antenna_descriptor`
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
2. Startup config enables antenna voltage, polls MON-VER for receiver identity, and begins parsing UBX navigation frames. Position, satellite, and fix data are written to `GNSSState`. The MON-VER response populates the receiver model and firmware used in RTKLIB metadata.
3. When base mode starts (known-point or relative), Survey365 programs the receiver with fixed ellipsoid coordinates, disables native RTCM USB output, and enables the raw UBX messages RTKLIB needs.
4. Survey365 writes an active runtime file and starts the needed RTKLIB systemd units.
5. RTKLIB `str2str` instances read `tcpcli://127.0.0.1:5015#ubx` and emit:
   local NTRIP caster output, remote NTRIP source push, and raw file logging.
6. Survey365's local-caster proxy fronts the RTKLIB local caster so rovers still connect to the Pi on the configured port and the app can inspect inbound GGA/NMEA.
5. In CORS establish mode, `NTRIPClient` receives corrections from a remote caster, waits for RTK fix, averages the position, and saves the result as a site.
6. Status and satellite data are published through `/api/status`, `/api/satellites`, and `/ws/live`.

## Operational Notes

- Survey365 should run under `Group=dialout` to access `/dev/ttyGNSS`.
- A udev rule should provide the stable `/dev/ttyGNSS` symlink.
- The admin UI is the only active configuration surface for GNSS/NTRIP behavior.
- RTKLIB is pinned to `rtklibexplorer/RTKLIB v2.5.0`, built during `scripts/setup-pi.sh`, and installed as `/usr/local/bin/str2str`.
- The RTKBase-style default RTCM message set is `1004,1005(10),1006,1008(10),1012,1019,1020,1033(10),1042,1045,1046,1077,1087,1097,1107,1127,1230`.
- Broadcast coordinates use site ellipsoid height only. NAVD88 and orthometric heights remain display products.
