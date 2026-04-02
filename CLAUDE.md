# Survey365

Survey365 is a Raspberry Pi based RTK GNSS field controller. The repo is now flat: the app, UI, systemd templates, nginx template, migrations, and Pi scripts all live at the repo root.

## Current Layout

```text
app/
documentation/
migrations/
nginx/
scripts/
systemd/
ui/
requirements.txt
CLAUDE.md
AGENTS.md
```

Important scripts:

- `scripts/setup-pi.sh`: installs dependencies, deploys nginx/systemd/udev/sudoers, initializes the DB, imports legacy `station.conf` settings, and enables resilient mode.
- `scripts/update.sh`: checks `origin/main`, fast-forwards the repo, optionally runs `apt-get full-upgrade`, then reruns `scripts/setup-pi.sh`.
- `scripts/setup-wifi.sh`: reads `wifi_networks` from SQLite and writes managed NetworkManager connections for `wlan0` and `wlan1`.
- `scripts/enable-resilient-usb.sh`: wipes a USB disk, formats it, mounts it as the data volume, and calls `scripts/setup-pi.sh --resilient`.
- `scripts/bootstrap-pi.sh`: first-time Pi bootstrap script for a fresh box.

## Pi Access

- SSH over Tailscale: `ssh jaredirby@rtkbase-pi`
- SSH over LAN: `ssh jaredirby@192.168.1.110`
- Tailscale HTTPS UI: `https://rtkbase-pi.alligator-perch.ts.net`
- LAN UI: `http://192.168.1.110`
- Tailscale IP: `100.68.19.26`

## Hardware and OS

- Raspberry Pi 4 Model B, 2 GB RAM
- Debian trixie on aarch64
- Python 3.13
- u-blox ZED-F9P on `/dev/ttyGNSS`
- Waveshare SIM7600G-H on `/dev/ttyUSB2`
- Alfa AWUS036ACH on `wlan1` plus onboard Wi-Fi on `wlan0`
- `zram` swap stays enabled; do not replace it with disk-backed swap

## App Overview

Survey365 uses:

- FastAPI backend
- Alpine.js + HTMX frontend
- SQLite + SpatiaLite
- systemd for services
- nginx for local HTTP
- direct serial GNSS control with `pyubx2` and `pyserial`

Main admin APIs:

- `/api/config`
- `/api/ntrip`
- `/api/system/*`
- `/api/wifi`

Main field APIs:

- `/api/status`
- `/api/mode/*`
- `/api/sites`
- `/api/projects`

Default admin password: `survey365`

## Database-backed Device Config

The old `base-station/station.conf` file is gone. Its settings now live in SQLite.

Wi-Fi networks:

- stored in `wifi_networks`
- managed from the admin UI
- applied with `POST /api/wifi/apply` or `sudo bash scripts/setup-wifi.sh`
- passwords are write-only in the UI/API

Legacy migration behavior:

- `scripts/setup-pi.sh` will import settings from a legacy `base-station/station.conf` if present.
- If the file is gone from the worktree, the installer can recover the most recent committed copy from Git history and import from that.
- Existing DB values win; the importer only fills empty Wi-Fi settings.

## Managed Infrastructure

All Pi infrastructure is generated from repo templates by `scripts/setup-pi.sh`.

Managed files:

- `systemd/survey365.service` -> `/etc/systemd/system/survey365.service`
- `systemd/survey365-boot.service` -> `/etc/systemd/system/survey365-boot.service`
- `systemd/survey365-update.service` -> `/etc/systemd/system/survey365-update.service`
- `systemd/survey365-update-check.service` -> `/etc/systemd/system/survey365-update-check.service`
- `systemd/survey365-update-check.timer` -> `/etc/systemd/system/survey365-update-check.timer`
- `nginx/survey365.conf` -> `/etc/nginx/sites-available/survey365`
- inline udev rule -> `/etc/udev/rules.d/99-survey365-gnss.rules`
- inline sudoers policy -> `/etc/sudoers.d/survey365`

Template placeholders:

- `{user}`
- `{home}`
- `{repo_dir}`
- `{data_dir}`

Rules:

- Change templates in the repo, not directly on the Pi.
- Rerun `scripts/setup-pi.sh` after changing systemd, nginx, sudoers, or resilient-mode behavior.
- `scripts/update.sh` already reruns `scripts/setup-pi.sh`, so deployment stays single-sourced.

## Core Commands

Normal install:

```bash
cd ~/survey365
sudo bash scripts/setup-pi.sh --user=jaredirby
```

Manual app update:

```bash
cd ~/survey365
bash scripts/update.sh
```

Manual app update plus Debian upgrade and reboot:

```bash
cd ~/survey365
bash scripts/update.sh --os-upgrade
```

Enable resilient mode on a USB data disk:

```bash
cd ~/survey365
sudo bash scripts/enable-resilient-usb.sh --device=/dev/sda --user=jaredirby --force --reboot
```

Apply Wi-Fi profiles from the DB:

```bash
cd ~/survey365
sudo bash scripts/setup-wifi.sh
```

## Update Model

Updates are explicit maintenance actions.

- `survey365-update-check.timer` runs daily and only checks whether `origin/main` is ahead.
- The admin UI shows update availability.
- The admin UI triggers `survey365-update.service` only when the user chooses `Update Now`.
- `scripts/update.sh` can:
  - perform a read-only-safe check in `--auto` mode
  - fast-forward the repo
  - reinstall Python packages when `requirements.txt` changes
  - optionally run `apt-get full-upgrade`
  - rerun `scripts/setup-pi.sh`
  - reboot after OS package updates

During resilient mode, updates temporarily remount the OS writable, then restore read-only mode unless rebooting.

## Resilient Mode

Resilient mode is the unplug-tolerant operating mode for the Pi.

What it does:

- mounts `/` read-only during normal operation
- mounts `/boot/firmware` read-only
- keeps app data on a separate writable filesystem, usually `/srv/survey365`
- keeps volatile write-heavy paths on `tmpfs`
- keeps `fsck.repair=yes`
- keeps `zram`
- disables noisy unattended package timers
- preserves Tailscale state on the writable data volume

Persistent paths in resilient mode:

- database: `/srv/survey365/survey365.db`
- logs: `/srv/survey365/logs`
- RINEX: `/srv/survey365/rinex`
- Tailscale: `/srv/survey365/tailscale`
- systemd random seed: `/srv/survey365/systemd/random-seed`

Volatile paths in resilient mode:

- `/tmp`
- `/var/tmp`
- `/var/log`
- `/var/lib/nginx`
- `/var/lib/sudo`
- `/var/lib/chrony`

Installer behavior:

- resilient mode requires `--data-root` to be a separate mounted filesystem
- the installer writes `fstab` entries for the data volume and tmpfs mounts
- the installer repoints `/etc/resolv.conf` to `/run/NetworkManager/resolv.conf`
- journald is set to `Storage=volatile`
- helper commands are installed:
  - `survey365-root-rw`
  - `survey365-root-ro`
  - `survey365-maint-rw`
  - `survey365-maint-ro`

Maintenance pattern:

```bash
sudo /usr/local/bin/survey365-maint-rw
sudo apt update
sudo apt full-upgrade
sudo reboot
```

## Runtime Notes

- `survey365.service` uses `ProtectSystem=strict` and only writes to `{data_dir}`.
- Logging is written beside the active database when `SURVEY365_DB` is set.
- `scripts/setup-wifi.sh` creates managed `rtk-*` NetworkManager profiles on both adapters.
- `wlan1` gets the lower metric; `wlan0` gets `metric + 550`.

## Development Notes

- Prefer changing root-level paths; the old nested `survey365/` subtree is gone.
- Any remaining `~/rtk-surveying` reference is stale and should be removed.
- If you touch install/update behavior, keep bootstrap, update, and resilient-mode flows consistent.
