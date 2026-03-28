# RTK Base Station Pi — Install Guide

## Hardware

| Field | Value |
|-------|-------|
| Board | Raspberry Pi 4 Model B Rev 1.5 |
| OS | Debian GNU/Linux 13 (trixie) aarch64 |
| Kernel | 6.12.47+rpt-rpi-v8 |
| Hostname | `rtkbase-pi` |
| Local IP | 192.168.1.162 (wlan0) |
| Tailscale IP | 100.68.19.26 |
| Tailscale hostname | `rtkbase-pi.alligator-perch.ts.net` |
| Tailscale tag | `tag:rtksurveying` |

## Initial Setup

### 1. Fan Configuration

```
sudo raspi-config
  → Performance Options
    → Fan
      → GPIO 14
      → OK
      → 60°C trigger temperature
      → Fan on
```

### 2. Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --authkey=<YOUR_AUTH_KEY> --hostname=rtkbase-pi
sudo tailscale set --ssh
```

Tailscale SSH is enabled — allows passwordless SSH from authorized devices without needing the local user password.

### 3. Enable Tailscale on Boot

```bash
sudo systemctl enable tailscaled
```

### 4. SSH Key Setup

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

### 5. Fix Locale

The Pi ships without `en_US.UTF-8` generated, causing warnings on SSH login from macOS.

```bash
sudo sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
sudo locale-gen
sudo update-locale LANG=en_US.UTF-8
```

### 6. OS Update

```bash
sudo apt update && sudo apt full-upgrade -y
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

## Network Interfaces

| Interface | Status | Notes |
|-----------|--------|-------|
| wlan0 | Active | 192.168.1.162, primary connectivity |
| eth0 | Up, no link | Ethernet available but not connected |
| wlan1 | Up, no link | Second WiFi adapter present but unused |
