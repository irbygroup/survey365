"""
Wi-Fi network CRUD and apply routes.

The database is the source of truth. Applying settings writes managed
NetworkManager connections for both wlan0 and wlan1 via scripts/setup-wifi.sh.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import require_admin
from ..db import get_db

router = APIRouter(prefix="/api/wifi", tags=["wifi"])

REPO_DIR = Path(__file__).resolve().parents[2]
APPLY_SCRIPT = REPO_DIR / "scripts" / "setup-wifi.sh"


class WifiNetworkCreate(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=128)
    priority: int = Field(default=0, ge=-999, le=999)
    metric: int = Field(default=50, ge=1, le=5000)


class WifiNetworkUpdate(BaseModel):
    ssid: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=0, max_length=128)
    priority: int | None = Field(default=None, ge=-999, le=999)
    metric: int | None = Field(default=None, ge=1, le=5000)


async def _run_cmd(*args: str, timeout: float = 30.0) -> tuple[int, str, str]:
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


@router.get("")
async def list_wifi_networks(_admin=Depends(require_admin)):
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, ssid, priority, metric, psk, created_at, updated_at
            FROM wifi_networks
            ORDER BY priority DESC, id ASC
            """
        )
        rows = await cursor.fetchall()

    return {
        "networks": [
            {
                "id": row["id"],
                "ssid": row["ssid"],
                "priority": row["priority"],
                "metric": row["metric"],
                "password_set": bool(row["psk"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_wifi_network(network: WifiNetworkCreate, _admin=Depends(require_admin)):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM wifi_networks WHERE ssid = ?",
            (network.ssid.strip(),),
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="SSID already exists")

        await db.execute(
            """
            INSERT INTO wifi_networks (ssid, psk, priority, metric, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (network.ssid.strip(), network.password, network.priority, network.metric),
        )
        await db.commit()

    return {"ok": True}


@router.put("/{network_id}")
async def update_wifi_network(
    network_id: int,
    network: WifiNetworkUpdate,
    _admin=Depends(require_admin),
):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, ssid, psk, priority, metric FROM wifi_networks WHERE id = ?",
            (network_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Wi-Fi network not found")

        ssid = network.ssid.strip() if network.ssid is not None else row["ssid"]
        priority = network.priority if network.priority is not None else row["priority"]
        metric = network.metric if network.metric is not None else row["metric"]
        psk = row["psk"]
        if network.password is not None and network.password != "":
            psk = network.password

        cursor = await db.execute(
            "SELECT id FROM wifi_networks WHERE ssid = ? AND id != ?",
            (ssid, network_id),
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="SSID already exists")

        await db.execute(
            """
            UPDATE wifi_networks
            SET ssid = ?, psk = ?, priority = ?, metric = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (ssid, psk, priority, metric, network_id),
        )
        await db.commit()

    return {"ok": True}


@router.delete("/{network_id}")
async def delete_wifi_network(network_id: int, _admin=Depends(require_admin)):
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM wifi_networks WHERE id = ?",
            (network_id,),
        )
        await db.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Wi-Fi network not found")
    return {"ok": True}


@router.post("/apply", status_code=status.HTTP_202_ACCEPTED)
async def apply_wifi_networks(_admin=Depends(require_admin)):
    if not APPLY_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="Wi-Fi apply script not found")

    rc, stdout, stderr = await _run_cmd("sudo", "-n", str(APPLY_SCRIPT), timeout=60.0)
    if rc != 0:
        raise HTTPException(
            status_code=500,
            detail=stderr or stdout or "Failed to apply Wi-Fi configuration",
        )

    return {"ok": True, "output": stdout}
