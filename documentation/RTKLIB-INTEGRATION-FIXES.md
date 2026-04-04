# RTKLIB Integration Fixes Plan

Post-merge fix plan for `feat/rtklib-output-integration`. This branch will be
merged to `main` first, then a new `fix/rtklib-integration-hardening` branch
implements all remaining bugs, gaps, and plan deviations identified during code
review.

---

## Phase 1 — Merge and Branch

### Step 1: Merge the current PR

```
git checkout main
git merge feat/rtklib-output-integration
git push origin main
```

### Step 2: Clean up the feature branch

```
git branch -d feat/rtklib-output-integration
git push origin --delete feat/rtklib-output-integration
```

### Step 3: Create the fix branch

```
git checkout -b fix/rtklib-integration-hardening
git push -u origin fix/rtklib-integration-hardening
```

---

## Phase 2 — Bug Fixes (P0 — Runtime Crash Risks)

### Fix 1: Broken native-fallback NTRIPCaster

**Problem:**
`NTRIPCaster` was fully refactored from an RTCM broadcaster (implementing the
`RTCMOutput` protocol with a `write()` method) into a reverse proxy. The
`write()` method was removed entirely. But `_start_base_native()` in
`app/gnss/base_station.py` still creates an `NTRIPCaster` instance and points
it at `upstream_port=2110`, where nothing is listening in native mode. It also
assigns it to `manager.local_caster_proxy` instead of adding it to
`manager.rtcm_fanout`, so even if upstream existed it wouldn't receive RTCM
data. Anyone who sets `rtcm_engine=native` (the one-release rollback path) gets
a completely non-functional local caster.

**Fix — three parts:**

1. **Restore a standalone broadcast-capable NTRIPCaster for native mode.**
   Create a thin `NTRIPDirectCaster` class (or add a `mode` parameter to
   `NTRIPCaster`) that keeps the old `write()` method and direct client
   streaming behavior. This class implements the `RTCMOutput` protocol so
   `rtcm_fanout` can call `write()` on it. Keep all existing client-capture
   logic (session tracking, GGA parsing, NMEA capture) so the native path
   retains full admin visibility.

   Recommended approach: add an internal flag to `NTRIPCaster.__init__()`:
   - `upstream_port=None` means direct-broadcast mode (native): the caster
     serves its own sourcetable, implements `write()` for RTCM fanout, and
     streams data directly to clients.
   - `upstream_port=<int>` means proxy mode (RTKLIB): current proxy behavior.

   This avoids duplicating the session-capture and NMEA/GGA parsing code across
   two classes.

2. **Fix `_start_base_native()` to use direct-broadcast mode.**
   Create the `NTRIPCaster` with `upstream_port=None` (or omitted), pass
   `latitude` and `longitude` for the sourcetable, and add it to
   `manager.rtcm_fanout` as before. Also assign it to
   `manager.local_caster_proxy` so `snapshot_clients()` works from both code
   paths.

3. **Fix `stop_base()` native path to also clear `manager.local_caster_proxy`.**
   Currently the native `stop_base` path only calls
   `manager.rtcm_fanout.clear_outputs()` but doesn't clear
   `manager.local_caster_proxy`. After this fix, both paths must clear the
   proxy reference.

**Files:**
- `app/gnss/ntrip_caster.py` — add direct-broadcast mode back
- `app/gnss/base_station.py` — fix `_start_base_native()` and native
  `stop_base()` path

**Verification:**
- Set `rtcm_engine=native` in the database.
- Start a known-base session with `local_caster_enabled=true`.
- Connect a rover to the external local caster port.
- Verify the rover receives RTCM data.
- Verify `/api/ntrip/local-caster/clients` shows the connected rover.
- Stop the session and verify clean teardown.

---

### Fix 2: QuectelBackend missing methods and attributes

**Problem:**
`GNSSManager.configure_base()` calls `self.backend.enable_raw_output()` and
`configure_rover()` calls `self.backend.disable_raw_output()` when
`rtcm_engine=rtklib`. `receiver_descriptor()` reads
`self.backend.receiver_model` and `self.backend.receiver_firmware`. The Quectel
stub has none of these, so any `gnss_backend=quectel` with RTKLIB mode crashes
with `AttributeError`.

**Fix:**
Add to `app/gnss/quectel.py`:
```python
receiver_model: str = "LG290P"
receiver_firmware: str = "unknown"

async def enable_raw_output(self, serial_reader):
    raise NotImplementedError("LG290P support coming soon")

async def disable_raw_output(self, serial_reader):
    raise NotImplementedError("LG290P support coming soon")
```

**Files:**
- `app/gnss/quectel.py`

**Verification:**
- Set `gnss_backend=quectel` and `rtcm_engine=rtklib` in the database.
- Start Survey365 and confirm it raises `NotImplementedError` with a clear
  message instead of `AttributeError`.

---

### Fix 3: `stop_base()` reads `rtcm_engine` from DB — mismatch risk

**Problem:**
`stop_base()` reads `rtcm_engine` from the database on every call to decide
which teardown path to use. If someone changes `rtcm_engine` in the config
while a base session is active, `stop_base()` will try to tear down the wrong
output stack (e.g., try to clear `rtcm_fanout` when RTKLIB services are
running, or try to stop RTKLIB services when native outputs are active).

**Fix:**
Track the active engine in `GNSSManager` as `_active_output_engine: str | None`.
- `start_base()` sets `manager._active_output_engine` to the engine that was
  actually started (`"rtklib"` or `"native"`).
- `stop_base()` reads `manager._active_output_engine` instead of the DB.
- `stop_base()` clears `manager._active_output_engine` to `None` after
  teardown.
- If `_active_output_engine` is `None`, `stop_base()` does a defensive
  best-effort teardown of both paths (stop RTKLIB services if any are tracked
  as running, clear rtcm_fanout if it has outputs).

**Files:**
- `app/gnss/manager.py` — add `_active_output_engine` attribute
- `app/gnss/base_station.py` — set/read/clear the attribute

**Verification:**
- Start base in RTKLIB mode.
- Change `rtcm_engine` to `native` in the database (via `/api/config`).
- Stop the base session.
- Verify the RTKLIB services are stopped correctly (not the wrong teardown
  path).

---

## Phase 3 — Plan Compliance Gaps (P1)

### Fix 4: Implement MON-VER polling and real receiver descriptor

**Problem:**
The plan requires: "Poll receiver identity once at startup with MON-VER and
cache model/firmware for descriptor generation." Currently
`receiver_firmware: str = "unknown"` is never updated, and no MON-VER poll
exists. The RTKLIB `-i` descriptor is always
`"RTKBase ZED-F9P,Survey365 unknown"`.

The plan also specifies the descriptor format as:
`RTKBase {receiver_model},{survey365_version} {receiver_firmware}`

The current code produces:
`RTKBase {receiver_model},Survey365 {receiver_firmware}`

This is missing the Survey365 version and uses the wrong field order.

**Fix — four parts:**

1. **Add MON-VER constants and poll/parse logic to `app/gnss/ublox.py`.**
   - Add constants: `UBX_MON_CLASS = 0x0A`, `UBX_MON_VER_ID = 0x04`.
   - Add a `poll_mon_ver(serial_reader)` method that sends a zero-payload
     UBX-MON-VER poll message and returns.
   - Add MON-VER response parsing in `parse_frame()`: when class=0x0A
     id=0x04 is received, extract the 30-byte `swVersion` field and the
     30-byte `hwVersion` field from the payload, update
     `self.receiver_firmware` and `self.receiver_model`.
   - Allow `_should_process_ubx_message` to also pass MON-VER (0x0A, 0x04)
     through the filter so the app queue sees the response. Alternatively,
     handle MON-VER parsing inline in the serial reader's UBX processing
     since it's a one-time startup event — but the simpler path is to just
     let it through to `parse_frame()`.

2. **Poll MON-VER once during startup configuration in `GNSSManager`.**
   In `_connect_and_read()`, after `enable_antenna_voltage()`, call
   `self.backend.poll_mon_ver(self.serial_reader)`. The response will arrive
   as a UBX frame in the normal read loop and get parsed by `parse_frame()`.

3. **Update `_should_process_ubx_message` to also pass 0x0A/0x04.**
   Add `(msg_class == 0x0A and msg_id == 0x04)` to the filter so the
   MON-VER response frame reaches `parse_frame()`. Similarly update
   `_should_queue_frame` for UBX class 0x0A.

4. **Fix the receiver descriptor format in `GNSSManager.receiver_descriptor()`.**
   The plan format is: `RTKBase {model},{survey365_version} {firmware}`.
   Since there is no `app/version.py` or `__version__`, use the git short
   hash or a hardcoded version string. Recommended: read the version from a
   `app/version.py` file (create it with a `__version__ = "1.0.0"` constant)
   so it can be bumped independently.

   Update `receiver_descriptor()` to:
   ```python
   def receiver_descriptor(self) -> str:
       from ..version import __version__
       return (
           f"RTKBase {self.backend.receiver_model},"
           f"Survey365_{__version__} {self.backend.receiver_firmware}"
       )
   ```

5. **Fix the hardcoded sourcetable `receiver_label` in `base_station.py`.**
   Line 118 hardcodes `"RTKBase_ZED-F9P,Survey365"`. Replace with a dynamic
   value derived from `manager.backend.receiver_model` and the app version:
   ```python
   "receiver_label": f"RTKBase_{manager.backend.receiver_model},Survey365_{version}",
   ```

**Files:**
- `app/gnss/ublox.py` — MON-VER constants, poll method, parse logic
- `app/gnss/manager.py` — poll on connect, update filters, fix descriptor
- `app/gnss/base_station.py` — dynamic receiver_label in runtime config
- `app/version.py` — new file with `__version__`

**Verification:**
- Start Survey365 connected to an F9P.
- Check logs for MON-VER parse output with actual firmware version.
- Start a base session.
- Read `active-base.json` and verify `receiver_descriptor` contains real
  firmware and app version.
- Connect a rover and inspect the NTRIP sourcetable for correct receiver
  label.

---

### Fix 5: Missing `reset-failed` sudoers entries

**Problem:**
The plan requires sudoers entries for `start`, `stop`, `restart`, `is-active`,
and `reset-failed` for each RTKLIB unit. `reset-failed` is missing.

**Fix:**
Add three lines to the sudoers block in `scripts/setup-pi.sh`:
```
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl reset-failed survey365-rtklib-local-caster.service
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl reset-failed survey365-rtklib-outbound.service
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl reset-failed survey365-rtklib-log.service
```

Also add a `reset_failed_service()` helper to `app/systemd.py` so base_station
can call it if needed before a restart:
```python
async def reset_failed_service(name: str) -> None:
    await sudo_systemctl("reset-failed", name)
```

**Files:**
- `scripts/setup-pi.sh`
- `app/systemd.py`

**Verification:**
- Run `setup-pi.sh` on the Pi.
- Verify `/etc/sudoers.d/survey365` contains the `reset-failed` lines.
- Run `sudo -n systemctl reset-failed survey365-rtklib-local-caster.service`
  as the survey365 user and confirm it succeeds.

---

### Fix 6: Missing `pkg-config` and `unzip` in setup-pi DEPS

**Problem:**
The plan calls for `build-essential`, `pkg-config`, and `unzip` as RTKBase-
proven build prerequisites. Only `build-essential` and `curl` were added.

**Fix:**
Add `pkg-config` and `unzip` to the DEPS array in `scripts/setup-pi.sh`.

**Files:**
- `scripts/setup-pi.sh`

**Verification:**
- Run `setup-pi.sh` on a fresh Pi.
- Confirm `pkg-config` and `unzip` are installed.

---

### Fix 7: UI logging terminology not updated

**Problem:**
The plan says: "change UI/docs wording from 'RINEX / raw RTCM logging' to
'Raw GNSS logging'." The admin UI still uses stale terminology:
- "Enable raw RTCM logging" (line 519)
- "RINEX Rotate Hours" (line 524)
- "RINEX Data Dir" (line 528)

This is misleading because RTKLIB now logs raw UBX, not RTCM.

**Fix:**
Update `ui/admin.html`:
- "Enable raw RTCM logging" → "Enable raw GNSS logging"
- "RINEX Rotate Hours" → "Log Rotation (hours)"
- "RINEX Data Dir" → "Raw Log Directory"

Keep the underlying config keys (`rinex_enabled`, `rinex_rotate_hours`,
`rinex_data_dir`) unchanged for backward compatibility as the plan specifies.

**Files:**
- `ui/admin.html`

**Verification:**
- Load the admin page and confirm the labels are updated.
- Save config and confirm the correct keys are still sent to the API.

---

### Fix 8: `active-base.json` missing planned fields

**Problem:**
The plan says the runtime file should contain: active mode, local external
port, internal RTKLIB port, and other fields. Currently missing:
- `active_mode` (e.g., `"known_base"`, `"relative_base"`)
- `external_local_caster_port` (the LAN-facing proxy port)

**Fix:**
Add the missing fields to the runtime payload in
`app/gnss/base_station.py` `_start_base_rtklib()`:
```python
runtime = {
    "active_mode": mode,  # pass mode from caller
    "raw_relay_port": RAW_RELAY_PORT,
    "trace_level": 0,
    "position": { ... },
    ...
    "outputs": {
        "local_caster": {
            ...
            "external_port": local_caster_port,
            "internal_port": LOCAL_CASTER_INTERNAL_PORT,
            ...
        },
        ...
    },
}
```

This requires threading the active mode string from the mode route through
`start_base()`. Add an optional `mode: str | None = None` parameter to
`start_base()` and `_start_base_rtklib()`.

**Files:**
- `app/gnss/base_station.py` — add fields to runtime payload, add mode param
- `app/routes/mode.py` — pass mode string to `start_base()`

**Verification:**
- Start a known-base session.
- Read `{data_dir}/rtklib/active-base.json`.
- Confirm `active_mode`, `external_port` fields are present and correct.

---

## Phase 4 — Correctness and Robustness (P2)

### Fix 9: UBX frames skipped without checksum validation

**Problem:**
In `serial_reader.py`, when `_should_process_ubx()` returns `False`, the frame
is deleted from the buffer using only the header-derived length — without
validating the checksum. If the header is corrupt (bad length field), this
could consume/discard arbitrary data from the buffer.

**Fix:**
Move the filter check to after `_try_ubx()` succeeds (checksum validated),
not before. The performance optimization of skipping checksum validation on
filtered messages is not worth the data-integrity risk.

Revised flow:
```python
frame_len = self._peek_ubx_frame_length(buffer)
if frame_len > 0:
    frame_len = self._try_ubx(buffer)
if frame_len > 0:
    if self._should_process_ubx(buffer):
        frame = bytes(buffer[:frame_len])
        if self._should_emit("ubx", frame):
            self._emit("ubx", frame)
    del buffer[:frame_len]
    continue
```

This still skips the queue for unneeded messages (RXM-RAWX, RXM-SFRBX) but
only after confirming the frame is valid. The checksum computation is cheap
(Fletcher-8 over a few hundred bytes).

**Files:**
- `app/gnss/serial_reader.py`

**Verification:**
- Start Survey365 with RTKLIB mode.
- Monitor logs for any frame parse errors.
- Verify NAV-PVT and NAV-SAT frames are still parsed correctly.
- Verify RXM-RAWX / RXM-SFRBX frames are skipped from the queue but the raw
  relay still gets the bytes (raw relay receives pre-parse chunks).

---

### Fix 10: `NoNewPrivileges=true` removed unnecessarily from survey365.service

**Problem:**
The comment says "Survey365 needs sudo -n systemctl access to manage RTKLIB
child units" but `sudo` is a setuid binary — it elevates privileges before
`NoNewPrivileges` applies to the child process. Removing `NoNewPrivileges=true`
weakens security hardening for no reason.

**Fix:**
Restore `NoNewPrivileges=true` in `systemd/survey365.service`. Test that
`sudo -n systemctl start/stop` still works with it enabled.

If testing reveals `NoNewPrivileges=true` does block `sudo -n` in this
specific systemd context, keep it removed but update the comment to explain
why with the specific error observed.

**Files:**
- `systemd/survey365.service`

**Verification:**
- Deploy the unit to the Pi.
- Start a base session that starts RTKLIB services via sudo.
- Confirm `sudo -n systemctl start survey365-rtklib-local-caster.service`
  succeeds.
- If it fails, revert and document the reason in the comment.

---

### Fix 11: Outbound profile validation

**Problem:**
When `ntrip_push` is in the outputs list, the outbound profile is loaded from
the database. If required fields (`host`, `mountpoint`, `password`) are empty
or NULL, the generated str2str command line will be malformed. str2str may fail
silently or produce confusing errors.

**Fix:**
Add validation in `_start_base_rtklib()` after loading the outbound profile.
If `host` or `mountpoint` is empty/None, log a warning and skip outbound
(treat it as not enabled) rather than writing a broken runtime config.

```python
if outbound_profile is not None:
    if not outbound_profile.get("host") or not outbound_profile.get("mountpoint"):
        logger.warning("Outbound NTRIP profile is incomplete (missing host or mountpoint), skipping push")
        outbound_profile = None
```

An empty password is allowed (some casters accept anonymous source pushes), so
only validate `host` and `mountpoint`.

**Files:**
- `app/gnss/base_station.py`

**Verification:**
- Create an outbound profile with an empty host.
- Start a base session.
- Confirm the outbound service is NOT started and a warning is logged.
- Confirm the rest of the output stack (local caster, logging) starts
  correctly.

---

## Phase 5 — Unit Tests (P2)

### Fix 12: Add focused unit tests

**Problem:**
The plan has an explicit test matrix. No tests exist in the repo.

**Setup:**
- Create `tests/` directory.
- Add `pytest` and `pytest-asyncio` to a `requirements-dev.txt` (do not add to
  production `requirements.txt`).
- Create `tests/conftest.py` with common fixtures.

**Tests to add:**

#### `tests/test_raw_relay.py`
- **test_broadcast_exact_bytes**: Start relay, connect two TCP clients, publish
  known byte sequences, verify both clients receive identical bytes in order.
- **test_queue_full_drops_gracefully**: Fill the queue to capacity, verify
  `publish_nowait` does not raise, verify the drop counter increments.
- **test_dead_client_cleanup**: Connect a client, close it, publish data,
  verify the relay removes the dead client without crashing.

#### `tests/test_launcher.py`
- **test_local_caster_argv**: Write a known `active-base.json`, call
  `build_command("local_caster")`, verify the exact argv list including
  `-in`, `-msg`, `-out`, `-p`, `-i`, `-a`, `-t`, `-fl` flags.
- **test_outbound_argv**: Same for outbound role, verify `ntrips://` URL
  format with password-only auth.
- **test_log_argv**: Same for log role, verify `file://` URL with rotation
  syntax and no `-msg`/`-p`/`-i`/`-a` flags.
- **test_disabled_role_exits**: Write config with a role disabled, call
  `build_command()`, verify `SystemExit` is raised.

#### `tests/test_ntrip_caster_proxy.py`
- **test_proxy_captures_gga**: Start a mock upstream TCP server that sends
  `ICY 200 OK` + RTCM bytes. Start the proxy. Connect a test client that
  sends a GGA sentence. Verify `snapshot_clients()` contains the parsed GGA
  with latitude/longitude.
- **test_proxy_source_table_passthrough**: Start a mock upstream that returns
  a sourcetable. Verify the proxy returns the exact same bytes to the client.
- **test_proxy_upstream_failure**: Start the proxy with an unreachable
  upstream port. Connect a client. Verify `last_proxy_error` is set and
  `upstream_active` is `False`.

#### `tests/test_migration.py`
- **test_migration_copies_custom_rtcm_messages**: Set up an in-memory SQLite
  DB with a non-default `rtcm_messages` value. Run `init_db()`. Verify
  `rtklib_local_messages` and `rtklib_outbound_messages` both contain the
  custom value.
- **test_migration_skips_default_rtcm_messages**: Set up a DB with the old
  default `rtcm_messages` value (`1005,1077,1087,1097,1127,1230(10)`). Run
  `init_db()`. Verify the RTKLIB message keys have the RTKBase default, not
  the old Survey365 default.
- **test_fresh_install_gets_rtklib_defaults**: Run `init_db()` on an empty
  DB. Verify `rtcm_engine=rtklib` and message keys have RTKBase defaults.

#### `tests/test_serial_reader.py`
- **test_raw_chunk_callback_before_parsing**: Create a `SerialReader` with a
  mock callback. Feed raw bytes. Verify the callback receives the exact raw
  bytes before any frame extraction.
- **test_ubx_filter_skips_unwanted_messages**: Create a reader with a filter
  that only passes NAV-PVT. Feed a valid RXM-RAWX frame followed by a
  NAV-PVT frame. Verify only NAV-PVT reaches the output queue.

**Files:**
- `requirements-dev.txt` — new file
- `tests/__init__.py` — new empty file
- `tests/conftest.py` — new file with fixtures
- `tests/test_raw_relay.py` — new file
- `tests/test_launcher.py` — new file
- `tests/test_ntrip_caster_proxy.py` — new file
- `tests/test_migration.py` — new file
- `tests/test_serial_reader.py` — new file

**Verification:**
- `pip install -r requirements-dev.txt`
- `python -m pytest tests/ -v`
- All tests pass.

---

## Phase 6 — Deploy and Field Verification

### Step 1: Deploy to Pi

```bash
# On the Pi
cd ~/survey365
git fetch origin
git checkout fix/rtklib-integration-hardening
git pull
sudo bash scripts/setup-pi.sh
sudo systemctl restart survey365
```

### Step 2: Verify RTKLIB mode (primary path)

1. Confirm `rtcm_engine=rtklib` in `/api/config`.
2. Start a known-base session with local caster and outbound push enabled.
3. Verify all three RTKLIB units are active:
   ```bash
   systemctl is-active survey365-rtklib-local-caster.service
   systemctl is-active survey365-rtklib-outbound.service
   systemctl is-active survey365-rtklib-log.service
   ```
4. Read `active-base.json` and verify:
   - `active_mode` is present and correct
   - `receiver_descriptor` contains real firmware version (not "unknown")
   - `external_port` is present in local_caster output
5. Connect a rover to the local caster external port.
6. Verify `/api/ntrip/local-caster/clients` shows:
   - The connected client
   - `upstream_active: true`
   - `upstream_port: 2110`
   - Captured GGA if the rover sends it
7. Verify `/api/status` shows:
   - `local_caster: true`
   - `ntrip_push: true`
   - `rinex_logging: true`
   - `raw_relay_clients` >= 1
8. Capture 60–120 seconds of RTCM from the local caster and verify presence
   of messages: 1005, 1006, 1008, 1033, 1077, 1087, 1097, 1127, 1230, and
   at least some broadcast ephemeris messages.
9. Inspect the NTRIP sourcetable (`GET /` on external port) and verify the
   receiver label and descriptor fields are populated with real values.
10. Stop the session and verify clean teardown:
    - All three RTKLIB units are inactive.
    - `active-base.json` is deleted.
    - `/api/status` shows all outputs false.
    - Receiver is back in rover mode.

### Step 3: Verify auto-resume

1. Start a known-base session.
2. Set `auto_resume=true` in config.
3. Reboot the Pi.
4. After boot, verify the base session and RTKLIB units came back
   automatically.
5. Set `auto_resume=false`, reboot, and verify units stay down.

### Step 4: Verify native fallback (if time permits)

1. Set `rtcm_engine=native` in `/api/config`.
2. Start a known-base session with local caster enabled.
3. Connect a rover and verify it receives RTCM data through the direct
   caster (not the proxy).
4. Verify `/api/ntrip/local-caster/clients` shows the rover.
5. Stop the session and verify clean teardown.
6. Set `rtcm_engine=rtklib` back.

### Step 5: Verify admin UI

1. Load the admin page.
2. Confirm logging section says "Enable raw GNSS logging",
   "Log Rotation (hours)", and "Raw Log Directory".
3. Confirm RTKLIB message fields and antenna descriptor are editable.
4. Confirm outbound caster note mentions password-only auth.
5. Confirm correction engine field is displayed as read-only.
6. Save config and verify round-trip.

### Step 6: Run unit tests on Pi

```bash
cd ~/survey365
source venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Confirm all tests pass in the Pi environment.

---

## Phase 7 — Fix Forward

After the deployment verification in Phase 6, if any issues are found:

1. Fix the issue on the `fix/rtklib-integration-hardening` branch.
2. Commit with a descriptive message referencing the issue.
3. Push to origin.
4. Re-deploy to the Pi and re-verify the specific fix.
5. Repeat until all Phase 6 checks pass cleanly.

---

## Phase 8 — Open PR

Once all fixes are implemented, all tests pass, and field verification is
complete:

```bash
git push origin fix/rtklib-integration-hardening
```

Open a PR from `fix/rtklib-integration-hardening` → `main` with:
- Title: `fix: RTKLIB integration hardening`
- Description referencing this plan document and summarizing all fixes
- Link to this file (`documentation/RTKLIB-INTEGRATION-FIXES.md`)

---

## Complete File Change Manifest

| File | Action | Fix # |
|------|--------|-------|
| `app/gnss/ntrip_caster.py` | Restore direct-broadcast mode | 1 |
| `app/gnss/base_station.py` | Fix native path, engine tracking, outbound validation, runtime fields | 1, 3, 8, 11 |
| `app/gnss/manager.py` | Add `_active_output_engine`, update MON-VER filter, fix descriptor | 3, 4 |
| `app/gnss/quectel.py` | Add missing methods and attributes | 2 |
| `app/gnss/ublox.py` | Add MON-VER poll/parse | 4 |
| `app/gnss/serial_reader.py` | Move filter after checksum validation | 9 |
| `app/version.py` | New file — app version constant | 4 |
| `app/systemd.py` | Add `reset_failed_service()` | 5 |
| `app/routes/mode.py` | Pass mode string to `start_base()` | 8 |
| `scripts/setup-pi.sh` | Add reset-failed sudoers, pkg-config, unzip | 5, 6 |
| `systemd/survey365.service` | Restore `NoNewPrivileges=true` (if test passes) | 10 |
| `systemd/survey365-rtklib-*.service` | No changes expected | — |
| `ui/admin.html` | Update logging labels | 7 |
| `requirements-dev.txt` | New file — pytest deps | 12 |
| `tests/__init__.py` | New file | 12 |
| `tests/conftest.py` | New file — fixtures | 12 |
| `tests/test_raw_relay.py` | New file | 12 |
| `tests/test_launcher.py` | New file | 12 |
| `tests/test_ntrip_caster_proxy.py` | New file | 12 |
| `tests/test_migration.py` | New file | 12 |
| `tests/test_serial_reader.py` | New file | 12 |
| `documentation/RTKLIB-INTEGRATION-FIXES.md` | This file | — |

---

## Implementation Order

The fixes should be implemented in this order to avoid merge conflicts and
allow incremental testing:

1. Fix 2 — QuectelBackend (standalone, no dependencies)
2. Fix 1 — Native NTRIPCaster (largest change, touches ntrip_caster + base_station)
3. Fix 3 — Engine tracking in stop_base (touches base_station + manager)
4. Fix 4 — MON-VER + descriptor (touches ublox + manager + base_station + new version.py)
5. Fix 9 — Serial reader checksum order (standalone)
6. Fix 10 — NoNewPrivileges (standalone systemd change, needs Pi test)
7. Fix 11 — Outbound validation (small base_station change)
8. Fix 5 — reset-failed sudoers (standalone setup-pi change)
9. Fix 6 — pkg-config/unzip deps (standalone setup-pi change)
10. Fix 7 — UI labels (standalone HTML change)
11. Fix 8 — Runtime file fields (base_station + mode route)
12. Fix 12 — Unit tests (depends on all other fixes being in place)
