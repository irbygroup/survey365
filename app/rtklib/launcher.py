"""
Build and exec RTKLIB str2str commands from the active runtime config.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .runtime import read_active_base_config

STR2STR_PATH = "/usr/local/bin/str2str"


def _require_enabled(config: dict, role: str) -> dict:
    outputs = config.get("outputs", {})
    if role not in outputs or not outputs[role].get("enabled"):
        raise SystemExit(f"role '{role}' is not enabled in active-base.json")
    return outputs[role]


def _position_tokens(config: dict) -> list[str]:
    position = config["position"]
    return [str(position["lat"]), str(position["lon"]), str(position["height"])]


def _receiver_descriptor(config: dict) -> str:
    return config.get("receiver_descriptor") or "RTKBase unknown,Survey365 unknown"


def _antenna_descriptor(config: dict) -> str:
    return config.get("antenna_descriptor") or "ADVNULLANTENNA"


def _build_source_table(config: dict, output: dict) -> str:
    position = config["position"]
    auth_mode = "N"
    if output.get("username") or output.get("password"):
        auth_mode = "B"
    message_set = output["messages"]
    receiver_frequency_count = output.get("receiver_frequency_count", "2")
    receiver_label = output.get("receiver_label") or "RTKBase_unknown,Survey365"
    return (
        f"{output['mountpoint']};rtcm3;{message_set};{receiver_frequency_count};"
        f"GPS+GLO+GAL+BDS+QZS;NONE;NONE;{position['lat']};{position['lon']};0;0;"
        f"{receiver_label};NONE;{auth_mode};N;;"
    )


def build_command(role: str) -> list[str]:
    config = read_active_base_config()
    output = _require_enabled(config, role)
    Path(config["logs_dir"]).mkdir(parents=True, exist_ok=True)

    args = [
        STR2STR_PATH,
        "-in",
        f"tcpcli://127.0.0.1:{config['raw_relay_port']}#ubx",
    ]

    if role == "local_caster":
        source_table = _build_source_table(config, output)
        out = (
            "ntripc://"
            f"{output.get('username', '')}:{output.get('password', '')}@:"
            f"{output['internal_port']}/{output['mountpoint']}:{source_table}#rtcm3"
        )
        log_name = "str2str_local_caster.log"
    elif role == "outbound":
        out = (
            "ntrips://"
            f":{output['password']}@{output['host']}:{output['port']}/{output['mountpoint']}#rtcm3"
        )
        log_name = "str2str_outbound.log"
    elif role == "log":
        Path(output["data_dir"]).mkdir(parents=True, exist_ok=True)
        out = (
            f"file://{output['data_dir']}/%Y-%m-%d_%h-%M-%S_GNSS-1.ubx::T::S={output['rotate_hours']}"
        )
        log_name = "str2str_file.log"
    else:
        raise SystemExit(f"unsupported role: {role}")

    if role != "log":
        args.extend(["-msg", output["messages"]])
    args.extend(["-out", out])
    if role != "log":
        args.extend(["-p", *_position_tokens(config)])
        args.extend(["-i", _receiver_descriptor(config)])
        args.extend(["-a", _antenna_descriptor(config)])
    args.extend(["-t", str(config.get("trace_level", 0))])
    args.extend(["-fl", str(Path(config["logs_dir"]) / log_name)])
    return args


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.rtklib.launcher <local_caster|outbound|log>")

    role = sys.argv[1]
    argv = build_command(role)
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
