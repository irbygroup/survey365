"""
RINEX logger: write raw GNSS data to timestamped files for post-processing.

Implements the RTCMOutput protocol. Files are rotated every N hours
and old files are gzip-compressed.
"""

import asyncio
import gzip
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("survey365.gnss.rinex_logger")


class RINEXLogger:
    """Write raw RTCM3 bytes to timestamped files."""

    name: str = "rinex"

    def __init__(
        self,
        data_dir: str = "data/rinex",
        rotate_hours: int = 24,
    ):
        self._data_dir = Path(data_dir)
        self._rotate_hours = rotate_hours
        self._file = None
        self._file_path: Path | None = None
        self._file_started: datetime | None = None
        self._total_bytes = 0
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def write(self, data: bytes) -> None:
        """Write RTCM3 data to the current log file."""
        self._ensure_file()
        if self._file is not None:
            self._file.write(data)
            self._file.flush()
            self._total_bytes += len(data)

    async def close(self) -> None:
        """Close the current file and compress it."""
        if self._file is not None:
            self._file.close()
            self._file = None

            # Compress the closed file
            if self._file_path is not None and self._file_path.exists():
                await asyncio.to_thread(self._compress, self._file_path)
            self._file_path = None
            self._file_started = None

    def _ensure_file(self):
        """Open a new file if needed, or rotate if the current file is too old."""
        now = datetime.now(timezone.utc)

        if self._file is not None and self._file_started is not None:
            age_hours = (now - self._file_started).total_seconds() / 3600
            if age_hours >= self._rotate_hours:
                # Rotate: close current, compress, open new
                self._file.close()
                if self._file_path is not None and self._file_path.exists():
                    # Compress in background (don't block writes)
                    path = self._file_path
                    asyncio.get_event_loop().run_in_executor(None, self._compress, path)
                self._file = None
                self._file_path = None
                self._file_started = None

        if self._file is None:
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            self._file_path = self._data_dir / f"gnss_{timestamp}.rtcm3"
            self._file = open(self._file_path, "ab")
            self._file_started = now
            self._total_bytes = 0
            logger.info("RINEX log started: %s", self._file_path)

    @staticmethod
    def _compress(path: Path):
        """Gzip compress a file and delete the original."""
        gz_path = path.with_suffix(path.suffix + ".gz")
        try:
            with open(path, "rb") as f_in:
                with gzip.open(gz_path, "wb") as f_out:
                    f_out.write(f_in.read())
            os.remove(path)
            logger.info("Compressed RINEX log: %s", gz_path)
        except Exception as exc:
            logger.warning("Failed to compress %s: %s", path, exc)
