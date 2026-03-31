"""
Operating mode routes: Known Point Base, Relative Base, Stop, Resume.

Mode transitions are serialized via an asyncio.Lock to prevent concurrent changes.
Mode state is held in memory (Python dict) and persisted to the sessions table.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_active_project_id, get_db
from ..gnss import gnss_manager, gnss_state
from ..gnss.base_station import start_base, stop_base
from ..gnss.ntrip_client import NTRIPClient
from ..ws.live import broadcast_event

logger = logging.getLogger("survey365.mode")

router = APIRouter(prefix="/api/mode", tags=["mode"])

# Serialized mode transitions
_mode_lock = asyncio.Lock()

# In-memory mode state
_current_mode: str = "idle"
_current_site: dict | None = None
_current_session_id: int | None = None
_session_started_at: str | None = None
_establishing: bool = False
_establish_progress: dict | None = None
_establish_task: asyncio.Task | None = None
_cors_ntrip_client: NTRIPClient | None = None


def get_mode_state() -> dict:
    """Return the current mode state dict (read by status route and WebSocket)."""
    mode_label = "IDLE"
    if _current_mode == "known_base" and _current_site:
        mode_label = f"Broadcasting from {_current_site['name']}"
    elif _current_mode == "relative_base" and not _establishing:
        mode_label = "Broadcasting (Relative)"
    elif _current_mode == "cors_establish" and _establishing:
        phase = (_establish_progress or {}).get("phase", "connecting")
        if phase == "waiting_fix":
            mode_label = "Waiting for RTK fix..."
        elif phase == "averaging":
            mode_label = "RTK Fixed — Averaging..."
        else:
            mode_label = "Connecting to CORS..."
    elif _establishing:
        mode_label = "Establishing position..."

    return {
        "mode": _current_mode,
        "mode_label": mode_label,
        "site": _current_site,
        "session_id": _current_session_id,
        "started_at": _session_started_at,
        "establishing": _establishing,
        "establish_progress": _establish_progress,
        "session": {
            "id": _current_session_id,
            "started_at": _session_started_at,
            "mode": _current_mode,
        }
        if _current_session_id
        else None,
    }


class KnownBaseRequest(BaseModel):
    site_id: int


class RelativeBaseRequest(BaseModel):
    duration_seconds: int = Field(default=120, ge=10, le=600)


class CORSEstablishRequest(BaseModel):
    profile_id: int
    averaging_seconds: int = Field(default=60, ge=10, le=600)
    rtk_timeout_seconds: int = Field(default=120, ge=30, le=600)
    min_accuracy: float = Field(default=0.05, ge=0.005, le=1.0)


@router.get("")
async def get_mode():
    """Return current operating mode and state."""
    return get_mode_state()


@router.post("/known-base")
async def start_known_base(req: KnownBaseRequest):
    """Start Known Point Base mode.

    1. Look up site by ID
    2. Configure F9P as base station at site coordinates
    3. Start RTCM outputs (RINEX, local caster)
    4. Create session record
    5. Broadcast mode change via WebSocket
    """
    global _current_mode, _current_site, _current_session_id, _session_started_at
    global _establishing, _establish_progress

    if _mode_lock.locked():
        raise HTTPException(status_code=409, detail="Mode change in progress")

    async with _mode_lock:
        # Look up site
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT id, name, lat, lon, height FROM sites WHERE id = ?",
                (req.site_id,),
            )
            site = await cursor.fetchone()

        if site is None:
            raise HTTPException(status_code=404, detail="Site not found")

        site_dict = {
            "id": site["id"],
            "name": site["name"],
            "lat": site["lat"],
            "lon": site["lon"],
            "height": site["height"],
        }

        # End previous session if active
        await _end_current_session()

        # Configure GNSS receiver and start RTCM outputs
        height = site["height"] if site["height"] is not None else 0.0
        await start_base(gnss_manager, site["lat"], site["lon"], height)

        # Create session record
        active_pid = await get_active_project_id()
        async with get_db() as db:
            cursor = await db.execute(
                """
                INSERT INTO sessions (mode, site_id, started_at, project_id)
                VALUES ('known_base', ?, datetime('now'), ?)
                """,
                (req.site_id, active_pid),
            )
            session_id = cursor.lastrowid

            # Update site last_used
            await db.execute(
                "UPDATE sites SET last_used = datetime('now') WHERE id = ?",
                (req.site_id,),
            )
            await db.commit()

        # Update in-memory state
        _current_mode = "known_base"
        _current_site = site_dict
        _current_session_id = session_id
        _session_started_at = datetime.now(timezone.utc).isoformat()
        _establishing = False
        _establish_progress = None

        # Broadcast mode change
        await broadcast_event({
            "type": "mode_change",
            "mode": "known_base",
            "site": site_dict,
            "session_id": session_id,
        })

        logger.info("Started known_base mode at site '%s' (id=%d)", site["name"], req.site_id)

        return {"ok": True, "session_id": session_id}


@router.post("/relative-base")
async def start_relative_base(req: RelativeBaseRequest = RelativeBaseRequest()):
    """Start Relative Base mode.

    1. Begin averaging current GNSS position
    2. Set mode with establishing=True
    3. Background task collects positions for duration_seconds
    4. Compute mean position
    5. Configure F9P as base, start RTCM outputs
    6. Save averaged point as site
    """
    global _establishing, _establish_task

    if _mode_lock.locked():
        raise HTTPException(status_code=409, detail="Mode change in progress")

    # Check GNSS fix before starting
    position = await gnss_state.get_position()
    if position is None:
        raise HTTPException(status_code=400, detail="No GNSS fix available for averaging")

    # Cancel any existing establish task
    if _establish_task is not None and not _establish_task.done():
        _establish_task.cancel()

    _establish_task = asyncio.create_task(
        _run_relative_base(req.duration_seconds)
    )

    return {"ok": True, "message": f"Averaging position for {req.duration_seconds} seconds"}


async def _run_relative_base(duration: int):
    """Background task: average GNSS position, then start broadcasting."""
    global _current_mode, _current_site, _current_session_id, _session_started_at
    global _establishing, _establish_progress

    async with _mode_lock:
        # End previous session
        await _end_current_session()

        _current_mode = "relative_base"
        _current_site = None
        _current_session_id = None
        _session_started_at = None
        _establishing = True
        _establish_progress = {
            "elapsed_seconds": 0,
            "total_seconds": duration,
            "current_position": None,
            "samples": 0,
        }

        # Broadcast that we are establishing
        await broadcast_event({
            "type": "mode_change",
            "mode": "relative_base",
            "site": None,
            "session_id": None,
        })

        # Collect position samples
        lat_samples = []
        lon_samples = []
        height_samples = []
        start_time = time.time()

        for elapsed in range(duration):
            if not _establishing:
                # Cancelled
                return

            # Wait 1 second between samples
            await asyncio.sleep(1.0)

            position = await gnss_state.get_position()
            if position is not None:
                lat, lon, h = position
                lat_samples.append(lat)
                lon_samples.append(lon)
                height_samples.append(h)

            current_elapsed = int(time.time() - start_time)
            current_pos = None
            if lat_samples:
                current_pos = {
                    "lat": sum(lat_samples) / len(lat_samples),
                    "lon": sum(lon_samples) / len(lon_samples),
                    "height": sum(height_samples) / len(height_samples),
                }

            _establish_progress = {
                "elapsed_seconds": current_elapsed,
                "total_seconds": duration,
                "current_position": current_pos,
                "samples": len(lat_samples),
            }

            # Broadcast progress every second
            await broadcast_event({
                "type": "establish_progress",
                **_establish_progress,
            })

        # Averaging complete
        if not lat_samples:
            logger.error("No valid GNSS samples collected during averaging")
            _establishing = False
            _current_mode = "idle"
            _current_site = None
            _current_session_id = None
            _session_started_at = None
            _establish_progress = None
            await broadcast_event({
                "type": "mode_change",
                "mode": "idle",
                "site": None,
                "session_id": None,
            })
            return

        avg_lat = sum(lat_samples) / len(lat_samples)
        avg_lon = sum(lon_samples) / len(lon_samples)
        avg_height = sum(height_samples) / len(height_samples)

        # Save as a site with source='averaged'
        site_name = f"Relative Base {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        active_pid = await get_active_project_id()

        async with get_db() as db:
            cursor = await db.execute(
                """
                INSERT INTO sites (name, lat, lon, height, source, accuracy_h, accuracy_v,
                                   established, notes, project_id)
                VALUES (?, ?, ?, ?, 'averaged', NULL, NULL, datetime('now'), ?, ?)
                """,
                (
                    site_name,
                    avg_lat,
                    avg_lon,
                    avg_height,
                    f"Averaged from {len(lat_samples)} samples over {duration}s",
                    active_pid,
                ),
            )
            site_id = cursor.lastrowid

            # Try to set SpatiaLite geometry
            try:
                await db.execute(
                    "UPDATE sites SET geom = MakePoint(?, ?, 4326) WHERE id = ?",
                    (avg_lon, avg_lat, site_id),
                )
            except Exception:
                pass  # SpatiaLite not available

            await db.commit()

        site_dict = {
            "id": site_id,
            "name": site_name,
            "lat": avg_lat,
            "lon": avg_lon,
            "height": avg_height,
        }

        # Configure GNSS receiver and start RTCM outputs
        await start_base(gnss_manager, avg_lat, avg_lon, avg_height)

        # Create session record
        async with get_db() as db:
            cursor = await db.execute(
                """
                INSERT INTO sessions (mode, site_id, started_at, project_id)
                VALUES ('relative_base', ?, datetime('now'), ?)
                """,
                (site_id, active_pid),
            )
            session_id = cursor.lastrowid
            await db.commit()

        # Update in-memory state
        _current_site = site_dict
        _current_session_id = session_id
        _session_started_at = datetime.now(timezone.utc).isoformat()
        _establishing = False
        _establish_progress = None

        # Broadcast completion
        await broadcast_event({
            "type": "mode_change",
            "mode": "relative_base",
            "site": site_dict,
            "session_id": session_id,
        })

        logger.info(
            "Relative base established: lat=%.9f lon=%.9f height=%.4f (%d samples)",
            avg_lat, avg_lon, avg_height, len(lat_samples),
        )


@router.post("/cors-establish")
async def start_cors_establish(req: CORSEstablishRequest):
    """Start CORS Establish mode.

    1. Load NTRIP profile (must be type inbound_cors)
    2. Configure F9P as rover
    3. Connect to CORS NTRIP caster, receive corrections
    4. Wait for RTK fix (or timeout)
    5. Average RTK-fixed position
    6. Save as site, switch to Known Base mode
    """
    global _establishing, _establish_task

    if _mode_lock.locked():
        raise HTTPException(status_code=409, detail="Mode change in progress")

    # Load and validate NTRIP profile
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM ntrip_profiles WHERE id = ? AND type = 'inbound_cors'",
            (req.profile_id,),
        )
        profile = await cursor.fetchone()

    if profile is None:
        raise HTTPException(status_code=404, detail="CORS NTRIP profile not found")

    if not profile["host"] or not profile["mountpoint"]:
        raise HTTPException(status_code=400, detail="NTRIP profile missing host or mountpoint")

    # Check GNSS connection
    if not gnss_state.connected:
        raise HTTPException(status_code=400, detail="GNSS receiver not connected")

    # Cancel any existing establish task
    if _establish_task is not None and not _establish_task.done():
        _establish_task.cancel()

    profile_dict = {
        "id": profile["id"],
        "name": profile["name"],
        "host": profile["host"],
        "port": profile["port"] or 2101,
        "mountpoint": profile["mountpoint"],
        "username": profile["username"] or "",
        "password": profile["password"] or "",
    }

    _establish_task = asyncio.create_task(
        _run_cors_establish(
            profile_dict,
            req.averaging_seconds,
            req.rtk_timeout_seconds,
            req.min_accuracy,
        )
    )

    return {"ok": True, "message": f"Connecting to {profile['name']}..."}


async def _run_cors_establish(
    profile: dict,
    averaging_seconds: int,
    rtk_timeout: int,
    min_accuracy: float,
):
    """Background task: connect to CORS, wait for RTK fix, average, start base."""
    global _current_mode, _current_site, _current_session_id, _session_started_at
    global _establishing, _establish_progress, _cors_ntrip_client

    async with _mode_lock:
        # End previous session
        await _end_current_session()

        _current_mode = "cors_establish"
        _current_site = None
        _current_session_id = None
        _session_started_at = None
        _establishing = True
        _establish_progress = {
            "phase": "connecting",
            "elapsed_seconds": 0,
            "total_seconds": rtk_timeout,
            "rtk_quality": "none",
            "accuracy_h": 0,
            "accuracy_v": 0,
            "samples": 0,
            "current_position": None,
            "ntrip_connected": False,
            "ntrip_bytes": 0,
            "profile_name": profile["name"],
        }

        await broadcast_event({
            "type": "mode_change",
            "mode": "cors_establish",
            "site": None,
            "session_id": None,
        })

        # Configure F9P as rover (disable TMODE3)
        try:
            await gnss_manager.configure_rover()
        except Exception as exc:
            logger.error("Failed to configure rover mode: %s", exc)

        # Start NTRIP client
        ntrip_bytes = 0

        async def on_rtcm(data: bytes):
            nonlocal ntrip_bytes
            ntrip_bytes += len(data)
            await gnss_manager.inject_rtcm(data)

        async def gga_provider() -> str | None:
            return await gnss_manager.generate_gga()

        client = NTRIPClient(
            host=profile["host"],
            port=profile["port"],
            mountpoint=profile["mountpoint"],
            username=profile["username"],
            password=profile["password"],
            on_rtcm=on_rtcm,
            gga_provider=gga_provider,
        )
        _cors_ntrip_client = client
        await client.start()

        # ── Phase 1: Wait for RTK Fix ──
        start_time = time.time()
        rtk_achieved = False

        for elapsed in range(rtk_timeout):
            if not _establishing:
                await client.stop()
                _cors_ntrip_client = None
                return

            await asyncio.sleep(1.0)

            rtk_quality = await gnss_state.get_rtk_quality()
            snap = await gnss_state.snapshot()
            current_elapsed = int(time.time() - start_time)

            _establish_progress = {
                "phase": "waiting_fix",
                "elapsed_seconds": current_elapsed,
                "total_seconds": rtk_timeout,
                "rtk_quality": rtk_quality,
                "accuracy_h": snap.get("accuracy_h", 0),
                "accuracy_v": snap.get("accuracy_v", 0) if "accuracy_v" in snap else 0,
                "samples": 0,
                "current_position": {
                    "lat": snap["latitude"],
                    "lon": snap["longitude"],
                    "height": snap["height"],
                } if snap.get("latitude") else None,
                "ntrip_connected": client.is_connected,
                "ntrip_bytes": ntrip_bytes,
                "profile_name": profile["name"],
            }

            await broadcast_event({
                "type": "establish_progress",
                **_establish_progress,
            })

            if rtk_quality == "fixed" and snap.get("accuracy_h", 999) < min_accuracy:
                rtk_achieved = True
                break

        if not rtk_achieved:
            logger.warning("CORS establish timed out after %ds without RTK fix", rtk_timeout)
            await client.stop()
            _cors_ntrip_client = None
            _establishing = False
            _current_mode = "idle"
            _current_site = None
            _current_session_id = None
            _session_started_at = None
            _establish_progress = None
            await broadcast_event({
                "type": "mode_change",
                "mode": "idle",
                "site": None,
                "session_id": None,
            })
            await broadcast_event({
                "type": "establish_error",
                "message": f"RTK fix not achieved after {rtk_timeout}s",
            })
            return

        # ── Phase 2: Average RTK-Fixed Position ──
        lat_samples = []
        lon_samples = []
        height_samples = []
        acc_h_samples = []
        acc_v_samples = []
        avg_start = time.time()

        for elapsed in range(averaging_seconds):
            if not _establishing:
                await client.stop()
                _cors_ntrip_client = None
                return

            await asyncio.sleep(1.0)

            rtk_quality = await gnss_state.get_rtk_quality()
            position = await gnss_state.get_position()
            snap = await gnss_state.snapshot()

            if position is not None and rtk_quality == "fixed":
                lat, lon, h = position
                lat_samples.append(lat)
                lon_samples.append(lon)
                height_samples.append(h)
                acc_h_samples.append(snap.get("accuracy_h", 0))
                acc_v_samples.append(snap.get("accuracy_v", 0) if "accuracy_v" in snap else 0)

            current_elapsed = int(time.time() - avg_start)
            current_pos = None
            if lat_samples:
                current_pos = {
                    "lat": sum(lat_samples) / len(lat_samples),
                    "lon": sum(lon_samples) / len(lon_samples),
                    "height": sum(height_samples) / len(height_samples),
                }

            _establish_progress = {
                "phase": "averaging",
                "elapsed_seconds": current_elapsed,
                "total_seconds": averaging_seconds,
                "rtk_quality": rtk_quality,
                "accuracy_h": snap.get("accuracy_h", 0),
                "accuracy_v": snap.get("accuracy_v", 0) if "accuracy_v" in snap else 0,
                "samples": len(lat_samples),
                "current_position": current_pos,
                "ntrip_connected": client.is_connected,
                "ntrip_bytes": ntrip_bytes,
                "profile_name": profile["name"],
            }

            await broadcast_event({
                "type": "establish_progress",
                **_establish_progress,
            })

        # Stop NTRIP client
        await client.stop()
        _cors_ntrip_client = None

        # Check we got enough samples
        if len(lat_samples) < 5:
            logger.error("CORS establish: only %d RTK-fixed samples (need >= 5)", len(lat_samples))
            _establishing = False
            _current_mode = "idle"
            _current_site = None
            _current_session_id = None
            _session_started_at = None
            _establish_progress = None
            await broadcast_event({
                "type": "mode_change",
                "mode": "idle",
                "site": None,
                "session_id": None,
            })
            await broadcast_event({
                "type": "establish_error",
                "message": f"Only {len(lat_samples)} RTK-fixed samples collected",
            })
            return

        # ── Phase 3: Save and Switch to Base ──
        avg_lat = sum(lat_samples) / len(lat_samples)
        avg_lon = sum(lon_samples) / len(lon_samples)
        avg_height = sum(height_samples) / len(height_samples)
        avg_acc_h = sum(acc_h_samples) / len(acc_h_samples) if acc_h_samples else None
        avg_acc_v = sum(acc_v_samples) / len(acc_v_samples) if acc_v_samples else None

        site_name = f"CORS Base {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        active_pid = await get_active_project_id()

        async with get_db() as db:
            cursor = await db.execute(
                """
                INSERT INTO sites (name, lat, lon, height, source, accuracy_h, accuracy_v,
                                   established, notes, project_id)
                VALUES (?, ?, ?, ?, 'cors_rtk', ?, ?, datetime('now'), ?, ?)
                """,
                (
                    site_name,
                    avg_lat,
                    avg_lon,
                    avg_height,
                    avg_acc_h,
                    avg_acc_v,
                    f"CORS RTK via {profile['name']}: {len(lat_samples)} samples over {averaging_seconds}s",
                    active_pid,
                ),
            )
            site_id = cursor.lastrowid

            try:
                await db.execute(
                    "UPDATE sites SET geom = MakePoint(?, ?, 4326) WHERE id = ?",
                    (avg_lon, avg_lat, site_id),
                )
            except Exception:
                pass

            await db.commit()

        site_dict = {
            "id": site_id,
            "name": site_name,
            "lat": avg_lat,
            "lon": avg_lon,
            "height": avg_height,
        }

        # Configure as base and start outputs
        await start_base(gnss_manager, avg_lat, avg_lon, avg_height)

        # Create session
        async with get_db() as db:
            cursor = await db.execute(
                """
                INSERT INTO sessions (mode, site_id, started_at, project_id)
                VALUES ('known_base', ?, datetime('now'), ?)
                """,
                (site_id, active_pid),
            )
            session_id = cursor.lastrowid
            await db.commit()

        # Update state
        _current_mode = "known_base"
        _current_site = site_dict
        _current_session_id = session_id
        _session_started_at = datetime.now(timezone.utc).isoformat()
        _establishing = False
        _establish_progress = None

        await broadcast_event({
            "type": "mode_change",
            "mode": "known_base",
            "site": site_dict,
            "session_id": session_id,
        })

        logger.info(
            "CORS establish complete: lat=%.9f lon=%.9f height=%.4f acc=%.4fm (%d samples via %s)",
            avg_lat, avg_lon, avg_height, avg_acc_h or 0, len(lat_samples), profile["name"],
        )


@router.post("/stop")
async def stop_mode():
    """Stop current mode and set to IDLE.

    1. End current session
    2. Stop RTCM outputs and disable base mode on receiver
    3. Set mode to idle
    4. Broadcast via WebSocket
    """
    global _current_mode, _current_site, _current_session_id, _session_started_at
    global _establishing, _establish_progress, _establish_task, _cors_ntrip_client

    # Stop CORS NTRIP client if running
    if _cors_ntrip_client is not None:
        try:
            await _cors_ntrip_client.stop()
        except Exception:
            pass
        _cors_ntrip_client = None

    # Cancel any running establish task
    if _establish_task is not None and not _establish_task.done():
        _establishing = False
        _establish_task.cancel()
        try:
            await _establish_task
        except asyncio.CancelledError:
            pass
        _establish_task = None

    if _mode_lock.locked():
        raise HTTPException(status_code=409, detail="Mode change in progress")

    async with _mode_lock:
        await _end_current_session()

        # Stop RTCM outputs and disable base mode
        await stop_base(gnss_manager)

        # Update state
        _current_mode = "idle"
        _current_site = None
        _current_session_id = None
        _session_started_at = None
        _establishing = False
        _establish_progress = None

        # Broadcast
        await broadcast_event({
            "type": "mode_change",
            "mode": "idle",
            "site": None,
            "session_id": None,
        })

        logger.info("Mode stopped, now IDLE")

    return {"ok": True}


@router.post("/resume")
async def resume_mode():
    """Resume the last active session.

    Finds the last non-idle session and restarts with the same parameters.
    """
    global _current_mode, _current_site, _current_session_id, _session_started_at

    if _mode_lock.locked():
        raise HTTPException(status_code=409, detail="Mode change in progress")

    async with _mode_lock:
        # Find last session (scoped to active project if one is set)
        active_pid = await get_active_project_id()
        async with get_db() as db:
            query = """
                SELECT s.id, s.mode, s.site_id, si.name as site_name,
                       si.lat, si.lon, si.height
                FROM sessions s
                LEFT JOIN sites si ON s.site_id = si.id
                WHERE s.mode != 'idle'
            """
            params: list = []
            if active_pid is not None:
                query += " AND s.project_id = ?"
                params.append(active_pid)
            query += " ORDER BY s.started_at DESC LIMIT 1"
            cursor = await db.execute(query, params)
            session = await cursor.fetchone()

        if session is None:
            raise HTTPException(status_code=404, detail="No previous session to resume")

    # Resume based on mode type
    if session["mode"] == "known_base" and session["site_id"]:
        return await start_known_base(KnownBaseRequest(site_id=session["site_id"]))
    elif session["mode"] == "relative_base" and session["site_id"]:
        # For relative base, re-use the previously averaged position
        return await start_known_base(KnownBaseRequest(site_id=session["site_id"]))
    else:
        raise HTTPException(status_code=400, detail=f"Cannot resume mode: {session['mode']}")


async def _end_current_session():
    """End the current session by setting ended_at timestamp."""
    global _current_session_id

    if _current_session_id is not None:
        async with get_db() as db:
            await db.execute(
                "UPDATE sessions SET ended_at = datetime('now') WHERE id = ?",
                (_current_session_id,),
            )
            await db.commit()
        _current_session_id = None
