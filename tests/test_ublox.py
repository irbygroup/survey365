"""Tests for ublox.py — MON-VER parsing and descriptor generation."""

import pytest

from app.gnss.ublox import parse_mon_ver, parse_nav_pvt, parse_nav_sat


class TestMonVerParsing:
    def test_parse_basic_mon_ver(self):
        """Parse a MON-VER response with model in extensions."""
        sw = b"HPG 1.32\x00" + b"\x00" * 21  # 30 bytes
        hw = b"00190000\x00\x00"               # 10 bytes
        ext1 = b"FWVER=HPG 1.32\x00" + b"\x00" * 15   # 30 bytes
        ext2 = b"PROTVER=27.31\x00" + b"\x00" * 16     # 30 bytes
        ext3 = b"MOD=ZED-F9P\x00" + b"\x00" * 18       # 30 bytes

        payload = sw + hw + ext1 + ext2 + ext3
        result = parse_mon_ver(payload)

        assert result is not None
        assert result["sw_version"] == "HPG 1.32"
        assert result["hw_version"] == "00190000"
        assert result["model"] == "ZED-F9P"
        assert len(result["extensions"]) == 3

    def test_parse_short_payload_returns_none(self):
        assert parse_mon_ver(b"short") is None

    def test_parse_no_model_extension(self):
        """If no MOD= extension, model should be None."""
        sw = b"HPG 1.32\x00" + b"\x00" * 21
        hw = b"00190000\x00\x00"
        payload = sw + hw
        result = parse_mon_ver(payload)
        assert result is not None
        assert result["model"] is None
        assert result["sw_version"] == "HPG 1.32"


class TestNavPvtParsing:
    def test_parse_valid_pvt(self):
        payload = bytearray(92)
        # Set fix_type = 3 (3D fix)
        payload[20] = 3
        # Set numSV = 12
        payload[23] = 12
        result = parse_nav_pvt(bytes(payload))
        assert result is not None
        assert result["fix_type"] == 3
        assert result["num_sv"] == 12

    def test_short_payload(self):
        assert parse_nav_pvt(bytes(10)) is None


class TestNavSatParsing:
    def test_parse_single_satellite(self):
        # Header: 8 bytes (iTOW + version + numSvs + reserved)
        header = bytearray(8)
        header[5] = 1  # numSvs = 1
        # Satellite block: 12 bytes
        sat = bytearray(12)
        sat[0] = 0   # gnssId = GPS
        sat[1] = 5   # svId = 5
        sat[2] = 40  # cno = 40
        sat[8] = 0x08  # flags: used bit set
        payload = bytes(header + sat)
        result = parse_nav_sat(payload)
        assert len(result) == 1
        assert result[0]["constellation"] == "GPS"
        assert result[0]["svid"] == 5
        assert result[0]["cn0"] == 40.0
        assert result[0]["used"] is True
