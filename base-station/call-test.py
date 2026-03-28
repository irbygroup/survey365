#!/usr/bin/env python3
"""Call a phone number and play a generated melody over USB PCM audio.

Usage:
    sudo python3 call-test.py [NUMBER]

Default number: +12515334726
Requires: python3-serial, ModemManager stopped.
"""

import math
import serial
import struct
import sys
import time

AT_PORT = "/dev/ttyUSB2"
AUDIO_PORT = "/dev/ttyUSB4"
BAUD = 115200
SAMPLE_RATE = 16000
FRAME_MS = 40
PLAY_DURATION = 60  # seconds

DEFAULT_NUMBER = "+12515334726"

# Twinkle Twinkle Little Star — note frequencies and durations
# (frequency_hz, duration_beats)
MELODY = [
    # Twinkle twinkle little star
    (262, 1), (262, 1), (392, 1), (392, 1), (440, 1), (440, 1), (392, 2),
    # How I wonder what you are
    (349, 1), (349, 1), (330, 1), (330, 1), (294, 1), (294, 1), (262, 2),
    # Up above the world so high
    (392, 1), (392, 1), (349, 1), (349, 1), (330, 1), (330, 1), (294, 2),
    # Like a diamond in the sky
    (392, 1), (392, 1), (349, 1), (349, 1), (330, 1), (330, 1), (294, 2),
    # Twinkle twinkle little star
    (262, 1), (262, 1), (392, 1), (392, 1), (440, 1), (440, 1), (392, 2),
    # How I wonder what you are
    (349, 1), (349, 1), (330, 1), (330, 1), (294, 1), (294, 1), (262, 2),
]

BEAT_DURATION = 0.35  # seconds per beat


def generate_tone(freq, duration_s, volume=0.6):
    """Generate a sine wave tone as signed 16-bit PCM samples."""
    n_samples = int(SAMPLE_RATE * duration_s)
    samples = []
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        # Fade in/out to avoid clicks (10ms ramp)
        ramp_samples = int(SAMPLE_RATE * 0.01)
        envelope = 1.0
        if i < ramp_samples:
            envelope = i / ramp_samples
        elif i > n_samples - ramp_samples:
            envelope = (n_samples - i) / ramp_samples
        val = volume * envelope * math.sin(2.0 * math.pi * freq * t)
        samples.append(int(val * 32767))
    return struct.pack(f"<{len(samples)}h", *samples)


def generate_melody_pcm(duration_s):
    """Generate the melody looped to fill duration_s, returned as raw PCM bytes."""
    # Build one pass of the melody
    melody_pcm = b""
    for freq, beats in MELODY:
        note_dur = beats * BEAT_DURATION
        melody_pcm += generate_tone(freq, note_dur)
        # Small gap between notes
        gap_samples = int(SAMPLE_RATE * 0.03)
        melody_pcm += b"\x00\x00" * gap_samples

    # Loop to fill duration
    total_bytes = int(SAMPLE_RATE * 2 * duration_s)  # 16-bit = 2 bytes/sample
    loops = (total_bytes // len(melody_pcm)) + 1
    full_pcm = melody_pcm * loops
    return full_pcm[:total_bytes]


def at_cmd(ser, cmd, wait=1.0):
    """Send AT command and return response."""
    ser.reset_input_buffer()
    ser.write(f"{cmd}\r\n".encode())
    time.sleep(wait)
    resp = ser.read(ser.in_waiting).decode(errors="ignore")
    print(f"  > {cmd}")
    for line in resp.strip().splitlines():
        print(f"  < {line}")
    return resp


def wait_for_connect(ser, timeout=60):
    """Wait for voice call to connect (VOICE CALL: BEGIN or +CLCC shows active)."""
    print(f"Waiting up to {timeout}s for answer...")
    start = time.time()
    buf = ""
    while time.time() - start < timeout:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode(errors="ignore")
            if "VOICE CALL: BEGIN" in buf or "MO CONNECTED" in buf:
                print("  Call connected!")
                return True
            if "NO CARRIER" in buf or "NO ANSWER" in buf or "BUSY" in buf or "ERROR" in buf:
                print(f"  Call failed: {buf.strip()}")
                return False
        time.sleep(0.2)
    print("  Timeout waiting for answer")
    return False


def main():
    number = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NUMBER
    if not number.startswith("+"):
        number = "+1" + number

    print(f"Generating {PLAY_DURATION}s of Twinkle Twinkle Little Star...")
    pcm_data = generate_melody_pcm(PLAY_DURATION)
    print(f"  {len(pcm_data)} bytes of PCM audio ready")

    print(f"\nOpening AT port {AT_PORT}...")
    at_ser = serial.Serial(AT_PORT, BAUD, timeout=2)
    time.sleep(0.5)

    # Basic modem check
    resp = at_cmd(at_ser, "AT")
    if "OK" not in resp:
        print("ERROR: Modem not responding")
        return 1

    # Check registration
    resp = at_cmd(at_ser, "AT+CREG?")
    if ",1" not in resp and ",5" not in resp:
        print("WARNING: Modem may not be registered on network")

    # Dial
    print(f"\nDialing {number}...")
    at_cmd(at_ser, f"ATD{number};", wait=0.5)

    # Wait for answer
    if not wait_for_connect(at_ser, timeout=60):
        at_cmd(at_ser, "AT+CHUP")
        at_ser.close()
        return 1

    # Enable USB PCM audio
    time.sleep(0.5)
    at_cmd(at_ser, "AT+CPCMREG=1", wait=0.5)

    # Open audio port
    print(f"\nOpening audio port {AUDIO_PORT}...")
    audio_ser = serial.Serial(AUDIO_PORT, BAUD, timeout=0.5)
    time.sleep(0.2)

    # Stream audio
    frame_bytes = int(SAMPLE_RATE * 2 * FRAME_MS / 1000)  # bytes per frame
    total_frames = len(pcm_data) // frame_bytes
    print(f"Streaming {PLAY_DURATION}s of audio ({total_frames} frames)...")

    start = time.time()
    offset = 0
    frames_sent = 0
    try:
        while offset + frame_bytes <= len(pcm_data):
            frame_start = time.time()
            chunk = pcm_data[offset:offset + frame_bytes]
            audio_ser.write(chunk)
            offset += frame_bytes
            frames_sent += 1

            # Pace to real-time
            elapsed = time.time() - frame_start
            target = FRAME_MS / 1000.0
            if elapsed < target:
                time.sleep(target - elapsed)

            # Progress every 10 seconds
            total_elapsed = time.time() - start
            if frames_sent % (10000 // FRAME_MS) == 0:
                print(f"  {total_elapsed:.0f}s elapsed, {frames_sent} frames sent")

    except KeyboardInterrupt:
        print("\n  Interrupted by user")

    total_time = time.time() - start
    print(f"\nDone. Streamed {frames_sent} frames in {total_time:.1f}s")

    # Hang up
    print("Hanging up...")
    audio_ser.close()
    at_cmd(at_ser, "AT+CPCMREG=0", wait=0.5)
    at_cmd(at_ser, "AT+CHUP", wait=1)
    at_ser.close()
    print("Call ended.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
