# Agents

Pointers for AI coding agents working in this repository.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — Project overview, Pi access, deploy instructions, resilient-mode operations, Wi-Fi/device config, and Survey365 architecture

## Key Infrastructure Files

- [`scripts/setup-pi.sh`](scripts/setup-pi.sh) — Pi installer and resilient-mode enabler (systemd, nginx, udev, sudoers, legacy config import)
- [`scripts/update.sh`](scripts/update.sh) — Manual update flow (git fast-forward, optional OS upgrade, redeploy)
- [`scripts/setup-wifi.sh`](scripts/setup-wifi.sh) — Applies database-backed Wi-Fi profiles to NetworkManager
- [`nginx/survey365.conf`](nginx/survey365.conf) — Nginx reverse proxy template
- [`systemd/`](systemd/) — Systemd unit templates (`{user}`, `{home}`, `{repo_dir}`, `{data_dir}` placeholders)
