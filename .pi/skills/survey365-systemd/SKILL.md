---
name: survey365-systemd
description: Systemd service management for Survey365. Use when working on service units, timers, boot sequence, journal inspection, service lifecycle, or RTKLIB child service orchestration.
---

# Survey365 Systemd Services

## Service Inventory

### Core Services

| Unit | Type | Purpose | Auto-start |
|------|------|---------|------------|
| `survey365.service` | simple | Main FastAPI app (uvicorn on :8080) | Yes (enabled) |
| `survey365-boot.service` | oneshot | Hardware check on boot (boot.py) | Yes (enabled) |
| `survey365-update-check.timer` | timer | Daily check for git updates | Yes (enabled) |
| `survey365-update-check.service` | oneshot | Runs `update.sh --auto` (read-only check) | Timer-triggered |
| `survey365-update.service` | oneshot | Applies update (triggered by admin UI) | Manual only |

### RTKLIB Child Services

| Unit | Type | Purpose | Auto-start |
|------|------|---------|------------|
| `survey365-rtklib-local-caster.service` | simple | str2str → ntripc:// local caster on :2110 | No — started by app |
| `survey365-rtklib-outbound.service` | simple | str2str → ntrips:// push to remote caster | No — started by app |
| `survey365-rtklib-log.service` | simple | str2str → file:// raw GNSS logging | No — started by app |

## Service Dependencies

```
                    ┌─────────────────────────┐
                    │   survey365.service      │
                    │   (main app, :8080)      │
                    └─────────┬───────────────┘
                              │ PartOf + Requires + After
                    ┌─────────┼───────────────┐
                    │         │               │
           ┌────────▼──┐  ┌──▼─────────┐  ┌──▼──────────┐
           │ rtklib-    │  │ rtklib-    │  │ rtklib-     │
           │ local-     │  │ outbound   │  │ log         │
           │ caster     │  │            │  │             │
           └────────────┘  └────────────┘  └─────────────┘
```

- RTKLIB services use `PartOf=survey365.service` — they stop when the main service stops
- RTKLIB services use `Requires=survey365.service` + `After=survey365.service` — they can't start without it
- The outbound service additionally `Wants=network-online.target`

## How the App Manages RTKLIB Services

Survey365 does **not** use systemd to auto-start RTKLIB services. Instead:

1. When a base mode is activated (`POST /api/mode/known-base`), `base_station.py:start_base()` runs
2. It writes `active-base.json` with the runtime config (position, outputs, message sets)
3. It calls `sudo -n systemctl start survey365-rtklib-*.service` via `app/systemd.py`
4. Each RTKLIB service runs `python -m app.rtklib.launcher <role>` which reads `active-base.json` and `exec`s `str2str`
5. When the mode stops, `stop_base()` calls `sudo -n systemctl stop` on each service

The `app/systemd.py` module provides:
```python
await start_service("survey365-rtklib-local-caster.service")
await stop_service("survey365-rtklib-outbound.service")
await restart_service("survey365.service")
await systemctl_is_active("survey365-rtklib-log.service")
```

All calls use `sudo -n systemctl` (passwordless via sudoers).

## In-Memory RTKLIB State Tracking

`app/systemd.py` tracks which RTKLIB services were started by the app:

```python
@dataclass
class RTKLIBServiceState:
    local_caster: bool = False
    outbound: bool = False
    log: bool = False
```

This avoids querying systemctl on the hot path (the 1-second status WebSocket loop). The state is updated by `set_rtklib_service_state()` when services are started/stopped, and read by `get_rtklib_service_state()` for status reporting.

**Important:** Do not add synchronous `systemctl is-active` polling to the status hot path. Use the in-memory state.

## Template Placeholders

All service files in `systemd/` are templates with placeholders:

| Placeholder | Typical Value |
|-------------|---------------|
| `{user}` | `jaredirby` |
| `{home}` | `/home/jaredirby` |
| `{repo_dir}` | `/home/jaredirby/survey365` |
| `{data_dir}` | `/srv/survey365` (resilient) or `data/` (standard) |

`setup-pi.sh` performs `sed` substitution and copies to `/etc/systemd/system/`.

## Security Hardening

The main service and RTKLIB services use:
```ini
ProtectSystem=strict       # Read-only /usr, /boot, /etc
ReadWritePaths={data_dir}  # Only the data directory is writable
PrivateTmp=true            # Isolated /tmp
NoNewPrivileges=true       # (RTKLIB services only)
```

The main service needs `sudo -n systemctl` access to manage RTKLIB child units, so it cannot use `NoNewPrivileges=true`.

## Sudoers Rules

`/etc/sudoers.d/survey365` grants the target user passwordless access to:
- `systemctl start/stop/restart/is-active` for all Survey365 services
- `systemctl start survey365-update.service`
- `systemctl reboot`, `systemctl daemon-reload`
- `apt-get *`
- `scripts/setup-pi.sh`, `scripts/setup-wifi.sh`
- The four `survey365-root-rw/ro` and `survey365-maint-rw/ro` helpers
- `tee` to `/etc/systemd/system/survey365*` (for auto-deploy)
- `cp` and `nginx -t/-s` for nginx config updates

## Journal Inspection

```bash
# Live logs for the main app
journalctl -u survey365 -f

# Recent logs (last 5 minutes)
journalctl -u survey365 --since "5 min ago" --no-pager

# RTKLIB local caster logs
journalctl -u survey365-rtklib-local-caster -f

# Boot service output
journalctl -u survey365-boot --no-pager

# Update check history
journalctl -u survey365-update-check --no-pager

# All Survey365-related logs
journalctl -t survey365 -t survey365-boot -t survey365-rtklib-local-caster -t survey365-rtklib-outbound -t survey365-rtklib-log --since "1 hour ago"

# Timer status (when did the update check last run?)
systemctl list-timers survey365-*
```

## Update Timer

```ini
[Timer]
OnBootSec=15min          # First check 15 min after boot
OnUnitActiveSec=1d       # Then every 24 hours
AccuracySec=15min        # Allow ±15 min scheduling flexibility
Persistent=true          # Run missed checks after sleep/shutdown
```

The timer only triggers `update.sh --auto` which is a read-only check. Updates are applied manually via the admin UI or `update.sh` without `--auto`.

## Common Operations

```bash
# Restart the main app
sudo systemctl restart survey365

# Check all service states
for s in survey365{,-boot,-update,-update-check,-rtklib-local-caster,-rtklib-outbound,-rtklib-log}; do
    printf "%-45s %s\n" "$s" "$(systemctl is-active $s.service 2>/dev/null || echo unknown)"
done

# Reload after changing unit files
sudo systemctl daemon-reload

# Tail all Survey365 logs at once
journalctl -u 'survey365*' -f
```
