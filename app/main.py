"""
Survey365 FastAPI application entry point.

- Mounts static files from ui/ directory
- Registers all API route modules
- Registers WebSocket endpoint
- Startup: init DB, set default password, start GNSS reader, start WS broadcast
- Shutdown: stop GNSS reader, stop WS broadcast
- Runs on 0.0.0.0:8080 behind Nginx

Usage:
    uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
"""

import asyncio
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .auth import ensure_default_password
from .db import init_db
from .gnss import gnss_manager
from .routes import auth as auth_routes
from .routes import config as config_routes
from .routes import mode as mode_routes
from .routes import ntrip as ntrip_routes
from .routes import projects as projects_routes
from .routes import sites as sites_routes
from .routes import status as status_routes
from .routes import system as system_routes
from .routes import wifi as wifi_routes
from .ws import live as ws_live

# Configure logging — stdout (journalctl) + rotating file
_log_fmt = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("survey365")

# Rotating file handler: colocate logs with the active database when configured.
_db_path = os.environ.get("SURVEY365_DB")
if _db_path:
    _log_dir = Path(_db_path).resolve().parent / "logs"
else:
    _log_dir = Path(__file__).parent.parent / "data" / "logs"

try:
    _log_dir.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.handlers.RotatingFileHandler(
        _log_dir / "survey365.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    _file_handler.setFormatter(_log_fmt)
    logging.getLogger().addHandler(_file_handler)
except OSError:
    logger.warning("File logging disabled; unable to write %s", _log_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    # --- Startup ---
    logger.info("Survey365 starting up...")

    # Initialize database (create tables if needed)
    await init_db()
    logger.info("Database initialized")

    # Set default password on first boot
    await ensure_default_password()

    # Start GNSS manager (serial reader + backend)
    await gnss_manager.start()

    # Start WebSocket status broadcast
    ws_live.start_broadcast()

    logger.info("Survey365 ready")

    yield

    # --- Shutdown ---
    logger.info("Survey365 shutting down...")

    # Stop WebSocket broadcast
    try:
        await asyncio.wait_for(ws_live.stop_broadcast(), timeout=2.0)
    except TimeoutError:
        logger.error("Timed out stopping WebSocket broadcast")
    except Exception:
        logger.exception("Failed stopping WebSocket broadcast")

    # Stop GNSS manager
    try:
        await asyncio.wait_for(gnss_manager.stop(), timeout=8.0)
    except TimeoutError:
        logger.error("Timed out stopping GNSS manager")
    except Exception:
        logger.exception("Failed stopping GNSS manager")

    logger.info("Survey365 stopped")


# Create FastAPI app
app = FastAPI(
    title="Survey365",
    description="Field operations controller for RTK GNSS base stations",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware -- permissive for LAN development
# In production behind Nginx, this is largely irrelevant (same-origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Register API routes ---
app.include_router(status_routes.router)
app.include_router(mode_routes.router)
app.include_router(sites_routes.router)
app.include_router(projects_routes.router)
app.include_router(ntrip_routes.router)
app.include_router(config_routes.router)
app.include_router(auth_routes.router)
app.include_router(system_routes.router)
app.include_router(wifi_routes.router)

# --- Register WebSocket ---
app.include_router(ws_live.router)

# --- Mount static files ---
# The ui/ directory is served at root. This must be last so API routes take priority.
ui_dir = Path(__file__).parent.parent / "ui"
if ui_dir.exists():
    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="static")
else:
    logger.warning("UI directory not found at %s -- static file serving disabled", ui_dir)
