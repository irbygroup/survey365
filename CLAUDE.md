# RTK Surveying

RTK GNSS base-station system running on a Raspberry Pi 4. The repo has two main areas:

- `base-station/` — Pi hardware config, Wi-Fi setup, modem notes
- `survey365/` — FastAPI field app, UI, GNSS control, systemd/nginx templates, install/update scripts

## Pi Access

- SSH over Tailscale: `ssh jaredirby@rtkbase-pi`
- SSH over LAN: `ssh jaredirby@192.168.1.110`
- Tailscale HTTPS UI: `https://rtkbase-pi.alligator-perch.ts.net`
- LAN UI: `http://192.168.1.110`
- Tailscale IP: `100.68.19.26`

If Tailscale SSH is flaky, use the LAN IP. The web UI is still fronted by `tailscale serve` for remote access.

## Hardware and OS

- Raspberry Pi 4 Model B, 2 GB RAM
- Debian trixie on aarch64
- Python 3.13
- u-blox ZED-F9P on `/dev/ttyGNSS`
- Waveshare SIM7600G-H on `/dev/ttyUSB2`
- Alfa AWUS036ACH on `wlan1` plus onboard Wi-Fi on `wlan0`
- `zram` swap enabled; do not replace it with disk-backed swap

## Survey365 Overview

Survey365 is a map-centric field operations controller with native GNSS control.

- Backend: FastAPI, async Python, SQLite + SpatiaLite
- Frontend: HTMX, Alpine.js, MapLibre GL JS, Pico.css
- Reverse proxy: nginx
- Process manager: systemd
- GNSS control: direct serial I/O with `pyubx2` and `pyserial`

## Survey365 Structure

```text
survey365/
  app/
    main.py
    db.py
    boot.py
    auth.py
    gnss/
      manager.py
      serial_reader.py
      state.py
      ublox.py
      rtcm_fanout.py
      base_station.py
      ntrip_client.py
      ntrip_push.py
      ntrip_caster.py
      rinex_logger.py
    routes/
      status.py
      mode.py
      sites.py
      ntrip.py
      config.py
      auth.py
      system.py
    ws/
      live.py
  ui/
    index.html
    admin.html
    login.html
    js/
    css/
  migrations/
  nginx/
    survey365.conf
  systemd/
    survey365.service
    survey365-boot.service
    survey365-update.service
    survey365-update-check.service
    survey365-update-check.timer
  scripts/
    install.sh
    update.sh
    enable-resilient-usb.sh
```

## GNSS Architecture

```text
/dev/ttyGNSS
    |
GNSSManager
    |
    +-- UBloxBackend
    +-- GNSSState updates
    +-- RTCM fan-out
          +-- RINEX logger
          +-- outbound NTRIP push
          +-- local NTRIP caster
```

Important design points:

- Survey365 owns the receiver directly. There is no separate relay process.
- GNSS state, mode transitions, and WebSocket clients are all in-process.
- The frontend prefers WebSocket and falls back to HTTP polling.
- Tailscale Serve proxies HTTPS, but WebSocket is unreliable there, so polling fallback matters.

## API Notes

Main field routes are unauthenticated:

- `GET /api/status`
- `GET /api/satellites`
- `GET /api/mode`
- `POST /api/mode/known-base`
- `POST /api/mode/relative-base`
- `POST /api/mode/stop`
- `POST /api/mode/resume`
- `GET /api/sites`

Admin routes require the session cookie:

- `/api/sites` writes
- `/api/ntrip`
- `/api/config`
- `/api/auth/login`
- `/api/system/*` update and maintenance endpoints

Default admin password: `survey365`

## Managed Infrastructure

All Pi infrastructure is managed by `survey365/scripts/install.sh`. Repo files are templates; deployed files on the Pi are generated from them.

Managed templates:

- `survey365/systemd/survey365.service` -> `/etc/systemd/system/survey365.service`
- `survey365/systemd/survey365-boot.service` -> `/etc/systemd/system/survey365-boot.service`
- `survey365/systemd/survey365-update.service` -> `/etc/systemd/system/survey365-update.service`
- `survey365/systemd/survey365-update-check.service` -> `/etc/systemd/system/survey365-update-check.service`
- `survey365/systemd/survey365-update-check.timer` -> `/etc/systemd/system/survey365-update-check.timer`
- `survey365/nginx/survey365.conf` -> `/etc/nginx/sites-available/survey365`
- inline udev rule -> `/etc/udev/rules.d/99-survey365-gnss.rules`
- inline sudoers policy -> `/etc/sudoers.d/survey365`

Rules:

- Infrastructure changes should be made in the repo templates, not directly on the Pi.
- `survey365/scripts/update.sh` re-runs `survey365/scripts/install.sh`, so deployment logic stays single-sourced.
- New services, sudoers changes, or resilient-mode changes require rerunning `survey365/scripts/install.sh`.
- systemd templates use `{user}` and `{home}` placeholders.

## Core Commands

First-time install:

```bash
cd ~/rtk-surveying
sudo bash survey365/scripts/install.sh --user=jaredirby
```

App update:

```bash
cd ~/rtk-surveying
bash survey365/scripts/update.sh
```

App update plus Debian package upgrade and reboot:

```bash
cd ~/rtk-surveying
bash survey365/scripts/update.sh --os-upgrade
```

Enable resilient mode on a USB disk:

```bash
cd ~/rtk-surveying
sudo bash survey365/scripts/enable-resilient-usb.sh --device=/dev/sda --user=jaredirby --force --reboot
```

## Update Model

Updates are no longer background auto-apply operations.

- `survey365-update-check.timer` runs daily and only checks whether `origin/main` has a newer commit.
- The app can surface `update available` and trigger a manual apply.
- `survey365-update.service` runs `survey365/scripts/update.sh`.
- `survey365/scripts/update.sh` can:
  - check for updates in read-only-safe `--auto` mode
  - fast-forward the repo
  - reinstall Python packages when `requirements.txt` changes
  - optionally run `apt-get full-upgrade`
  - rerun `survey365/scripts/install.sh`
  - reboot after OS upgrades

During updates on a resilient Pi, the script temporarily remounts `/` read-write and restores read-only mode on exit unless it is rebooting.

## Resilient Mode

Resilient mode is the unplug-tolerant operating mode for the Pi.

What it does:

- mounts `/` read-only during normal operation
- mounts `/boot/firmware` read-only
- keeps persistent Survey365 data on a separate writable filesystem, typically `/srv/survey365`
- moves volatile, write-heavy paths to `tmpfs`
- keeps `fsck.repair=yes`
- keeps `zram`
- disables noisy background package timers
- preserves update capability by using a short maintenance window

Resilient mode requires a separate mounted filesystem for `--data-root`. The installer will refuse `--resilient` if the path is just a directory on `/`.

### Persistent Paths in Resilient Mode

Under the data root, typically `/srv/survey365`:

- `db/` — `survey365.db`, WAL, SHM
- `rinex/` — raw GNSS logs
- `tailscale/` — Tailscale state
- `systemd/random-seed` — persisted random seed

The service uses `SURVEY365_DB` from the systemd environment to point at the persistent database path.

### Volatile Paths in Resilient Mode

The installer configures these as `tmpfs`:

- `/tmp`
- `/var/tmp`
- `/var/log`
- `/var/lib/sudo`
- `/var/lib/chrony`

It also:

- sets journald to `Storage=volatile`
- bind-mounts persistent Tailscale state into `/var/lib/tailscale`
- repoints `/etc/resolv.conf` to `/run/NetworkManager/resolv.conf`
- moves `/var/lib/systemd/random-seed` onto the data filesystem

### Filesystem and Durability Details

- SQLite is configured for `PRAGMA journal_mode=WAL`
- SQLite is configured for `PRAGMA synchronous=FULL`
- the writable data filesystem is mounted with normal ext4 semantics, not global `sync`
- `fsck.repair=yes` remains enabled for recovery after unclean shutdown

### Background Services Disabled in Resilient Mode

The installer disables:

- `apt-daily.timer`
- `apt-daily-upgrade.timer`
- `man-db.timer`

This is deliberate. OS updates become explicit maintenance actions.

### Maintenance Helpers

The installer provides:

- `/usr/local/bin/survey365-root-rw`
- `/usr/local/bin/survey365-root-ro`
- `/usr/local/bin/survey365-maint-rw`
- `/usr/local/bin/survey365-maint-ro`

Use the maintenance helpers when you need a manual OS maintenance window outside the normal app update flow.

### USB Provisioning Helper

`survey365/scripts/enable-resilient-usb.sh` will:

- wipe the target USB disk
- create a GPT with one ext4 partition
- format it as `survey365-data`
- mount it at the data root
- call `survey365/scripts/install.sh --resilient --data-device=UUID=...`
- optionally reboot

The script refuses likely system disks and requires `--force`.

## Logging and Verification

Useful commands:

```bash
journalctl -u survey365 -f
journalctl -u survey365-update.service -n 50 --no-pager
journalctl -u survey365-update-check.service -n 50 --no-pager
systemctl status survey365 nginx tailscaled
findmnt /
findmnt /srv/survey365
```

For resilient mode verification after reboot, check:

- `/` is mounted read-only
- `/srv/survey365` is mounted from the USB or other dedicated data filesystem
- `survey365`, `nginx`, and `tailscaled` are active
- `survey365-update-check.timer` is enabled

## Important Notes

- Wi-Fi config is in `base-station/station.conf`; apply it with `sudo bash base-station/setup-wifi.sh`.
- Survey365 enables F9P antenna voltage during startup.
- GNSS config lives in the Survey365 database.
- If you are changing infra behavior, update the repo templates first, then redeploy with `survey365/scripts/install.sh` or `survey365/scripts/update.sh`.
