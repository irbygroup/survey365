"""
Helpers for locating writable runtime paths.
"""

import os
from pathlib import Path


def get_db_path() -> Path:
    db_env = os.environ.get("SURVEY365_DB")
    if db_env:
        return Path(db_env).resolve()
    return (Path(__file__).resolve().parent.parent / "data" / "survey365.db").resolve()


def get_data_dir() -> Path:
    return get_db_path().parent


def get_logs_dir() -> Path:
    return get_data_dir() / "logs"


def ensure_runtime_dir(name: str) -> Path:
    path = get_data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path
