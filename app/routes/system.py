"""
System management routes for update status and manual update requests.

All endpoints are admin-only.
"""

import asyncio
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..auth import require_admin

router = APIRouter(prefix="/api/system", tags=["system"])

REPO_DIR = Path(__file__).resolve().parents[2]
UPDATE_SERVICE = "survey365-update.service"
UPDATE_CHECK_TIMER = "survey365-update-check.timer"
COMMAND_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _run_cmd_sync(*args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=COMMAND_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"

    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


async def _run_cmd(*args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    return await asyncio.to_thread(_run_cmd_sync, *args, timeout=timeout)


def _parse_dirty_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if not line.strip():
            continue
        paths.append(line[3:].strip() if len(line) >= 4 else line.strip())
    return paths


@router.get("/update-status")
async def get_update_status(_admin=Depends(require_admin)):
    (
        (current_rc, current_commit, current_err),
        (short_rc, current_short, short_err),
        (branch_rc, branch, branch_err),
        (dirty_rc, dirty_output, dirty_err),
        (remote_rc, remote_output, remote_err),
        (service_rc, service_state, _),
        (timer_rc, timer_state, _),
        (enabled_rc, timer_enabled, _),
    ) = await asyncio.gather(
        _run_cmd("git", "rev-parse", "HEAD"),
        _run_cmd("git", "rev-parse", "--short", "HEAD"),
        _run_cmd("git", "rev-parse", "--abbrev-ref", "HEAD"),
        _run_cmd("git", "status", "--porcelain", "--untracked-files=no"),
        _run_cmd(
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            "main",
            timeout=15.0,
        ),
        _run_cmd("systemctl", "is-active", UPDATE_SERVICE),
        _run_cmd("systemctl", "is-active", UPDATE_CHECK_TIMER),
        _run_cmd("systemctl", "is-enabled", UPDATE_CHECK_TIMER),
    )

    if current_rc != 0 or short_rc != 0 or branch_rc != 0:
        raise HTTPException(
            status_code=500,
            detail=current_err or short_err or branch_err or "Unable to inspect git repository",
        )

    dirty_paths = _parse_dirty_paths(dirty_output if dirty_rc == 0 else "")
    remote_commit = ""
    if remote_rc == 0 and remote_output:
        remote_commit = remote_output.splitlines()[0].split()[0]

    return {
        "branch": branch,
        "current_commit": current_commit,
        "current_short": current_short,
        "remote_commit": remote_commit or None,
        "remote_short": remote_commit[:7] if remote_commit else None,
        "update_available": bool(remote_commit and remote_commit != current_commit),
        "repo_reachable": remote_rc == 0,
        "dirty": bool(dirty_paths),
        "dirty_paths": dirty_paths,
        "update_in_progress": service_state in {"activating", "active"},
        "service_state": service_state or ("unknown" if service_rc != 0 else "inactive"),
        "timer_state": timer_state or ("unknown" if timer_rc != 0 else "inactive"),
        "timer_enabled": timer_enabled == "enabled" if enabled_rc == 0 else False,
        "remote_error": remote_err if remote_rc != 0 else None,
        "dirty_error": dirty_err if dirty_rc != 0 else None,
    }


@router.post("/update", status_code=status.HTTP_202_ACCEPTED)
async def start_update(response: Response, _admin=Depends(require_admin)):
    status_payload = await get_update_status(_admin=True)

    if status_payload["update_in_progress"]:
        raise HTTPException(status_code=409, detail="Update already in progress")
    if status_payload["dirty"]:
        raise HTTPException(
            status_code=409,
            detail="Repository has tracked local changes; auto-update is blocked",
        )

    rc, _, err = await _run_cmd("sudo", "-n", "systemctl", "start", UPDATE_SERVICE, timeout=10.0)
    if rc != 0:
        raise HTTPException(
            status_code=500,
            detail=err or "Failed to start update service",
        )

    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "message": "Update service started"}
