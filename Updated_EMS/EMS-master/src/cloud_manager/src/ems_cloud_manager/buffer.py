"""Offline JSONL buffer for EMS cloud_manager (CLOUD-04).

Provides append-only writes to hourly files, crash-safe reads with truncated
line skipping, and dual-constraint retention management (max_hours + max_mb).

File layout: buffer_dir/{YYYY}/{MM}/{DD}/cloud_{HH}.jsonl

Design notes:
- No MQTT or ZMQ dependencies — fully testable with real tmp_path directories.
- Time is derived from datetime.now(timezone.utc) so tests can freeze/mock it.
- File age is determined from path components, not mtime (resilient to copies).
- fsync every _SYNC_EVERY writes (matches logger pattern for performance).
- Retention runs on write() only, never during drain() (drain only decreases size).
"""

from __future__ import annotations

import json
import logging
import os
import time
import datetime as _dt_module
from datetime import timezone
from pathlib import Path
from typing import Any, Iterator

# ``datetime`` is exposed at module scope so tests can freeze time via:
#   patch("ems_cloud_manager.buffer.datetime")
# All internal construction of datetime objects uses _dt_module.datetime
# directly so it is not affected by the test mock.
datetime = _dt_module.datetime

logger: logging.Logger = logging.getLogger(__name__)

# Sync every N writes for performance (matches ems_logger pattern)
_SYNC_EVERY: int = 100


class BufferManager:
    """JSONL offline buffer with hourly file rotation and dual-constraint retention.

    Args:
        buffer_dir: Root directory for buffer files. Created if missing.
        max_hours: Delete files whose path-date is older than this many hours.
        max_mb: Delete oldest files when total buffer exceeds this size in MB.
    """

    def __init__(self, buffer_dir: Path, max_hours: int, max_mb: int) -> None:
        self._buffer_dir: Path = Path(buffer_dir)
        self._max_hours: int = max_hours
        self._max_mb: int = max_mb

        self._buffer_dir.mkdir(parents=True, exist_ok=True)

        # Currently-open write handle and its hour key ("YYYY-MM-DD-HH")
        self._current_file: Any | None = None  # IO[str] | None
        self._current_hour_key: str = ""

        # fsync counter
        self._writes_since_sync: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, msg_type: str, topic: str, payload: dict[str, Any]) -> None:
        """Append one record to the current hourly JSONL file.

        Enforces retention first (max_hours + max_mb). If enforcement
        cannot free enough space (max_mb=0 edge case), logs WARNING and drops.

        Args:
            msg_type: Record type label (e.g. "telemetry", "event").
            topic: ZMQ/MQTT topic string.
            payload: Arbitrary dict payload to persist.
        """
        self._enforce_retention()

        # Drop if still over limit after enforcement (only possible when max_mb=0
        # or truly no files left to delete but size still exceeds limit)
        if self._max_mb == 0 and self._total_size_mb() > 0:
            logger.warning(
                "Buffer full (max_mb=%d): dropping message type=%s topic=%s",
                self._max_mb,
                msg_type,
                topic,
            )
            return

        now: datetime = datetime.now(timezone.utc)
        hour_key: str = now.strftime("%Y-%m-%d-%H")

        # Rotate if the hour changed
        if hour_key != self._current_hour_key:
            self._rotate(now, hour_key)

        record: dict[str, Any] = {
            "ts": time.time(),
            "type": msg_type,
            "topic": topic,
            "payload": payload,
        }
        line: str = json.dumps(record, separators=(",", ":")) + "\n"

        assert self._current_file is not None
        self._current_file.write(line)
        self._writes_since_sync += 1

        if self._writes_since_sync >= _SYNC_EVERY:
            self._sync()

    def drain(self) -> Iterator[tuple[Path, list[dict[str, Any]]]]:
        """Yield (file_path, records) for each buffered file, oldest-first.

        Skips the currently-open write file (caller should flush() first).
        Skips lines that fail JSON parsing (crash recovery).

        Caller is responsible for deleting the file after consuming it.

        Yields:
            Tuples of (path, records) where records is a list of parsed dicts.
        """
        for file_path in self._all_files_sorted():
            # Skip the file currently being written
            if self._current_hour_key and file_path == self._current_file_path():
                continue

            records: list[dict[str, Any]] = []
            try:
                with file_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.debug(
                                "Skipping unparseable JSONL line in %s: %.80s",
                                file_path,
                                line,
                            )
            except OSError as exc:
                logger.warning("Cannot read buffer file %s: %s", file_path, exc)
                continue

            yield file_path, records

    def flush(self) -> None:
        """Flush and close the currently-open write file.

        Resets _current_hour_key so the next write() opens a fresh handle.
        Safe to call when no file is open.
        """
        if self._current_file is not None:
            try:
                self._current_file.flush()
                os.fsync(self._current_file.fileno())
            except OSError as exc:
                logger.warning("fsync during flush failed: %s", exc)
            finally:
                self._current_file.close()
                self._current_file = None
        self._current_hour_key = ""
        self._writes_since_sync = 0

    @property
    def stats(self) -> dict[str, Any]:
        """Return current buffer statistics.

        Returns:
            Dict with ``files_remaining`` (int) and ``mb_remaining`` (float).
        """
        files: list[Path] = self._all_files_sorted()
        total_mb: float = round(self._total_size_mb(), 2)
        return {
            "files_remaining": len(files),
            "mb_remaining": total_mb,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _rotate(self, now: datetime, hour_key: str) -> None:
        """Close old write handle and open a new one for the current hour.

        Args:
            now: Current UTC datetime (determines directory structure).
            hour_key: New hour key string "YYYY-MM-DD-HH".
        """
        # Close current file first
        if self._current_file is not None:
            try:
                self._current_file.flush()
                os.fsync(self._current_file.fileno())
            except OSError:
                pass
            self._current_file.close()
            self._current_file = None
            self._writes_since_sync = 0

        target: Path = self._current_file_path_for(now)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._current_file = open(target, "a", encoding="utf-8")  # noqa: SIM115
        self._current_hour_key = hour_key
        logger.debug("Opened buffer file: %s", target)

    def _enforce_retention(self) -> None:
        """Delete files that exceed max_hours age or push total over max_mb.

        Hour-based deletion runs first (expired files unconditionally removed).
        Size-based deletion then removes oldest files until total <= max_mb.
        Empty parent directories are cleaned up after each deletion.
        """
        now: datetime = datetime.now(timezone.utc)
        files: list[Path] = self._all_files_sorted()

        # 1. Age-based: delete files older than max_hours
        for file_path in list(files):
            file_dt: datetime | None = self._file_datetime_from_path(file_path)
            if file_dt is None:
                continue
            age_hours: float = (now - file_dt).total_seconds() / 3600.0
            if age_hours > self._max_hours:
                self._delete_file(file_path)
                files.remove(file_path)

        # 2. Size-based: delete oldest files until total <= max_mb
        while files and self._total_size_mb() > self._max_mb:
            oldest: Path = files.pop(0)
            self._delete_file(oldest)

    def _delete_file(self, path: Path) -> None:
        """Unlink a buffer file and remove empty parent directories.

        Args:
            path: Path to the JSONL file to delete.
        """
        try:
            path.unlink(missing_ok=True)
            logger.debug("Deleted buffer file: %s", path)
        except OSError as exc:
            logger.warning("Failed to delete buffer file %s: %s", path, exc)
            return
        self._remove_empty_parents(path)

    def _total_size_mb(self) -> float:
        """Return total size of all buffer files in megabytes.

        Returns:
            Total size in MB as a float.
        """
        total_bytes: int = sum(
            f.stat().st_size for f in self._all_files_sorted() if f.exists()
        )
        return total_bytes / (1024 * 1024)

    def _all_files_sorted(self) -> list[Path]:
        """Return all cloud_*.jsonl files sorted oldest-first by path.

        Path sort is lexicographic on POSIX paths — since the path encodes
        YYYY/MM/DD/cloud_HH this correctly orders files by creation time.

        Returns:
            Sorted list of Path objects.
        """
        return sorted(
            self._buffer_dir.rglob("cloud_*.jsonl"),
            key=lambda p: p.as_posix(),
        )

    def _current_file_path(self) -> Path:
        """Derive the expected path for the current UTC hour.

        Returns:
            Path object for the current hour's JSONL file.
        """
        now: datetime = datetime.now(timezone.utc)
        return self._current_file_path_for(now)

    def _current_file_path_for(self, now: datetime) -> Path:
        """Derive the path for the JSONL file corresponding to a given datetime.

        Args:
            now: UTC datetime used to determine the file path.

        Returns:
            Path: buffer_dir/YYYY/MM/DD/cloud_HH.jsonl
        """
        return (
            self._buffer_dir
            / now.strftime("%Y")
            / now.strftime("%m")
            / now.strftime("%d")
            / f"cloud_{now.strftime('%H')}.jsonl"
        )

    def _file_datetime_from_path(self, path: Path) -> datetime | None:
        """Extract UTC datetime from a buffer file's path components.

        Expected structure: .../YYYY/MM/DD/cloud_HH.jsonl

        Args:
            path: Path to the JSONL file.

        Returns:
            datetime with UTC timezone, or None if parsing fails.
        """
        try:
            day_dir: Path = path.parent
            month_dir: Path = day_dir.parent
            year_dir: Path = month_dir.parent

            year: int = int(year_dir.name)
            month: int = int(month_dir.name)
            day: int = int(day_dir.name)

            # stem is "cloud_HH"
            stem: str = path.stem  # e.g. "cloud_10"
            hour: int = int(stem.split("_")[1])

            # Use _dt_module.datetime directly so mocking `datetime` for now()
            # calls in tests does not affect path-parsing construction.
            return _dt_module.datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
        except (ValueError, IndexError, AttributeError):
            return None

    def _remove_empty_parents(self, path: Path) -> None:
        """Walk up from path.parent to _buffer_dir, removing empty directories.

        Args:
            path: The deleted file's path.
        """
        parent: Path = path.parent
        while parent != self._buffer_dir and parent.is_dir():
            try:
                parent.rmdir()  # succeeds only if empty
                logger.debug("Removed empty buffer directory: %s", parent)
                parent = parent.parent
            except OSError:
                break  # directory not empty or permission error

    def _sync(self) -> None:
        """Flush and fsync the current write file."""
        if self._current_file is not None and not self._current_file.closed:
            self._current_file.flush()
            os.fsync(self._current_file.fileno())
            self._writes_since_sync = 0
