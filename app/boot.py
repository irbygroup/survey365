#!/usr/bin/env python3
"""
Survey365 Boot Tasks

Runs at boot as a oneshot systemd service (survey365-boot.service).
Checks that the GNSS hardware is present before Survey365 starts.

Antenna voltage configuration is now handled by GNSSManager on startup,
so this boot service only needs to verify hardware readiness.
"""

import os
import sys


# ── Logging (goes to systemd journal via stdout/stderr) ──────────────────

def log_info(msg: str) -> None:
    print(f"[survey365-boot] {msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"[survey365-boot] WARN: {msg}", flush=True)


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    log_info("Survey365 boot tasks starting...")

    gnss_port = os.environ.get("GNSS_PORT", "/dev/ttyGNSS")

    if os.path.exists(gnss_port):
        log_info(f"F9P detected at {gnss_port}")
    else:
        log_warn(f"F9P not found at {gnss_port} -- Survey365 will retry on startup")

    log_info("Boot tasks complete (antenna voltage handled by Survey365 on startup)")


if __name__ == "__main__":
    main()
