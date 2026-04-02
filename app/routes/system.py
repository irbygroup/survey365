"""
System management routes for update status and manual update requests.

All endpoints are admin-only.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..auth import require_admin

router = APIRouter(prefix="/api/system", tags=["system"])

REPO_DIR = Path(__file__).resolve().parents[2]
UPDATE_SERVICE = "survey365-update.service"
UPDATE_CHECK_TIMER = "survey365-update-check.timer"


async def _run_cmd(*args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(REPO_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "", "command timed out"
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


def _parse_dirty_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if not line.strip():
            continue
        paths.append(line[3:].strip() if len(line) >= 4 else line.strip())
    return paths


@router.get("/update-status")
async def get_update_status(_admin=Depends(require_admin)):
    current_rc, current_commit, current_err = await _run_cmd("git", "rev-parse", "HEAD")
    short_rc, current_short, short_err = await _run_cmd("git", "rev-parse", "--short", "HEAD")
    branch_rc, branch, branch_err = await _run_cmd("git", "rev-parse", "--abbrev-ref", "HEAD")
    dirty_rc, dirty_output, dirty_err = await _run_cmd(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
    )
    remote_rc, remote_output, remote_err = await _run_cmd(
        "git",
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        "main",
        timeout=15.0,
    )
    service_rc, service_state, _ = await _run_cmd("systemctl", "is-active", UPDATE_SERVICE)
    timer_rc, timer_state, _ = await _run_cmd("systemctl", "is-active", UPDATE_CHECK_TIMER)
    enabled_rc, timer_enabled, _ = await _run_cmd("systemctl", "is-enabled", UPDATE_CHECK_TIMER)

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
