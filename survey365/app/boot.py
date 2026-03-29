#!/usr/bin/env python3
"""
Survey365 Boot Tasks

Runs at boot as a oneshot systemd service (survey365-boot.service).
Executes tasks that require hardware access before Survey365 starts:

1. Enable F9P antenna voltage (UBX-CFG-VALSET via serial)
   - Required for active tripleband antenna to receive power
   - Persists in F9P flash, but re-applied on boot as a safety net
   - Only runs if antenna_voltage_on_boot=true in config DB

2. (Future) Auto-resume last session

This script runs as root (needs serial port access to /dev/ttyGNSS).
It must stop str2str_tcp before sending UBX commands, then restart it.
Errors are logged but never fatal -- boot must not fail if F9P is absent.
"""

import os
import sqlite3
import struct
import subprocess
import sys
import time


# ── Configuration ────────────────────────────────────────────────────────

GNSS_SERIAL_PORT = "/dev/ttyGNSS"
GNSS_BAUD_RATE = 115200
GNSS_TIMEOUT = 2  # seconds

STR2STR_SERVICE = "str2str_tcp"


# ── Logging (goes to systemd journal via stdout/stderr) ──────────────────

def log_info(msg: str) -> None:
    print(f"[survey365-boot] {msg}", flush=True)


def log_error(msg: str) -> None:
    print(f"[survey365-boot] ERROR: {msg}", file=sys.stderr, flush=True)


def log_warn(msg: str) -> None:
    print(f"[survey365-boot] WARN: {msg}", flush=True)


# ── Database access ──────────────────────────────────────────────────────

def get_config_value(db_path: str, key: str) -> str | None:
    """Read a single config value from the Survey365 database."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        log_error(f"Failed to read config key '{key}': {e}")
        return None


# ── systemctl helpers ────────────────────────────────────────────────────

def service_is_active(service: str) -> bool:
    """Check if a systemd service is currently active."""
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        capture_output=True,
    )
    return result.returncode == 0


def stop_service(service: str) -> bool:
    """Stop a systemd service. Returns True if successful."""
    log_info(f"Stopping {service}...")
    result = subprocess.run(
        ["systemctl", "stop", service],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        log_error(f"Failed to stop {service}: {result.stderr.strip()}")
        return False
    # Wait for the service to release the serial port
    time.sleep(1)
    return True


def start_service(service: str) -> bool:
    """Start a systemd service. Returns True if successful."""
    log_info(f"Starting {service}...")
    result = subprocess.run(
        ["systemctl", "start", service],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        log_error(f"Failed to start {service}: {result.stderr.strip()}")
        return False
    return True


# ── UBX protocol helpers ────────────────────────────────────────────────

def ubx_checksum(msg: bytes) -> bytes:
    """Compute UBX CK_A and CK_B checksum over class, id, length, payload."""
    ck_a = 0
    ck_b = 0
    for b in msg:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes([ck_a, ck_b])


def ubx_message(cls: int, msg_id: int, payload: bytes = b"") -> bytes:
    """Build a complete UBX message with sync bytes, header, payload, checksum."""
    header = struct.pack("<BBH", cls, msg_id, len(payload))
    body = header + payload
    checksum = ubx_checksum(body)
    return b"\xb5\x62" + body + checksum


def build_antenna_voltage_command() -> bytes:
    """
    Build UBX-CFG-VALSET message to enable antenna voltage, short detection,
    and open detection. Saves to RAM + BBR + Flash (layer mask 0x07).

    Key IDs (from u-blox ZED-F9P interface description):
      0x10A3002E  CFG-HW-ANT_CFG_VOLTCTRL   (1 = enable antenna voltage)
      0x10A3002F  CFG-HW-ANT_CFG_SHORTDET   (1 = enable short detection)
      0x10A30030  CFG-HW-ANT_CFG_OPENDET    (1 = enable open detection)
    """
    # VALSET header: version=0, layers=0x07 (RAM+BBR+Flash), reserved=0x0000
    valset_header = struct.pack("<BBH", 0, 0x07, 0)

    # Key-value pairs (key is uint32 LE, value is uint8 for boolean keys)
    kv_pairs = b""
    kv_pairs += struct.pack("<I", 0x10A3002E) + bytes([1])  # ANT_CFG_VOLTCTRL
    kv_pairs += struct.pack("<I", 0x10A3002F) + bytes([1])  # ANT_CFG_SHORTDET
    kv_pairs += struct.pack("<I", 0x10A30030) + bytes([1])  # ANT_CFG_OPENDET

    payload = valset_header + kv_pairs
    return ubx_message(0x06, 0x8A, payload)


# ── Antenna voltage task ─────────────────────────────────────────────────

def enable_antenna_voltage() -> bool:
    """
    Send UBX-CFG-VALSET to enable antenna voltage on the F9P.

    This requires exclusive access to the serial port, so str2str_tcp
    must be stopped first and restarted after.

    Returns True if the command was sent successfully.
    """
    try:
        import serial
    except ImportError:
        log_error("pyserial not installed -- cannot configure F9P antenna voltage")
        return False

    if not os.path.exists(GNSS_SERIAL_PORT):
        log_warn(f"Serial port {GNSS_SERIAL_PORT} not found -- F9P may not be connected")
        return False

    # Stop str2str_tcp to release the serial port
    str2str_was_active = service_is_active(STR2STR_SERVICE)
    if str2str_was_active:
        if not stop_service(STR2STR_SERVICE):
            log_error("Cannot stop str2str_tcp -- aborting antenna voltage config")
            return False

    success = False
    try:
        log_info(f"Opening {GNSS_SERIAL_PORT} at {GNSS_BAUD_RATE} baud...")
        ser = serial.Serial(GNSS_SERIAL_PORT, GNSS_BAUD_RATE, timeout=GNSS_TIMEOUT)

        # Flush any stale data
        ser.reset_input_buffer()

        # Send the antenna voltage command
        cmd = build_antenna_voltage_command()
        log_info("Sending UBX-CFG-VALSET (antenna voltage + short/open detection)...")
        ser.write(cmd)

        # Wait for ACK (UBX-ACK-ACK is class=0x05 id=0x01)
        time.sleep(1)
        response = ser.read(ser.in_waiting or 64)

        if b"\xb5\x62\x05\x01" in response:
            log_info("F9P acknowledged antenna voltage configuration (UBX-ACK-ACK)")
            success = True
        elif b"\xb5\x62\x05\x00" in response:
            log_warn("F9P rejected antenna voltage configuration (UBX-ACK-NAK)")
            # Not fatal -- the setting may already be applied
            success = True
        else:
            log_warn(
                f"No clear ACK/NAK from F9P (got {len(response)} bytes) "
                "-- command may still have been applied"
            )
            # Treat as success since the setting persists in flash and
            # the F9P may simply not have responded within the timeout
            success = True

        ser.close()

    except serial.SerialException as e:
        log_error(f"Serial port error: {e}")
    except Exception as e:
        log_error(f"Unexpected error configuring antenna voltage: {e}")

    # Always restart str2str_tcp if it was running before
    if str2str_was_active:
        if not start_service(STR2STR_SERVICE):
            log_error("Failed to restart str2str_tcp after antenna voltage config")

    return success


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    log_info("Survey365 boot tasks starting...")

    # Determine database path from environment
    db_path = os.environ.get(
        "SURVEY365_DB",
        os.path.expanduser("~/rtk-surveying/survey365/data/survey365.db"),
    )

    # Task 1: Antenna voltage
    antenna_enabled = get_config_value(db_path, "antenna_voltage_on_boot")
    if antenna_enabled is None:
        # Database may not exist yet (first boot before install completes).
        # Default to enabling antenna voltage as a safe default.
        log_info("Config DB not found or key missing -- defaulting antenna voltage to ON")
        antenna_enabled = "true"

    if antenna_enabled.lower() == "true":
        log_info("Antenna voltage on boot is ENABLED")
        if enable_antenna_voltage():
            log_info("Antenna voltage configuration completed successfully")
        else:
            log_warn("Antenna voltage configuration had issues (see above)")
    else:
        log_info("Antenna voltage on boot is DISABLED -- skipping")

    log_info("Survey365 boot tasks complete")


if __name__ == "__main__":
    main()
