---
name: survey365-pi-deploy
description: Deployment and update workflow for Survey365 on Raspberry Pi. Use when deploying, updating, installing, or troubleshooting the Pi, running setup-pi.sh, managing systemd services, or SSHing to the device. Covers SSH access, the install/update flow, template placeholders, and pre/post-deploy checks.
---

# Survey365 Pi Deployment

## SSH Access

| Method | Command |
|--------|---------|
| Tailscale | `ssh jaredirby@rtkbase-pi` |
| LAN | `ssh jaredirby@192.168.1.110` |
| Tailscale IP | `ssh jaredirby@100.68.19.26` |

The Tailscale hostname is the preferred method — it works from anywhere.

## Hardware

- Raspberry Pi 4 Model B, 2 GB RAM
- Debian trixie on aarch64, Python 3.13
- u-blox ZED-F9P on `/dev/ttyGNSS`
- Waveshare SIM7600G-H on `/dev/ttyUSB2`
- Alfa AWUS036ACH on `wlan1` + onboard Wi-Fi `wlan0`
- `zram` swap — never replace with disk-backed swap

## Core Commands

### Normal install
```bash
cd ~/survey365
sudo bash scripts/setup-pi.sh --user=jaredirby
```

### Manual update (git pull + redeploy)
```bash
cd ~/survey365
bash scripts/update.sh
```

### Update with Debian upgrade + reboot
```bash
cd ~/survey365
bash scripts/update.sh --os-upgrade
```

### Enable resilient mode on USB data disk
```bash
cd ~/survey365
sudo bash scripts/enable-resilient-usb.sh --device=/dev/sda --user=jaredirby --force --reboot
```

### Apply Wi-Fi profiles from DB
```bash
cd ~/survey365
sudo bash scripts/setup-wifi.sh
```

## What `setup-pi.sh` Does (in order)

1. Installs system deps: `python3-venv`, `spatialite`, `nginx`, `build-essential`
2. Adds target user to `dialout` group for serial port access
3. Creates Python venv at `$REPO_DIR/venv`, installs pip packages from `requirements.txt`
4. Builds and installs RTKLIB `str2str` v2.5.0 from source (pinned)
5. Creates data directories (`$DATA_ROOT/`, `logs/`, `rinex/`, `tailscale/`)
6. Migrates data from `data/` to `$DATA_ROOT` if different
7. Runs `init_db()` — creates tables, runs all numbered migrations
8. Imports legacy `station.conf` Wi-Fi settings if present (or recovered from git history)
9. Deploys udev rule: `/etc/udev/rules.d/99-survey365-gnss.rules` (symlinks F9P to `/dev/ttyGNSS`)
10. Generates self-signed SSL cert if missing
11. Deploys nginx config from `nginx/survey365.conf`
12. Deploys all systemd units from `systemd/` with placeholder substitution
13. Installs helper scripts (`survey365-root-rw`, `survey365-root-ro`, `survey365-maint-rw`, `survey365-maint-ro`)
14. Configures resilient mode if `--resilient` flag is set
15. Installs sudoers rules for passwordless service management
16. Enables and starts `survey365.service`, `survey365-boot.service`, `survey365-update-check.timer`, `nginx`

## What `update.sh` Does

1. `git fetch origin` + fast-forward merge to `origin/main`
2. Reinstalls pip packages if `requirements.txt` changed
3. Optionally runs `apt-get full-upgrade` if `--os-upgrade`
4. Reruns `scripts/setup-pi.sh` (full idempotent redeploy)
5. Reboots after OS package updates

In `--auto` mode (used by the daily timer), it only checks whether `origin/main` is ahead — no changes are applied.

## Template Placeholders

Systemd units use these placeholders, substituted by `setup-pi.sh`:

| Placeholder | Value |
|-------------|-------|
| `{user}` | Target user (e.g., `jaredirby`) |
| `{home}` | User home directory |
| `{repo_dir}` | Git repo root (e.g., `/home/jaredirby/survey365`) |
| `{data_dir}` | Data root (e.g., `/srv/survey365` in resilient mode, or `data/` in standard mode) |

**Rule: Always change templates in the repo, never directly on the Pi.** Rerun `setup-pi.sh` to deploy changes.

## Managed Infrastructure Files

| Repo Source | Deployed To |
|-------------|-------------|
| `systemd/survey365.service` | `/etc/systemd/system/survey365.service` |
| `systemd/survey365-boot.service` | `/etc/systemd/system/survey365-boot.service` |
| `systemd/survey365-update.service` | `/etc/systemd/system/survey365-update.service` |
| `systemd/survey365-update-check.service` | `/etc/systemd/system/survey365-update-check.service` |
| `systemd/survey365-update-check.timer` | `/etc/systemd/system/survey365-update-check.timer` |
| `systemd/survey365-rtklib-local-caster.service` | `/etc/systemd/system/survey365-rtklib-local-caster.service` |
| `systemd/survey365-rtklib-outbound.service` | `/etc/systemd/system/survey365-rtklib-outbound.service` |
| `systemd/survey365-rtklib-log.service` | `/etc/systemd/system/survey365-rtklib-log.service` |
| `nginx/survey365.conf` | `/etc/nginx/sites-available/survey365` |
| Inline udev rule | `/etc/udev/rules.d/99-survey365-gnss.rules` |
| Inline sudoers policy | `/etc/sudoers.d/survey365` |

## Pre-Deploy Checks

```bash
# Is the Pi reachable?
ssh jaredirby@rtkbase-pi 'echo ok'

# Is rootfs read-only? (resilient mode)
ssh jaredirby@rtkbase-pi 'mount | grep "on / " | grep -o "ro\|rw"'

# If read-only, remount writable for deploy:
ssh jaredirby@rtkbase-pi 'sudo survey365-maint-rw'

# Is the data volume mounted?
ssh jaredirby@rtkbase-pi 'mountpoint -q /srv/survey365 && echo mounted || echo NOT mounted'
```

## Post-Deploy Verification

```bash
# Service status
ssh jaredirby@rtkbase-pi 'systemctl is-active survey365'

# Health check
ssh jaredirby@rtkbase-pi 'curl -s http://localhost:8080/api/status | python3 -m json.tool | head -20'

# Recent logs
ssh jaredirby@rtkbase-pi 'journalctl -u survey365 --since "5 min ago" --no-pager'

# GNSS connection
ssh jaredirby@rtkbase-pi 'curl -s http://localhost:8080/api/status | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"connected={d[\"gnss\"][\"connected\"]} fix={d[\"gnss\"][\"fix_type\"]}\")"'

# Restore read-only rootfs after deploy
ssh jaredirby@rtkbase-pi 'sudo survey365-maint-ro'
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SURVEY365_DB` | `data/survey365.db` | SQLite database path |
| `GNSS_PORT` | `/dev/ttyGNSS` | Serial port for GNSS receiver |
| `GNSS_BAUD` | `115200` | Serial baud rate |
| `GNSS_BACKEND` | `ublox` | Receiver backend (`ublox` or `quectel`) |
