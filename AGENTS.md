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

## Skills

Project-level skills are in `.pi/skills/` (with symlinks in `.agents/skills/`):

- **survey365-gnss** — GNSS/RTK domain: UBX protocol, RTCM, NTRIP, RTKLIB, serial control, base station modes
- **survey365-pi-deploy** — Deployment workflow: SSH access, setup-pi.sh, update.sh, template placeholders
- **survey365-resilient-mode** — Read-only rootfs architecture: mount layout, helper scripts, maintenance patterns
- **survey365-ui** — Frontend: Alpine.js + HTMX + MapLibre + Pico.css, no build step, WebSocket live updates
- **survey365-db-migrations** — SQLite/SpatiaLite schema: migration patterns, guard conditions, core tables
- **survey365-systemd** — Service units: dependencies, RTKLIB child orchestration, journal inspection
