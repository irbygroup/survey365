"""
Runtime configuration for RTKLIB-managed correction outputs.
"""

import json
from pathlib import Path

from ..runtime_paths import ensure_runtime_dir, get_logs_dir

ACTIVE_BASE_FILENAME = "active-base.json"


def get_rtklib_runtime_dir() -> Path:
    return ensure_runtime_dir("rtklib")


def get_active_base_path() -> Path:
    return get_rtklib_runtime_dir() / ACTIVE_BASE_FILENAME


def write_active_base_config(payload: dict) -> Path:
    runtime_dir = get_rtklib_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        **payload,
        "logs_dir": str(get_logs_dir()),
    }
    path = get_active_base_path()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def read_active_base_config() -> dict:
    return json.loads(get_active_base_path().read_text())


def clear_active_base_config() -> None:
    path = get_active_base_path()
    if path.exists():
        path.unlink()
