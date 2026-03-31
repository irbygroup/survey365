# RTK Surveying

RTK GNSS base station system running on a Raspberry Pi 4. Two main components:

## Structure

- `base-station/` — Pi hardware config: WiFi, modem IMEI, RTKBase install docs
- `survey365/` — Field operations controller (FastAPI web app, map UI, native GNSS control)

## Pi Access

- **SSH**: `ssh jaredirby@rtkbase-pi` (Tailscale) or `ssh jaredirby@192.168.1.162` (LAN)
- **Web UI**: `https://rtkbase-pi.alligator-perch.ts.net` (Tailscale HTTPS via `tailscale serve`)
- **Direct IPs**: `100.68.19.26` (Tailscale), `192.168.1.162` (wlan0), `192.168.1.110` (wlan1)

## Deploy to Pi

```bash
# From the Pi:
cd ~/rtk-surveying
bash survey365/scripts/update.sh

# Or remotely:
ssh jaredirby@rtkbase-pi "cd ~/rtk-surveying && bash survey365/scripts/update.sh"
```

The update script: git pulls, installs pip deps if changed, stamps cache version into HTML, restarts the service.

## First-Time Install

```bash
ssh jaredirby@rtkbase-pi
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

## Important Notes

- Survey365 controls the F9P directly via serial (pyubx2). No RTKBase dependency.
- F9P antenna voltage is enabled automatically by GNSSManager on startup.
- GNSS config is stored in the Survey365 database (config table), not settings.conf.
- Tailscale Serve proxies port 80 via HTTPS. WebSocket does NOT work through Tailscale Serve (HTTP/2 ALPN issue). The frontend falls back to HTTP polling.
- WiFi config is in `base-station/rtkbase.conf`. Run `sudo bash base-station/setup-wifi.sh` to apply.
- RTKBase is still installed at `~/rtkbase` but all its services are disabled. Can be removed later.
