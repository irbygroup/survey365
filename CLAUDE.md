# RTK Surveying

RTK GNSS base station system running on a Raspberry Pi 4. Two main components:

## Structure

- `base-station/` — Pi hardware config: WiFi, modem IMEI, RTKBase install docs
- `survey365/` — Field operations controller (FastAPI web app, map UI)

## Pi Access

- **SSH**: `ssh jaredirby@rtkbase-pi` (Tailscale) or `ssh jaredirby@192.168.1.162` (LAN)
- **Web UI**: `https://rtkbase-pi.alligator-perch.ts.net` (Tailscale HTTPS via `tailscale serve`)
- **RTKBase**: `https://rtkbase-pi.alligator-perch.ts.net/rtkbase/` (proxied by nginx)
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
| nginx | 80 | Reverse proxy for both apps |
| survey365 | 8080 | Field controller (FastAPI) |
| rtkbase_web | 8000 | RTKBase web UI |
| str2str_tcp | 5015 | F9P GNSS data relay |
| tailscale serve | 443 | HTTPS termination |

## Hardware

- Pi 4 Model B (2GB), Debian trixie (aarch64), Python 3.13
- u-blox ZED-F9P (ArduSimple simpleRTK2B) on `/dev/ttyGNSS`
- Waveshare SIM7600G-H 4G modem on `/dev/ttyUSB2`
- Alfa AWUS036ACH WiFi (wlan1) + onboard WiFi (wlan0)

## Important Notes

- F9P antenna voltage must be enabled after any RTKBase `--configure-gnss` run. Survey365's boot service handles this automatically.
- RTKBase settings.conf is at `~/rtkbase/settings.conf`. Survey365 reads/writes the `position` field.
- Tailscale Serve proxies port 80 via HTTPS. WebSocket does NOT work through Tailscale Serve (HTTP/2 ALPN issue). The frontend falls back to HTTP polling.
- WiFi config is in `base-station/rtkbase.conf`. Run `sudo bash base-station/setup-wifi.sh` to apply.
