"""Tests for serial_reader.py — frame detection and filtering."""

import struct

import pytest

from app.gnss.serial_reader import SerialReader, crc24q


def _ubx_checksum(data: bytes) -> bytes:
    ck_a = 0
    ck_b = 0
    for b in data:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes([ck_a, ck_b])


def _build_ubx(cls: int, msg_id: int, payload: bytes = b"") -> bytes:
    header = struct.pack("<BBH", cls, msg_id, len(payload))
    body = header + payload
    return b"\xb5\x62" + body + _ubx_checksum(body)


def _build_rtcm3(payload: bytes) -> bytes:
    length = len(payload)
    header = bytes([0xD3, (length >> 8) & 0x03, length & 0xFF])
    data = header + payload
    crc = crc24q(data)
    return data + bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])


class TestUBXFrameDetection:
    def test_valid_nav_pvt_extracted(self):
        """A valid NAV-PVT frame is extracted correctly."""
        payload = bytes(92)
        frame = _build_ubx(0x01, 0x07, payload)
        reader = SerialReader()
        buf = bytearray(frame)
        emitted = []
        reader._emit = lambda ft, d: emitted.append((ft, d))
        reader._extract_frames(buf)
        assert len(emitted) == 1
        assert emitted[0][0] == "ubx"
        assert emitted[0][1] == frame

    def test_invalid_checksum_rejected(self):
        """A frame with a bad checksum is rejected."""
        payload = bytes(10)
        frame = bytearray(_build_ubx(0x01, 0x07, payload))
        frame[-1] ^= 0xFF  # corrupt checksum
        reader = SerialReader()
        buf = bytearray(frame)
        emitted = []
        reader._emit = lambda ft, d: emitted.append((ft, d))
        reader._extract_frames(buf)
        assert len(emitted) == 0

    def test_filtered_ubx_not_emitted(self):
        """A UBX message that fails the message filter is skipped after validation."""
        # RXM-RAWX (0x02, 0x15) — not in the default filter
        payload = bytes(16)
        frame = _build_ubx(0x02, 0x15, payload)
        filtered_out = []

        def msg_filter(cls, msg_id):
            return cls == 0x01 and msg_id in {0x07, 0x35}

        reader = SerialReader(ubx_message_filter=msg_filter)
        buf = bytearray(frame)
        emitted = []
        reader._emit = lambda ft, d: emitted.append((ft, d))
        reader._extract_frames(buf)
        assert len(emitted) == 0
        assert len(buf) == 0  # frame consumed, not left in buffer

    def test_corrupt_length_does_not_skip_valid_data(self):
        """A corrupt header should not consume valid bytes after it."""
        # Corrupt: sync bytes + garbage class/id + huge payload length
        corrupt = bytearray(b"\xb5\x62\xff\xff\xff\x7f")  # length = 0x7fff
        valid = _build_ubx(0x01, 0x07, bytes(92))

        reader = SerialReader()
        buf = bytearray(corrupt + valid)
        emitted = []
        reader._emit = lambda ft, d: emitted.append((ft, d))
        reader._extract_frames(buf)
        # The corrupt header should be rejected (invalid checksum or too long),
        # and the valid frame should eventually be found
        assert any(e[0] == "ubx" for e in emitted)


class TestRTCM3FrameDetection:
    def test_valid_rtcm3_extracted(self):
        payload = bytes([0x00, 0x10, 0x20, 0x30])
        frame = _build_rtcm3(payload)
        reader = SerialReader()
        buf = bytearray(frame)
        emitted = []
        reader._emit = lambda ft, d: emitted.append((ft, d))
        reader._extract_frames(buf)
        assert len(emitted) == 1
        assert emitted[0][0] == "rtcm3"

    def test_invalid_crc_rejected(self):
        payload = bytes([0x00, 0x10])
        frame = bytearray(_build_rtcm3(payload))
        frame[-1] ^= 0xFF
        reader = SerialReader()
        buf = bytearray(frame)
        emitted = []
        reader._emit = lambda ft, d: emitted.append((ft, d))
        reader._extract_frames(buf)
        assert len(emitted) == 0


class TestRawChunkCallback:
    def test_raw_callback_sees_exact_bytes(self):
        """raw_chunk_callback must see exact serial bytes."""
        chunks = []
        reader = SerialReader(raw_chunk_callback=lambda d: chunks.append(d))
        # Simulate _read_thread calling the callback
        data = b"\xb5\x62\x01\x07" + bytes(92)
        reader.raw_chunk_callback(bytes(data))
        assert len(chunks) == 1
        assert chunks[0] == data
