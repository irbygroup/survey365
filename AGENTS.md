# Agents

Pointers for AI coding agents working in this repository.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — Project overview, Pi access, deploy instructions, managed infrastructure
- [`survey365/CLAUDE.md`](survey365/CLAUDE.md) — App architecture, API routes, tech stack, GNSS architecture

## Key Infrastructure Files

- [`survey365/install.sh`](survey365/install.sh) — First-time Pi installer (systemd, nginx, udev, sudoers)
- [`survey365/scripts/update.sh`](survey365/scripts/update.sh) — Safe auto-update (git pull, pip, infra deploy, service restart)
- [`survey365/nginx/survey365.conf`](survey365/nginx/survey365.conf) — Nginx reverse proxy template
- [`survey365/systemd/`](survey365/systemd/) — Systemd unit templates (`{user}`/`{home}` placeholders)
