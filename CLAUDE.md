# RTK Surveying

RTK GNSS base station system running on a Raspberry Pi 4. Two main components:

## Structure

- `base-station/` — Pi hardware config: WiFi, modem IMEI, and device setup docs
- `survey365/` — Field operations controller (FastAPI web app, map UI, native GNSS control)

## Pi Access

- **SSH**: `ssh jaredirby@rtkbase-pi` (Tailscale) or `ssh jaredirby@192.168.1.110` (LAN)
- **Web UI**: `https://rtkbase-pi.alligator-perch.ts.net` (Tailscale HTTPS via `tailscale serve`)
- **LAN**: `http://192.168.1.110` (wlan1 — wlan0 is not connected)
- **Tailscale IP**: `100.68.19.26`

## Deploy to Pi

```bash
# From the Pi:
cd ~/rtk-surveying
bash survey365/scripts/update.sh

# Or remotely:
ssh jaredirby@<pi-host> "cd ~/rtk-surveying && bash survey365/scripts/update.sh"
```

The update script fast-forwards to `origin/main`, installs pip deps if needed, and restarts the service. Automatic checks also run on boot and every 5 minutes on the Pi.

## First-Time Install

```bash
ssh jaredirby@<pi-host>
cd ~/rtk-surveying
sudo bash survey365/install.sh --user=jaredirby
```

## Key Services on Pi

| Service | Port | Description |
|---------|------|-------------|
| nginx | 80 | Reverse proxy for Survey365 |
| survey365 | 8080 | Field controller (FastAPI + native GNSS) |
| tailscale serve | 443 | HTTPS termination |

## Hardware

- Pi 4 Model B (2GB), Debian trixie (aarch64), Python 3.13
- u-blox ZED-F9P (ArduSimple simpleRTK2B) on `/dev/ttyGNSS`
- Waveshare SIM7600G-H 4G modem on `/dev/ttyUSB2`
- Alfa AWUS036ACH WiFi (wlan1) + onboard WiFi (wlan0)

## Managed Infrastructure

All Pi infrastructure is managed by `survey365/install.sh`. Config files in the repo are **templates** with `{user}` and `{home}` placeholders — never edit the deployed copies directly.

### Managed files

| Template (repo) | Deployed to | Auto-deployed by update.sh |
|-----------------|-------------|----------------------------|
| `survey365/systemd/survey365.service` | `/etc/systemd/system/survey365.service` | Yes |
| `survey365/systemd/survey365-boot.service` | `/etc/systemd/system/survey365-boot.service` | Yes |
| `survey365/systemd/survey365-update.service` | `/etc/systemd/system/survey365-update.service` | Yes |
| `survey365/systemd/survey365-update.timer` | `/etc/systemd/system/survey365-update.timer` | Yes |
| `survey365/nginx/survey365.conf` | `/etc/nginx/sites-available/survey365` | Yes |
| udev rule (inline in install.sh) | `/etc/udev/rules.d/99-survey365-gnss.rules` | No |
| sudoers (inline in install.sh) | `/etc/sudoers.d/survey365` | No |

### Rules

- **All infra changes go through the repo templates** — `install.sh` deploys them.
- `update.sh` auto-deploys systemd unit and nginx config changes on every pull.
- **New services, sudoers rules, or udev rules require re-running `install.sh`** on the Pi.
- Systemd templates use `{user}` and `{home}` — `sed` substitutes them at deploy time.
- Nginx config has no placeholders — it's copied directly.

## Logging

- **journalctl**: `journalctl -u survey365 -f` (systemd journal, always available)
- **File logs**: `survey365/data/logs/survey365.log` (rotating, 5MB x 3 backups)

## Important Notes

- Survey365 controls the F9P directly via serial (pyubx2).
- F9P antenna voltage is enabled automatically by GNSSManager on startup.
- GNSS config is stored in the Survey365 database (config table).
- Tailscale Serve proxies port 80 via HTTPS. WebSocket does NOT work through Tailscale Serve (HTTP/2 ALPN issue). The frontend falls back to HTTP polling.
- WiFi config is in `base-station/station.conf`. Run `sudo bash base-station/setup-wifi.sh` to apply.
