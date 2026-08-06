"""Periodic disk snapshot of RTDB for forensics.

Dumps the full RTDB ctypes buffer to a binary file at configurable intervals.
Uses atomic write (write to .tmp, rename) to prevent partial reads.
Keeps last N snapshots and deletes oldest.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import time
from pathlib import Path

from ems_common.rtdb import EmsRtdb

logger = logging.getLogger(__name__)

# Maximum number of snapshot files to retain
_MAX_SNAPSHOTS: int = 10


class SnapshotManager:
    """Manages periodic RTDB snapshot dumps to disk.

    Args:
        rtdb: Attached EmsRtdb ctypes struct (mapped to shm).
        snapshot_dir: Directory to write snapshot files.
        interval_s: Seconds between periodic snapshots.
    """

    def __init__(
        self,
        rtdb: EmsRtdb,
        snapshot_dir: Path,
        interval_s: int = 60,
    ) -> None:
        self._rtdb = rtdb
        self._snapshot_dir = snapshot_dir
        self._interval_s = interval_s
        self._rtdb_size = ctypes.sizeof(EmsRtdb)

    def dump_snapshot(self, reason: str = "periodic") -> Path:
        """Dump the full RTDB to a binary file on disk.

        Uses atomic write: writes to a .tmp file then renames.
        Maintains a maximum of _MAX_SNAPSHOTS files, deleting oldest.

        Args:
            reason: Human-readable reason for the snapshot (e.g., "periodic", "safety").

        Returns:
            Path to the created snapshot file.
        """
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"rtdb_{timestamp}_{reason}.bin"
        final_path = self._snapshot_dir / filename
        tmp_path = self._snapshot_dir / f"{filename}.tmp"

        # Copy full RTDB buffer to bytes via memmove
        buf = (ctypes.c_char * self._rtdb_size)()
        ctypes.memmove(buf, ctypes.addressof(self._rtdb), self._rtdb_size)
        data = bytes(buf)

        # Atomic write: write to tmp, then rename
        with open(tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.rename(str(tmp_path), str(final_path))

        logger.info("RTDB snapshot saved: %s (%d bytes, reason=%s)", final_path.name, len(data), reason)

        # Prune old snapshots
        self._prune_old_snapshots()

        return final_path

    def dump_on_safety_event(self) -> Path:
        """Dump an immediate snapshot triggered by a safety event.

        Returns:
            Path to the created snapshot file.
        """
        return self.dump_snapshot("safety")

    async def snapshot_loop(self) -> None:
        """Dump RTDB snapshots at configured interval indefinitely."""
        while True:
            await asyncio.sleep(self._interval_s)
            try:
                self.dump_snapshot("periodic")
            except Exception:
                logger.exception("Failed to dump RTDB snapshot")

    def _prune_old_snapshots(self) -> None:
        """Delete oldest snapshot files if count exceeds _MAX_SNAPSHOTS."""
        snapshots = sorted(
            self._snapshot_dir.glob("rtdb_*.bin"),
            key=lambda p: p.stat().st_mtime,
        )
        while len(snapshots) > _MAX_SNAPSHOTS:
            oldest = snapshots.pop(0)
            try:
                oldest.unlink()
                logger.info("Pruned old snapshot: %s", oldest.name)
            except OSError:
                logger.warning("Failed to prune snapshot: %s", oldest.name)
