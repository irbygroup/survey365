"""Tests for rtklib/launcher.py — argv generation for each role."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from app.rtklib.launcher import build_command


SAMPLE_CONFIG = {
    "survey365_version": "1.1.0",
    "active_mode": "base",
    "rtcm_engine": "rtklib",
    "raw_relay_port": 5015,
    "external_local_caster_port": 2101,
    "trace_level": 0,
    "position": {"lat": 30.6945, "lon": -88.0432, "height": 12.345},
    "receiver_descriptor": "RTKBase ZED-F9P,1.1.0 HPG 1.32",
    "antenna_descriptor": "ADVNULLANTENNA",
    "logs_dir": "/tmp/test_logs",
    "outputs": {
        "local_caster": {
            "enabled": True,
            "mountpoint": "TEST",
            "messages": "1004,1005(10),1077",
            "internal_port": 2110,
            "receiver_frequency_count": "2",
            "receiver_label": "RTKBase_ZED-F9P,1.1.0_HPG_1.32",
            "username": "",
            "password": "",
        },
        "outbound": {
            "enabled": True,
            "host": "caster.example.com",
            "port": 2101,
            "mountpoint": "PUSH1",
            "password": "secret",
            "messages": "1004,1077",
        },
        "log": {
            "enabled": True,
            "data_dir": "/tmp/test_rinex",
            "rotate_hours": 24,
        },
    },
}


def _mock_read():
    return SAMPLE_CONFIG.copy()


@patch("app.rtklib.launcher.read_active_base_config", side_effect=_mock_read)
def test_local_caster_argv(mock_read, tmp_path):
    with patch("app.rtklib.launcher.Path") as MockPath:
        MockPath.return_value.mkdir = lambda **kw: None
        MockPath.side_effect = Path
        argv = build_command("local_caster")

    assert argv[0] == "/usr/local/bin/str2str"
    assert "-in" in argv
    in_idx = argv.index("-in")
    assert "tcpcli://127.0.0.1:5015#ubx" in argv[in_idx + 1]
    assert "-out" in argv
    out_idx = argv.index("-out")
    assert "ntripc://" in argv[out_idx + 1]
    assert "2110" in argv[out_idx + 1]
    assert "-msg" in argv
    assert "-p" in argv
    assert "-i" in argv
    assert "-a" in argv


@patch("app.rtklib.launcher.read_active_base_config", side_effect=_mock_read)
def test_outbound_argv(mock_read):
    with patch("app.rtklib.launcher.Path") as MockPath:
        MockPath.return_value.mkdir = lambda **kw: None
        MockPath.side_effect = Path
        argv = build_command("outbound")

    assert "ntrips://" in argv[argv.index("-out") + 1]
    assert "caster.example.com" in argv[argv.index("-out") + 1]
    assert "PUSH1" in argv[argv.index("-out") + 1]


@patch("app.rtklib.launcher.read_active_base_config", side_effect=_mock_read)
def test_log_argv(mock_read):
    with patch("app.rtklib.launcher.Path") as MockPath:
        MockPath.return_value.mkdir = lambda **kw: None
        MockPath.side_effect = Path
        argv = build_command("log")

    assert "file://" in argv[argv.index("-out") + 1]
    assert "-msg" not in argv  # log role has no -msg


@patch("app.rtklib.launcher.read_active_base_config", side_effect=_mock_read)
def test_disabled_role_raises(mock_read):
    cfg = SAMPLE_CONFIG.copy()
    cfg["outputs"] = {**cfg["outputs"], "local_caster": {**cfg["outputs"]["local_caster"], "enabled": False}}
    mock_read.side_effect = lambda: cfg
    with pytest.raises(SystemExit):
        build_command("local_caster")
