# RTK Base Station Pi — Install Guide

## Hardware

| Field | Value |
|-------|-------|
| Board | Raspberry Pi 4 Model B Rev 1.5 |
| OS | Debian GNU/Linux 13 (trixie) aarch64 |
| Kernel | 6.12.47+rpt-rpi-v8 |
| Hostname | `rtkbase-pi` |
| Local IP | 192.168.1.162 (wlan0), 192.168.1.110 (wlan1) |
| Tailscale IP | 100.68.19.26 |
| Tailscale hostname | `rtkbase-pi.alligator-perch.ts.net` |
| Tailscale tag | `tag:rtksurveying` |
| Modem | Waveshare SIM7600G-H 4G USB Dongle |
| Modem USB ID | `1e0e:9001` (SimTech) |
| Modem AT port | `/dev/ttyUSB2` |
| Original IMEI | `862636051970786` |
| Active IMEI | `352741384997469` (Samsung Galaxy A10e) |
| WiFi (internal) | wlan0 — onboard Pi 4 WiFi |
| WiFi (external) | wlan1 — Alfa AWUS036ACH (mt76x2u driver) |
| GNSS Receiver | ArduSimple simpleRTK2B Budget (u-blox ZED-F9P) |
| GNSS USB ID | `1546:01a9` (U-Blox AG) |
| GNSS serial port | `/dev/ttyGNSS` (symlink to `/dev/ttyACM0`) |
| F9P firmware | 1.32 |
| GNSS Antenna | ArduSimple Calibrated Survey Tripleband + L-band (IP67) |
| RTKBase version | 2.7.0 |
| RTKBase web UI | `http://100.68.19.26` (Tailscale) or `http://192.168.1.162` (LAN) |
| RTKBase password | `admin` (default — change this) |

## Initial Setup

### 1. Fan Configuration

```
sudo raspi-config
  -> Performance Options
    -> Fan
      -> GPIO 14
      -> OK
      -> 60 degree C trigger temperature
      -> Fan on
```

### 2. Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --authkey=<YOUR_AUTH_KEY> --hostname=rtkbase-pi
sudo tailscale set --ssh
sudo systemctl enable tailscaled
```

Tailscale SSH is enabled — allows passwordless SSH from authorized devices without needing the local user password. Enabled on boot via systemd.

### 3. SSH Key Setup

Added authorized keys for passwordless SSH from both workstations:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
cat >> ~/.ssh/authorized_keys << 'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIZDEKdM6Jo4kQGNZD/sLYsyHKF0M2vqurK4cFXF0TQs jaredirby@jareds-mac-mini
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILPz5BQ22gQqgfoWrAXBDdTFkpDPFOuKAvUNtXDhGcPx jaredirby@jareds-mac-work
EOF
chmod 600 ~/.ssh/authorized_keys
```

Connect from either Mac:
```bash
ssh jaredirby@rtkbase-pi
```

### 4. Fix Locale

The Pi ships without `en_US.UTF-8` generated, causing warnings on SSH login from macOS.

```bash
sudo sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
sudo locale-gen
sudo update-locale LANG=en_US.UTF-8
```

### 5. OS Update

```bash
sudo apt update && sudo apt full-upgrade -y
```

### 6. Install Tools

```bash
sudo apt install -y git gh python3-serial minicom
```

### 7. Clone This Repo

```bash
echo "<GITHUB_TOKEN>" | gh auth login --with-token
gh repo clone irbygroup/rtk-surveying
```

## Tailscale ACL — One-Way Access

The `tag:rtksurveying` tag is configured in the tailnet ACL with one-way access:

- `tag:jared` (your workstations) **can** connect to `tag:rtksurveying` (SSH, all ports)
- `tag:rtksurveying` **cannot** connect outbound to any other device on the tailnet

This was set via the Tailscale API:

```bash
# Add tag:rtksurveying to tagOwners in ACL policy
# Add tag:rtksurveying to the SSH accept rule for tag:jared
# No outbound grants for tag:rtksurveying
# Retag device:
curl -X POST -H "Authorization: Bearer $TS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tags":["tag:rtksurveying"]}' \
  "https://api.tailscale.com/api/v2/device/<DEVICE_ID>/tags"
```

## WiFi — Dual Adapter with Failover

Both adapters connect to all configured networks. The Alfa USB adapter (wlan1) is preferred when plugged in; the internal WiFi (wlan0) is the automatic fallback.

| Interface | Adapter | Route Metric | Role |
|-----------|---------|-------------|------|
| wlan1 | Alfa AWUS036ACH (mt76x2u) | 50 | Preferred — long-range external |
| wlan0 | Onboard Pi 4 | 600 | Fallback — internal |

### Configuration

All WiFi networks are defined in `rtkbase.conf` as `WIFI_<n>` entries:

```
WIFI_1=TranquilityHarbor|<psk>|20|50
WIFI_2=Jared Phone|<psk>|10|50
```

Format: `SSID|PASSWORD|PRIORITY|METRIC`

- **PRIORITY**: higher = tried first (NetworkManager autoconnect-priority)
- **METRIC**: lower = preferred route when multiple interfaces connected

### Apply WiFi Config

```bash
cd ~/rtk-surveying
git pull
sudo bash base-station/setup-wifi.sh
```

The script is safe to re-run — it deletes old `rtk-` connections before recreating. To add a new network, add a `WIFI_<n>` line to `rtkbase.conf` and re-run.

## SIM7600G-H Modem — IMEI Setup

The modem's original IMEI (`862636051970786`) is not recognized by carriers like Straight Talk because it's a cellular modem, not a phone. A generated IMEI from a known phone model is required for activation.

### Current IMEI

| Field | Value |
|-------|-------|
| Original | `862636051970786` (SIM7600G-H modem) |
| Active | `352741384997469` (Samsung Galaxy A10e SM-A102U) |
| Set date | 2026-03-28 |
| Checks passed | Model, Lost Device, Verizon, T-Mobile, Blacklist |

### Generate a New IMEI

The IMEI generator script is at `base-station/imei-generator/generate.py`. It uses the imei.info API to generate and verify IMEIs against carrier databases.

```bash
cd ~/rtk-surveying/base-station
source <(grep '^IMEI_' rtkbase.conf | sed 's/^/export /')
python3 imei-generator/generate.py --quiet
```

### Set IMEI on Modem

```bash
sudo systemctl stop ModemManager
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyUSB2', 115200, timeout=3)
s.write(b'AT+SIMEI=<NEW_IMEI>\r\n')
time.sleep(2)
print(s.read(s.in_waiting).decode())
s.write(b'AT+CFUN=1,1\r\n')  # reboot modem
time.sleep(2)
print(s.read(s.in_waiting).decode())
s.close()
"
# Wait ~15 seconds for modem to reboot, then:
sudo systemctl start ModemManager
sudo mmcli -m 0 | grep imei  # verify
```

The IMEI persists across power cycles (stored in modem NV memory). Save the original and new IMEI in `rtkbase.conf`.

## RTKBase — GNSS Base Station Software

RTKBase provides a web UI for managing the F9P receiver, streaming RTCM corrections, and logging raw GNSS data.

### Install

```bash
cd ~
wget https://raw.githubusercontent.com/Stefal/rtkbase/master/tools/install.sh -O install.sh
chmod +x install.sh
sudo ./install.sh --user=jaredirby --all release
```

The `--user` flag is required when running over non-interactive SSH (e.g., Tailscale SSH). The install takes ~5 minutes and handles dependencies, RTKLIB compilation, Python venv, systemd services, gpsd/chrony, and F9P detection/configuration.

### Enable Antenna Voltage (required)

The RTKBase install script does **not** enable active antenna voltage on the F9P. Without this, the tripleband antenna gets no power and the receiver sees zero satellites.

```bash
sudo systemctl stop str2str_tcp
python3 -c "
import serial, struct, time
s = serial.Serial('/dev/ttyGNSS', 115200, timeout=2)
def ubx_msg(cls, mid, payload=b''):
    msg = bytearray([0xB5, 0x62, cls, mid]) + struct.pack('<H', len(payload)) + payload
    ck_a = ck_b = 0
    for b in msg[2:]:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes(msg) + bytes([ck_a, ck_b])
# Enable antenna voltage control + short/open detection, save to RAM+BBR+Flash
valset = struct.pack('<BBH', 0, 0x07, 0)
valset += struct.pack('<I', 0x10A3002E) + bytes([1])  # ANT_CFG_VOLTCTRL
valset += struct.pack('<I', 0x10A3002F) + bytes([1])  # ANT_CFG_SHORTDET
valset += struct.pack('<I', 0x10A30030) + bytes([1])  # ANT_CFG_OPENDET
s.write(ubx_msg(0x06, 0x8A, valset))
time.sleep(1)
print(s.read(s.in_waiting))
s.close()
"
sudo systemctl start str2str_tcp
```

This persists across power cycles (saved to F9P flash). Only needs to be run once.

**Warning:** If you ever re-run `./install.sh --configure-gnss`, it factory-resets the F9P and you must re-run this antenna voltage command.

### Services

| Service | Purpose | Enabled |
|---------|---------|---------|
| `rtkbase_web` | Web UI on port 80 | yes (boot) |
| `str2str_tcp` | F9P serial → TCP :5015 relay | yes (boot) |
| `gpsd` | GNSS time source | yes (boot) |
| `chrony` | NTP sync from GNSS | yes (boot) |
| `rtkbase_archive.timer` | Daily RINEX compression + cleanup | yes (boot) |
| `str2str_ntrip_A` | NTRIP caster output (Emlid Caster) | not yet configured |
| `str2str_local_ntrip_caster` | Local NTRIP caster on Pi | not yet configured |

### Field Workflow

1. Set up tripod over a known point (PK nail, monument, benchmark)
2. Power on — Pi boots, F9P locks satellites, RTKBase starts automatically
3. Open web UI → Settings → enter known coordinates in the **Position** field
4. Enable NTRIP output → Emlid Reach RX connects and receives corrections

For a new site without known coordinates:
1. Let the base log raw data for 2-6 hours
2. Download RINEX from the RTKBase logs page
3. Submit to OPUS (opus.ngs.noaa.gov) → ~2cm absolute coordinates
4. Drive a PK nail, record the OPUS coords — now it's a known point

### Configuration

Settings file: `~/rtkbase/settings.conf`

Key fields to configure:
- `position` — base coordinates (lat lon height)
- `svr_addr_a` / `svr_port_a` / `svr_pwd_a` / `mnt_name_a` — Emlid Caster NTRIP credentials
- `local_ntripc_port` / `local_ntripc_mnt_name` / `local_ntripc_pwd` — local NTRIP caster

Data directory: `~/rtkbase/data/` (raw GNSS logs, auto-archived daily)

## Network Interfaces

| Interface | Status | Notes |
|-----------|--------|-------|
| wlan0 | Active | Internal WiFi, fallback (metric 600) |
| wlan1 | Active | Alfa USB WiFi, preferred (metric 50) |
| eth0 | Up, no link | Ethernet available but not connected |
| tailscale0 | Active | Tailscale VPN tunnel |
| cdc-wdm0 | Available | SIM7600 cellular data (not yet configured) |

## File Layout

```
base-station/
  rtkbase.conf              # All config: modem, IMEI API, WiFi networks
  setup-wifi.sh             # Configure WiFi from rtkbase.conf
  call-test.py              # Voice call test — plays Twinkle Twinkle over SIM7600
  install.md                # This file
  RTK-Base-Station-Final-Shopping-List.md
  imei-generator/
    generate.py             # IMEI generator (from sim7600-dongle-gateway)
    tac-models.json         # TAC database for US budget phones
```

On the Pi:
```
~/rtkbase/                  # RTKBase installation (managed by RTKBase, not this repo)
  settings.conf             # RTKBase settings (base position, NTRIP, services)
  data/                     # Raw GNSS logs (auto-archived daily)
  logs/                     # str2str and service logs
  venv/                     # Python virtual environment
```
