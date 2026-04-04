# RTKLIB Integration Hardening Plan

## Purpose

This document is the **post-merge hardening plan** for the RTKLIB output
integration work currently on `feat/rtklib-output-integration`.

The current PR gets the core architecture in place:

- Survey365 still owns `/dev/ttyGNSS`
- Survey365 still configures the receiver and serves status / mode APIs
- raw receiver bytes are relayed on localhost for RTKLIB consumption
- RTKLIB `str2str` now owns correction encoding / publishing
- the external local caster is proxied so rover visibility and GGA capture stay
  in Survey365
- RTKLIB child units are systemd-managed and installer-managed

That architectural direction is correct and should be preserved.

However, code review found a number of **follow-up fixes** that should be done
in a dedicated hardening branch rather than by continuing to pile changes onto
this already-large feature branch.

This plan therefore assumes:

1. the current RTKLIB integration PR is merged first,
2. the feature branch is cleaned up,
3. a new focused hardening branch is created,
4. all remaining bugs / design gaps / plan deviations are fixed there,
5. the branch is repeatedly deployed and tested on the Pi until behavior is
   stable,
6. only then is a new PR opened for review.

---

## Scope

This hardening plan covers **all currently known follow-up issues** identified in
review of the RTKLIB integration branch, including:

- broken native fallback behavior
- backend interface gaps
- receiver identity / descriptor gaps
- service-state reporting correctness
- output-engine state consistency
- local-caster health semantics
- runtime contract gaps
- outbound validation gaps
- parser robustness issues
- installer / sudoers gaps
- UI wording cleanup
- documentation alignment
- test coverage
- deployment / field verification / fix-forward loop

### Explicitly out of scope for this document

- **Firewall work for port 2110**

That topic is intentionally excluded from this plan per request. No tasks in
this document should add, remove, or discuss the loopback-protection firewall
rule.

---

## Why this should be a separate branch after merge

The current PR already delivers the core topology shift:

- receiver ownership stays in Survey365
- RTKLIB becomes the encoder / publisher
- local caster becomes proxy-backed
- output units become systemd-managed

That is the right architectural milestone.

The remaining work is best treated as **hardening and completion**, not as a
reason to keep the original branch open indefinitely. A clean follow-up branch
will make it easier to:

- isolate fix commits
- test incrementally on the Pi
- avoid rebasing a long-lived feature branch forever
- make review of the hardening PR much easier
- reduce risk while continuing iterative field validation

---

# Phase 0 — Merge, Clean Up, and Start Fresh

## Step 0.1 — Merge the current RTKLIB integration PR first

The first step is **not** to continue editing `feat/rtklib-output-integration`.
Merge it as the baseline RTKLIB architecture.

Suggested workflow:

```bash
git checkout main
git pull origin main
git merge --no-ff feat/rtklib-output-integration
git push origin main
```

If GitHub PR merge is used instead, that is fine too; the important thing is
that `main` becomes the baseline for all hardening work.

## Step 0.2 — Clean up the old feature branch

After merge:

```bash
git branch -d feat/rtklib-output-integration
git push origin --delete feat/rtklib-output-integration
```

Only do this after confirming the merge is complete and `main` is up to date.

## Step 0.3 — Create the new hardening branch

Create a fresh branch from updated `main`:

```bash
git checkout main
git pull origin main
git checkout -b fix/rtklib-integration-hardening
git push -u origin fix/rtklib-integration-hardening
```

All work described below happens on that new branch.

## Step 0.4 — Set the branch goal clearly

The hardening branch should have one simple success criterion:

> When the branch is done, RTKLIB mode, native rollback mode, status APIs,
> installer behavior, metadata generation, UI wording, and test coverage all
> match the intended design and are verified on the Pi.

---

# Phase 1 — Consolidated Issue Inventory

This section is the master checklist of issues to fix.

---

## Issue A — Native fallback mode is broken

### Background

The feature branch intentionally kept `rtcm_engine=native` as a one-release
rollback path. That means native mode must remain functional even though RTKLIB
is now the default engine.

Before the RTKLIB refactor, the local caster implementation acted as a direct
RTCM broadcaster and fit into `rtcm_fanout`.

After the refactor, `app/gnss/ntrip_caster.py` became a **reverse proxy** in
front of the internal RTKLIB caster.

### What is broken now

In native mode, `_start_base_native()` still instantiates `NTRIPCaster`, but:

- the refactored `NTRIPCaster` is now proxy-only
- it expects an upstream on port `2110`
- in native mode, no RTKLIB internal caster is running on `2110`
- the native path no longer adds the caster to `rtcm_fanout`
- the proxy no longer exposes the old `write()` broadcaster behavior

This means the promised native rollback path is not actually usable.

### Additional native regression

Native stop / teardown also regressed.

The old native stop path explicitly shut down native output behavior on the
receiver. The new common teardown path calls `configure_rover()`, but that only
disables raw output in RTKLIB mode. Native teardown therefore no longer fully
restores the previous behavior.

### Desired end state

Native fallback must either:

1. be restored fully for the one-release compatibility window, **or**
2. be intentionally removed from the product surface with migration/docs/UI
   updated to say native rollback is no longer supported.

This plan assumes the intended path is to **restore native mode correctly** for
one release, because that is what the original integration plan promised.

---

## Issue B — GNSS backend interface is inconsistent (`QuectelBackend`)

### Background

`GNSSManager` now assumes all backends provide enough surface area for:

- native mode
- RTKLIB mode
- receiver descriptor generation

### What is broken now

`app/gnss/quectel.py` is only a minimal stub and is missing:

- `receiver_model`
- `receiver_firmware`
- `enable_raw_output()`
- `disable_raw_output()`

So switching to `gnss_backend=quectel` can fail with `AttributeError` instead of
producing a clean, explicit “not implemented yet” behavior.

### Desired end state

Every backend must satisfy a consistent interface. Unsupported behavior should
fail **explicitly and predictably**, not through missing attributes.

---

## Issue C — MON-VER identity polling is missing

### Background

The RTKLIB integration plan explicitly required Survey365 to poll receiver
identity once at startup using UBX MON-VER and then use that information in the
metadata passed to RTKLIB.

That matters because RTKBase-style output metadata is expected to contain real
receiver identity, not placeholders.

### What is broken now

The u-blox backend still has firmware defaulted to `"unknown"`, and there is no
MON-VER polling or parsing path.

So the receiver descriptor and related sourcetable metadata are incomplete.

### Desired end state

At startup, Survey365 should:

- poll MON-VER once
- parse the returned software / hardware version information
- cache real receiver model / firmware
- use those values in the RTKLIB descriptor / sourcetable metadata

---

## Issue D — Receiver descriptor format does not match the intended RTKBase-style contract

### Background

The plan called for the RTKLIB `-i` descriptor to follow this field order:

`RTKBase {receiver_model},{survey365_version} {receiver_firmware}`

### What is broken now

The current descriptor logic builds something closer to:

`RTKBase {receiver_model},Survey365 {receiver_firmware}`

That has multiple problems:

- no actual Survey365 version number
- wrong field order relative to the planned format
- firmware is still usually `unknown`

### Desired end state

The metadata passed to RTKLIB should be deterministic, versioned, and match the
intended field layout.

---

## Issue E — Status APIs use cached service state instead of real systemd state

### Background

One goal of this integration was to make RTKLIB outputs be managed by systemd.
Once systemd owns those units, operator-visible status should reflect actual
unit state, not just what the application *thinks* it started.

### What is broken now

`/api/status` uses in-memory booleans maintained by the app rather than checking
real unit state.

That means status can drift from reality if:

- a `str2str` unit crashes
- a unit starts and then immediately exits
- systemd restarts or fails a unit
- someone manipulates a unit outside the normal app flow

### Desired end state

Operator-facing service status must reflect **real unit state**. It is fine to
cache or memoize if needed for performance, but not to rely solely on optimistic
internal flags.

---

## Issue F — Output-engine state is split between DB config and manager runtime state

### Background

The branch currently makes some engine decisions based on the DB config value
`rtcm_engine`, while `GNSSManager` separately keeps its own cached `_rtcm_engine`
loaded at startup.

### What is broken now

If the DB value changes while the app is running, different parts of the system
can disagree about which output engine is active.

That can affect both:

- startup behavior
- teardown behavior

This is especially risky during stop / reconfigure flows.

### Desired end state

There should be a clear distinction between:

- **configured engine**
- **currently active engine for the running session**

Teardown should use the engine that was actually started, not whatever the DB
happens to say later.

---

## Issue G — Local caster health reporting is inconsistent

### Background

There are two important operator views of local-caster state:

- `/api/status`
- `/api/ntrip/local-caster/clients`

Both should communicate the same reality.

### What is broken now

The proxy tracks upstream state in a way that is only partially tied to actual
health. For example:

- a healthy upstream may still appear inactive until traffic has flowed
- prior successful traffic can leave health looking better than it is
- `/api/status` uses one notion of state and the proxy snapshot uses another

### Desired end state

Local-caster state should be:

- internally consistent
- understandable from the UI/API
- clearly based on real runtime conditions

---

## Issue H — Runtime file contract is incomplete

### Background

The runtime file under `{data_dir}/rtklib/active-base.json` is the handoff
contract between Survey365 and the RTKLIB launcher.

The original plan described it as a complete description of the active base
session, not just a partial argument bundle.

### What is missing now

The current runtime file omits some planned fields, including items like:

- active mode
- external local-caster port
- fully self-describing session/runtime context

### Desired end state

The runtime file should be complete enough that:

- it is easy to debug by inspection
- launcher behavior is fully explained by the file
- operator/session context is recoverable from the file alone

---

## Issue I — Outbound profile validation is too weak

### Background

RTKLIB outbound push depends on DB-backed NTRIP profile values.

### What is broken now

If the default outbound profile is incomplete, Survey365 can still write a
runtime config and attempt to start the outbound unit with malformed or missing
values.

### Desired end state

Outbound startup should fail safely and clearly:

- incomplete profile → log warning and skip outbound unit
- valid profile → write correct runtime and start service

The rest of the stack should still come up when possible.

---

## Issue J — UBX fast-skip path can discard data using only header-derived length

### Background

The serial reader was optimized to avoid queueing high-volume UBX messages that
Survey365 itself does not need, while still relaying raw bytes to RTKLIB.

### What is broken now

The reader may discard an unneeded UBX frame based on the header-derived length
before verifying the checksum.

If the header length is corrupt, valid downstream bytes could be skipped.

### Desired end state

Filtering should only occur after a frame has been validated as structurally
correct.

---

## Issue K — Raw relay broadcasting has head-of-line blocking risk

### Background

The raw relay is designed to fan out the exact receiver byte stream to multiple
localhost clients, typically:

- RTKLIB local caster
- RTKLIB outbound push
- RTKLIB log service

### What is risky now

Broadcast currently drains clients sequentially. One slow client can delay the
others.

The queue is bounded, so slow consumers can also increase the chance of dropped
chunks.

### Desired end state

The relay should remain simple, but we should decide explicitly whether to:

- accept the current behavior as sufficient for expected load, or
- improve it so one slow consumer does not throttle the rest.

This does not necessarily have to become a large redesign, but it should be
reviewed and either improved or clearly documented.

---

## Issue L — Installer / sudoers gaps remain

### Background

The feature branch updated the Pi installer and service management rules, but a
few follow-up tasks are still needed.

### Known gaps

- `reset-failed` sudoers entries are missing for RTKLIB services
- `pkg-config` and `unzip` were requested in the original plan but are not yet
  in the dependency set

### Desired end state

Installer output, unit permissions, and prerequisites should match the intended
operational model exactly.

---

## Issue M — UI wording is only partially updated

### Background

The backend moved from Python-based RTCM/RINEX-style logging to RTKLIB raw GNSS
logging, but config keys were intentionally preserved for compatibility.

### What is wrong now

The admin UI still uses stale terms like:

- raw RTCM logging
- RINEX rotate hours
- RINEX data dir

This no longer reflects the actual data path.

### Desired end state

The UI should clearly describe:

- Survey365 owns the receiver
- RTKLIB generates corrections
- local caster is proxied for visibility
- raw logging is raw GNSS / UBX logging
- ellipsoid heights are what drive broadcast metadata

---

## Issue N — Documentation needs to match the hardened behavior exactly

### Background

The architecture docs were updated, but after the hardening work lands, the docs
must reflect the final actual behavior rather than the first pass.

### Desired end state

The following docs should be re-reviewed and corrected as needed after all code
changes are in place:

- `CLAUDE.md`
- `documentation/NATIVE-GNSS-ARCHITECTURE.md`
- this file

---

## Issue O — Test coverage is missing

### Background

The RTKLIB integration plan included a meaningful test matrix. None of that was
implemented in the initial feature branch.

### Desired end state

There should be focused automated tests covering at least:

- raw relay byte fidelity
- launcher argv generation
- proxy request / GGA capture behavior
- migration behavior for legacy `rtcm_messages`
- serial-reader filtering behavior

And there should be a documented Pi verification matrix for live testing.

---

## Issue P — Verify `NoNewPrivileges` behavior and document the final decision

### Background

The feature branch removed `NoNewPrivileges=true` from `survey365.service` to
support `sudo -n systemctl ...` child-unit control.

This is one of the few review items where the correct answer should be driven by
actual runtime testing rather than assumption.

### Desired end state

We should explicitly verify on the Pi whether `NoNewPrivileges=true` can be
restored without breaking child-unit control.

- If it can be restored safely, restore it.
- If it cannot, keep it removed and document the exact reason in the unit file
  comment and in deployment notes.

This should be resolved intentionally, not left ambiguous.

---

# Phase 2 — Implementation Workstreams

The fixes should be done as coordinated workstreams rather than random file
edits.

---

## Workstream 1 — Restore native rollback support cleanly

### Goal

Make `rtcm_engine=native` actually work again for the one-release compatibility
window.

### Implementation plan

1. Rework `app/gnss/ntrip_caster.py` so it can support **two explicit modes**:
   - **direct broadcast mode** for native output
   - **proxy mode** for RTKLIB output

2. Preserve shared client-session features across both modes:
   - request line capture
   - header capture
   - bytes in / out
   - incoming text
   - NMEA / GGA parsing
   - session history

3. In native mode, the local caster must once again:
   - act as an RTCMOutput-compatible sink
   - be attachable to `rtcm_fanout`
   - serve a direct sourcetable / stream without requiring an upstream

4. Update `_start_base_native()` in `app/gnss/base_station.py` so it:
   - creates the caster in direct mode
   - adds it to `manager.rtcm_fanout`
   - stores a reference for API visibility

5. Update teardown so native mode also clears the local caster reference and
   fully restores receiver state.

6. Confirm native stop behavior still disables the correct receiver-side native
   output behavior.

### Files expected to change

- `app/gnss/ntrip_caster.py`
- `app/gnss/base_station.py`
- possibly `app/gnss/manager.py` if helper hooks are needed

### Acceptance criteria

- native known-base start works
- native local caster serves RTCM directly
- native stop fully tears down outputs
- `/api/ntrip/local-caster/clients` still works in native mode

---

## Workstream 2 — Make GNSS backend contracts explicit and safe

### Goal

Ensure every backend exposes the attributes/methods the manager now depends on.

### Implementation plan

1. Add missing interface surface to `QuectelBackend`:
   - receiver identity attributes
   - raw-output method stubs

2. Optionally define a lightweight backend protocol or at least a docstring
   contract listing the required methods / attributes.

3. Fail unsupported paths with `NotImplementedError` and clear logging, not
   missing-attribute crashes.

### Files expected to change

- `app/gnss/quectel.py`
- possibly `app/gnss/manager.py` docstrings / typing

### Acceptance criteria

- selecting `quectel` never produces `AttributeError`
- unsupported operations fail explicitly and predictably

---

## Workstream 3 — Add receiver identity discovery and correct metadata formatting

### Goal

Generate real RTKBase-style metadata rather than placeholders.

### Implementation plan

1. Add UBX MON-VER support to the u-blox backend:
   - constants
   - poll helper
   - response parsing

2. Update the manager’s UBX filters so MON-VER is allowed through once.

3. Trigger the poll on connect/startup after basic startup config.

4. Add an application version source:
   - preferred: `app/version.py` with `__version__`

5. Update `receiver_descriptor()` to match the intended contract.

6. Replace hardcoded receiver label values in the RTKLIB runtime payload with
   values derived from actual backend/app identity.

### Files expected to change

- `app/gnss/ublox.py`
- `app/gnss/manager.py`
- `app/gnss/base_station.py`
- `app/version.py`

### Acceptance criteria

- logs show MON-VER parse at startup
- `active-base.json` contains real metadata
- sourcetable / RTKLIB descriptor use real model + version + firmware

---

## Workstream 4 — Unify configured engine vs active engine semantics

### Goal

Prevent DB config drift from breaking stop/start logic.

### Implementation plan

1. Add explicit runtime state to `GNSSManager`, e.g.:
   - configured engine (loaded from config)
   - active output engine for the running base session

2. When a base session starts, record the engine that was actually started.

3. When a base session stops, use the active runtime engine, not the current DB
   value.

4. Ensure teardown remains defensive if state is partially lost.

5. Audit all start/stop callers to make sure mode transitions remain consistent.

### Files expected to change

- `app/gnss/manager.py`
- `app/gnss/base_station.py`
- maybe `app/routes/mode.py` if explicit mode/engine state should be threaded

### Acceptance criteria

- changing `rtcm_engine` in config while a session is active does not break
  teardown
- stop always tears down the stack that is actually running

---

## Workstream 5 — Make status APIs reflect reality

### Goal

Have service status in the API match actual systemd state and actual proxy
health.

### Implementation plan

1. Rework `app/systemd.py` so it exposes helpers for:
   - `systemctl is-active`
   - optionally `systemctl show` / more detailed checks if needed
   - `reset-failed` support

2. Replace the optimistic in-memory-only status path with one of these:
   - real-time systemd checks on status requests, or
   - a short-lived cache refreshed from real systemd state

3. Define consistent local-caster health semantics across:
   - `/api/status`
   - `/api/ntrip/local-caster/clients`

4. Decide how to represent proxy vs upstream health separately so operators can
   understand what failed.

### Files expected to change

- `app/systemd.py`
- `app/routes/status.py`
- `app/gnss/ntrip_caster.py`
- maybe `app/routes/ntrip.py`

### Acceptance criteria

- if a child RTKLIB unit crashes, `/api/status` reflects that accurately
- local-caster status is consistent across endpoints
- proxy health vs upstream health is understandable

---

## Workstream 6 — Complete the runtime config contract

### Goal

Make `active-base.json` a complete, self-describing runtime handoff file.

### Implementation plan

1. Add missing fields such as:
   - active mode
   - external local-caster port
   - any other planned values needed for debugging / introspection

2. Pass explicit mode information from the mode route into `start_base()`.

3. Review launcher needs and ensure the runtime file documents all arguments it
   relies on.

4. Keep backward compatibility simple: the launcher only needs the new file
   shape used by the hardening branch.

### Files expected to change

- `app/gnss/base_station.py`
- `app/routes/mode.py`
- `app/rtklib/runtime.py`
- possibly `app/rtklib/launcher.py`

### Acceptance criteria

- runtime file contains all planned context fields
- file is easy to inspect for support/debugging

---

## Workstream 7 — Harden outbound startup validation

### Goal

Prevent malformed outbound config from starting a broken service.

### Implementation plan

1. Validate the selected outbound profile before writing runtime config.

2. Required values should be treated explicitly:
   - host must be present
   - mountpoint must be present
   - password behavior should be intentional and documented

3. If invalid:
   - log a clear warning
   - skip outbound unit startup
   - continue starting other outputs if possible

4. Update any UI copy or API validation only if necessary; keep existing DB
   schema stable.

### Files expected to change

- `app/gnss/base_station.py`
- possibly `app/routes/ntrip.py` if stronger validation belongs in the API too

### Acceptance criteria

- incomplete outbound profile no longer produces confusing RTKLIB startup
  failure
- other outputs still come up normally

---

## Workstream 8 — Fix serial-reader robustness

### Goal

Preserve the filtering optimization without risking buffer corruption from
header-only skipping.

### Implementation plan

1. Change UBX filter sequencing so frame validation occurs before filtered
   frames are discarded.

2. Keep the raw relay behavior unchanged: raw chunks must still be forwarded
   before parsing decisions.

3. Re-test queue pressure and parser correctness after the change.

### Files expected to change

- `app/gnss/serial_reader.py`

### Acceptance criteria

- filtered frames are still not queued to the app unnecessarily
- invalid header lengths cannot cause unchecked multi-byte skips

---

## Workstream 9 — Review and improve raw-relay broadcast behavior

### Goal

Decide whether the current sequential drain design is sufficient or should be
improved.

### Implementation plan

1. Measure current behavior with expected consumer count on the Pi.

2. If acceptable, document why.

3. If not acceptable, improve one of:
   - write-all then drain strategy
   - per-client buffering
   - lighter-weight backpressure handling

4. Keep the relay simple; do not over-engineer if real-world measurements show
   current behavior is adequate.

### Files expected to change

- `app/gnss/raw_relay.py`
- maybe new tests to validate behavior

### Acceptance criteria

- one slow consumer does not create unacceptable degradation for other expected
  RTKLIB consumers
- behavior is documented and test-covered

---

## Workstream 10 — Finish installer and service-management parity

### Goal

Bring `setup-pi.sh` and service-management behavior into line with the intended
operational model.

### Implementation plan

1. Add missing dependencies:
   - `pkg-config`
   - `unzip`

2. Add `reset-failed` sudoers entries for each RTKLIB unit.

3. Optionally expose a helper in `app/systemd.py` for `reset-failed`.

4. Verify the installer output remains idempotent and Pi-safe.

### Files expected to change

- `scripts/setup-pi.sh`
- `app/systemd.py`

### Acceptance criteria

- installer deploys the expected dependency set
- survey365 user can `reset-failed` RTKLIB units without a password

---

## Workstream 11 — Verify and document final systemd hardening choice

### Goal

Resolve the `NoNewPrivileges` question intentionally.

### Implementation plan

1. Test on the Pi whether `survey365.service` can keep `NoNewPrivileges=true`
   while still allowing `sudo -n systemctl ...` to manage child RTKLIB units.

2. If yes:
   - restore `NoNewPrivileges=true`
   - leave an explanatory comment

3. If no:
   - keep it removed
   - update the comment to state exactly why it cannot remain enabled

### Files expected to change

- `systemd/survey365.service`
- maybe docs / comments if explanation is needed

### Acceptance criteria

- final unit file reflects an intentional, tested decision

---

## Workstream 12 — Finish UI and documentation wording cleanup

### Goal

Make operator-facing text accurately describe the final architecture.

### Implementation plan

1. Update admin UI logging labels to describe raw GNSS logging instead of RTCM /
   RINEX wording.

2. Re-review all relevant notes in the UI so they clearly state:
   - Survey365 owns the receiver
   - RTKLIB generates corrections
   - outbound source pushes are password-oriented in RTKLIB mode
   - ellipsoid height drives broadcast metadata

3. Re-review docs after code is complete so docs match reality exactly.

### Files expected to change

- `ui/admin.html`
- `CLAUDE.md`
- `documentation/NATIVE-GNSS-ARCHITECTURE.md`
- this file if implementation details shift

### Acceptance criteria

- no stale “raw RTCM” / “RINEX” wording remains where it is now misleading
- docs read like the final product, not a halfway state

---

## Workstream 13 — Add automated tests

### Goal

Add focused tests around the new behavior so the hardening work is durable.

### Minimum automated test set

#### Raw relay tests

- exact byte preservation to multiple clients
- graceful handling of queue saturation
- dead-client cleanup

#### Launcher tests

- exact argv generation for:
  - local caster
  - outbound push
  - log role
- disabled-role behavior

#### Proxy tests

- source-table pass-through
- ICY stream pass-through
- inbound GGA capture and parsing
- upstream failure reporting

#### Migration tests

- legacy custom `rtcm_messages` copied into RTKLIB keys once
- old default does not overwrite RTKBase defaults incorrectly
- fresh install gets RTKLIB defaults

#### Serial reader tests

- raw chunk callback sees exact bytes
- filtered UBX messages are skipped only after validation

### Suggested test scaffolding

- `requirements-dev.txt`
- `pytest`
- `pytest-asyncio`
- `tests/` package with focused modules

### Files expected to change

- `requirements-dev.txt`
- `tests/...`

### Acceptance criteria

- the automated test suite passes locally and on the Pi

---

# Phase 3 — Detailed Execution Order

The recommended implementation order is:

1. **Merge current PR and branch cleanup**
2. **Quectel backend contract fix**
3. **Native fallback restoration**
4. **Engine-state unification**
5. **MON-VER + descriptor + version work**
6. **Status / real systemd state / local-caster health semantics**
7. **Runtime-file completion**
8. **Outbound validation**
9. **Serial-reader robustness fix**
10. **Installer and sudoers parity**
11. **NoNewPrivileges verification**
12. **UI/docs wording cleanup**
13. **Automated tests**
14. **Pi deployment + verification loop**
15. **Fix-forward until 100% clean**
16. **Open the hardening PR for review**

This order minimizes cross-file churn and allows meaningful incremental testing.

---

# Phase 4 — Pi Deployment and Verification Loop

This phase is mandatory. The hardening branch is not done until it has gone
through repeated deploy / verify / fix cycles on the actual Pi.

## Step 4.1 — Deploy the branch to the Pi

```bash
cd ~/survey365
git fetch origin
git checkout fix/rtklib-integration-hardening
git pull
sudo bash scripts/setup-pi.sh
sudo systemctl restart survey365
```

## Step 4.2 — Verify baseline app health

Confirm:

- Survey365 service starts
- UI loads
- GNSS receiver connects
- logs do not show immediate startup regressions

## Step 4.3 — Verify primary RTKLIB mode

Test at minimum:

1. known-base mode start
2. relative-base mode start
3. outbound push enabled / disabled cases
4. local caster enabled / disabled cases
5. raw logging enabled / disabled cases
6. stop / restart / resume flows
7. auto-resume true / false behavior after reboot

For each case, verify:

- expected services are active
- unexpected services are inactive
- `active-base.json` is correct when active
- runtime file is removed on stop
- `/api/status` is correct
- `/api/ntrip/local-caster/clients` is correct
- local-caster traffic / GGA capture behaves correctly

## Step 4.4 — Verify native fallback mode explicitly

Because native fallback regressed, it must be tested on purpose after being
restored.

At minimum verify:

- known-base with local caster in native mode
- outbound push in native mode if still supported
- stop returns receiver to the expected state
- client visibility still works

## Step 4.5 — Verify metadata output

Inspect:

- `active-base.json`
- RTKLIB logs
- sourcetable output
- operator-visible labels

Confirm that model / firmware / version metadata are populated and formatted as
intended.

## Step 4.6 — Verify installer / service-management behavior

Confirm on the Pi:

- new dependencies are installed
- sudoers includes all intended RTKLIB commands including `reset-failed`
- `systemd` helper behavior works from the app user
- final `survey365.service` hardening settings are intentional and documented

## Step 4.7 — Run automated tests on-device too

```bash
cd ~/survey365
source venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

The hardening branch should pass tests both locally and on the Pi.

---

# Phase 5 — Fix-Forward Until Clean

After the first deployment of the hardening branch, there will likely still be
follow-up fixes.

The correct process is:

1. deploy
2. verify
3. identify issue
4. fix on the hardening branch
5. commit
6. push
7. redeploy
8. re-verify
9. repeat until every checklist item passes cleanly

The branch is **not done** just because the code compiles. It is only done when:

- all known issues in this document are resolved
- automated tests pass
- Pi integration testing passes
- field-oriented verification passes
- UI / docs are aligned
- no new regressions remain open

---

# Phase 6 — Completion Criteria

The hardening branch is considered 100% complete only when all of the following
are true:

## Architecture correctness

- RTKLIB mode works cleanly
- native rollback mode works cleanly for the promised compatibility window
- receiver ownership remains in Survey365
- RTKLIB metadata is correct and no longer placeholder-based

## Runtime correctness

- start / stop / resume / auto-resume work reliably
- teardown uses the engine that is actually active
- status APIs reflect reality
- local-caster health reporting is internally consistent

## Installer / deployment correctness

- installer dependencies are complete
- sudoers rules are complete
- final systemd hardening choice is tested and documented

## Operator-facing correctness

- UI wording matches the actual product behavior
- docs match the final implementation

## Test completeness

- focused automated tests exist and pass
- Pi verification matrix has been run
- any issues found during deployment have been fixed and re-verified

---

# Phase 7 — Final Deliverable PR

Only after all completion criteria above are satisfied:

```bash
git push origin fix/rtklib-integration-hardening
```

Then open a new PR:

- **Title:** `fix: RTKLIB integration hardening`
- **Base:** `main`
- **Head:** `fix/rtklib-integration-hardening`

## PR description should include

1. reference to this document
2. summary of every issue fixed
3. summary of Pi deployment / verification steps performed
4. summary of automated tests added and run
5. note that the branch was repeatedly deployed / fixed / redeployed until
   behavior was stable

And only **after all of that** should the PR be sent for review.

---

# Working File Checklist

This is the expected primary change surface for the hardening branch.

| File | Purpose |
|---|---|
| `app/gnss/ntrip_caster.py` | restore native/direct mode and improve health semantics |
| `app/gnss/base_station.py` | native fix, engine tracking, outbound validation, runtime contract updates |
| `app/gnss/manager.py` | engine-state cleanup, descriptor logic, MON-VER flow integration |
| `app/gnss/quectel.py` | backend contract completion |
| `app/gnss/ublox.py` | MON-VER poll / parse support |
| `app/gnss/serial_reader.py` | checksum-before-skip fix |
| `app/gnss/raw_relay.py` | optional backpressure / broadcast refinement |
| `app/systemd.py` | real service-state helpers, reset-failed helper |
| `app/routes/status.py` | real service state and consistent health reporting |
| `app/routes/mode.py` | runtime mode threading if needed |
| `app/routes/ntrip.py` | optional validation / health-report alignment |
| `app/rtklib/runtime.py` | runtime file contract review |
| `app/rtklib/launcher.py` | runtime-field consumption review |
| `app/version.py` | version source for descriptor metadata |
| `scripts/setup-pi.sh` | deps + sudoers completion |
| `systemd/survey365.service` | final hardening setting decision |
| `ui/admin.html` | wording cleanup |
| `CLAUDE.md` | final architecture wording |
| `documentation/NATIVE-GNSS-ARCHITECTURE.md` | final architecture wording |
| `requirements-dev.txt` | test dependencies |
| `tests/...` | automated coverage |

---

# Final instruction

Do **not** start implementing these fixes on the current feature branch.

The correct sequence is:

1. merge the current PR,
2. clean up the old branch,
3. create `fix/rtklib-integration-hardening`,
4. implement the fixes above,
5. deploy to the Pi,
6. test and verify everything,
7. fix anything found,
8. redeploy and retest,
9. repeat until everything is 100% complete,
10. then open a new PR for review.
