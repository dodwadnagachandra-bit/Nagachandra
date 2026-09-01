"""Retention and cleanup manager for EMS logger data files.

Monitors disk usage, enforces FIFO deletion policy (Parquet before JSONL),
handles crash recovery for stale .tmp files from incomplete writes.

Three-tier FIFO cleanup:
  Tier 1: Delete expired Parquet files (oldest first)
  Tier 2: Delete expired JSONL files (oldest first)
  Tier 3: Delete within-retention data oldest-first (survival mode)

File age is determined from directory path date components, not file mtime,
ensuring consistent behavior regardless of file system timestamp changes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ems_logger.config import LoggerConfig

logger: logging.Logger = logging.getLogger(__name__)

# Pattern for JSONL filenames: events_YYYYMMDD.jsonl
_JSONL_DATE_RE: re.Pattern[str] = re.compile(r"events_(\d{4})(\d{2})(\d{2})\.jsonl$")


def _parse_date_from_parquet_path(path: Path) -> date | None:
    """Extract year/month/day from parent directories of a Parquet file.

    Expected structure: .../YYYY/MM/DD/filename.parquet
    Returns None if path doesn't match expected structure.
    """
    try:
        day_dir: Path = path.parent
        month_dir: Path = day_dir.parent
        year_dir: Path = month_dir.parent

        year: int = int(year_dir.name)
        month: int = int(month_dir.name)
        day: int = int(day_dir.name)

        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _parse_date_from_jsonl_path(path: Path) -> date | None:
    """Extract date from JSONL filename events_YYYYMMDD.jsonl.

    Returns None if filename doesn't match expected pattern.
    """
    match: re.Match[str] | None = _JSONL_DATE_RE.search(path.name)
    if match is None:
        return None

    try:
        year: int = int(match.group(1))
        month: int = int(match.group(2))
        day: int = int(match.group(3))
        return date(year, month, day)
    except ValueError:
        return None


def find_expired_parquet(data_dir: Path, retention_days: int) -> list[Path]:
    """Find Parquet files older than retention_days.

    Scans data_dir/**/*.parquet, parses dates from directory structure,
    returns files older than retention threshold sorted oldest-first.

    Args:
        data_dir: Root data directory to scan.
        retention_days: Maximum age in days before file is considered expired.

    Returns:
        List of expired Parquet file paths, sorted oldest-first (FIFO order).
    """
    cutoff: date = date.today() - timedelta(days=retention_days)
    expired: list[tuple[date, Path]] = []

    for parquet_file in data_dir.rglob("*.parquet"):
        file_date: date | None = _parse_date_from_parquet_path(parquet_file)
        if file_date is None:
            logger.debug("Skipping non-dated Parquet file: %s", parquet_file)
            continue
        if file_date < cutoff:
            expired.append((file_date, parquet_file))

    # Sort oldest-first for FIFO deletion
    expired.sort(key=lambda item: item[0])
    return [path for _, path in expired]


def find_expired_jsonl(data_dir: Path, retention_days: int) -> list[Path]:
    """Find JSONL files older than retention_days.

    Scans data_dir/events/**/*.jsonl, parses dates from filenames,
    returns files older than retention threshold sorted oldest-first.

    Args:
        data_dir: Root data directory to scan.
        retention_days: Maximum age in days before file is considered expired.

    Returns:
        List of expired JSONL file paths, sorted oldest-first (FIFO order).
    """
    cutoff: date = date.today() - timedelta(days=retention_days)
    expired: list[tuple[date, Path]] = []

    events_dir: Path = data_dir / "events"
    if not events_dir.exists():
        return []

    for jsonl_file in events_dir.rglob("*.jsonl"):
        file_date: date | None = _parse_date_from_jsonl_path(jsonl_file)
        if file_date is None:
            logger.debug("Skipping non-dated JSONL file: %s", jsonl_file)
            continue
        if file_date < cutoff:
            expired.append((file_date, jsonl_file))

    # Sort oldest-first for FIFO deletion
    expired.sort(key=lambda item: item[0])
    return [path for _, path in expired]


def find_stale_tmp_files(data_dir: Path) -> list[Path]:
    """Find all .tmp files in the data directory tree.

    These are incomplete Parquet files from crashes where the atomic
    rename (.tmp -> .parquet) never completed.

    Args:
        data_dir: Root data directory to scan.

    Returns:
        List of .tmp file paths found.
    """
    return list(data_dir.rglob("*.tmp"))


def cleanup_stale_tmp(data_dir: Path) -> int:
    """Delete all stale .tmp files from crashes.

    Called on startup to clean up incomplete Parquet files from previous
    crashes where the atomic rename never completed (LOG-07).

    Args:
        data_dir: Root data directory to scan.

    Returns:
        Number of .tmp files deleted.
    """
    tmp_files: list[Path] = find_stale_tmp_files(data_dir)
    count: int = 0

    for tmp_file in tmp_files:
        try:
            tmp_file.unlink()
            count += 1
            logger.info("Deleted stale tmp file: %s", tmp_file)
        except OSError as exc:
            logger.warning("Failed to delete tmp file %s: %s", tmp_file, exc)

    if count > 0:
        logger.info("Crash recovery: cleaned up %d stale .tmp files", count)

    return count


def _find_all_parquet_sorted(data_dir: Path) -> list[Path]:
    """Find ALL Parquet files sorted oldest-first by path date.

    Used for tier 3 survival mode where within-retention files
    must be deleted to free disk space.

    Args:
        data_dir: Root data directory to scan.

    Returns:
        List of all Parquet file paths, sorted oldest-first.
    """
    dated: list[tuple[date, Path]] = []

    for parquet_file in data_dir.rglob("*.parquet"):
        file_date: date | None = _parse_date_from_parquet_path(parquet_file)
        if file_date is None:
            continue
        dated.append((file_date, parquet_file))

    dated.sort(key=lambda item: item[0])
    return [path for _, path in dated]


def _find_all_jsonl_sorted(data_dir: Path) -> list[Path]:
    """Find ALL JSONL files sorted oldest-first by filename date.

    Used for tier 3 survival mode after all Parquet files are exhausted.

    Args:
        data_dir: Root data directory to scan.

    Returns:
        List of all JSONL file paths, sorted oldest-first.
    """
    events_dir: Path = data_dir / "events"
    if not events_dir.exists():
        return []

    dated: list[tuple[date, Path]] = []

    for jsonl_file in events_dir.rglob("*.jsonl"):
        file_date: date | None = _parse_date_from_jsonl_path(jsonl_file)
        if file_date is None:
            continue
        dated.append((file_date, jsonl_file))

    dated.sort(key=lambda item: item[0])
    return [path for _, path in dated]


def get_disk_usage_pct(path: str | Path) -> float:
    """Get disk usage percentage for the filesystem containing path.

    Uses os.statvfs() for POSIX disk statistics.

    Args:
        path: Any path on the target filesystem.

    Returns:
        Disk usage as a percentage (0.0 - 100.0).
    """
    stat = os.statvfs(str(path))
    total: int = stat.f_blocks * stat.f_frsize
    free: int = stat.f_bfree * stat.f_frsize

    if total == 0:
        return 0.0

    return (total - free) / total * 100.0


def _remove_empty_parents(file_path: Path, stop_at: Path) -> None:
    """Remove empty parent directories up to (but not including) stop_at.

    Args:
        file_path: The deleted file's path.
        stop_at: Stop removing directories at this ancestor.
    """
    parent: Path = file_path.parent
    while parent != stop_at and parent.is_dir():
        try:
            parent.rmdir()  # Only succeeds if empty
            logger.debug("Removed empty directory: %s", parent)
            parent = parent.parent
        except OSError:
            break  # Directory not empty or permission error


def _delete_files(
    files: list[Path],
    threshold_pct: float,
    data_dir: Path,
) -> int:
    """Delete files one at a time until disk usage drops below threshold.

    Removes empty parent directories after each deletion. Stops early
    if disk usage falls below the threshold.

    Args:
        files: List of files to delete, in deletion order.
        threshold_pct: Stop deleting when disk usage drops below this.
        data_dir: Root data directory (stop point for empty dir removal).

    Returns:
        Number of files deleted.
    """
    count: int = 0

    for file_path in files:
        if not file_path.exists():
            continue

        try:
            size: int = file_path.stat().st_size
            file_path.unlink()
            count += 1
            logger.info("Deleted %s (%d bytes)", file_path, size)

            # Remove empty parent directories
            _remove_empty_parents(file_path, data_dir)

            # Check if we're back below threshold
            if get_disk_usage_pct(data_dir) < threshold_pct:
                logger.info("Disk usage below %.1f%%, stopping cleanup", threshold_pct)
                break
        except OSError as exc:
            logger.warning("Failed to delete %s: %s", file_path, exc)

    return count


class RetentionManager:
    """Manages data retention with three-tier FIFO cleanup.

    Monitors disk usage and enforces deletion order:
      Tier 1: Expired Parquet files (oldest first)
      Tier 2: Expired JSONL files (oldest first)
      Tier 3: Within-retention data (Parquet first, then JSONL, oldest first)

    JSONL is never deleted before Parquet at the same tier, preserving
    event narrative for incident investigation.

    Args:
        config: LoggerConfig with storage settings.
    """

    def __init__(self, config: LoggerConfig) -> None:
        self._data_dir: Path = Path(config.storage.data_dir)
        self._parquet_retention: int = config.storage.parquet_retention_days
        self._jsonl_retention: int = config.storage.jsonl_retention_days
        self._threshold_pct: float = float(config.storage.disk_usage_threshold_pct)
        self._cleanup_interval: int = config.storage.cleanup_interval_s

    def run_cleanup(self) -> dict[str, int]:
        """Execute three-tier FIFO cleanup if disk usage exceeds threshold.

        Returns:
            Dict with parquet_deleted, jsonl_deleted, and tier (highest used).
        """
        parquet_deleted: int = 0
        jsonl_deleted: int = 0
        tier: int = 0

        current_usage: float = get_disk_usage_pct(self._data_dir)
        if current_usage < self._threshold_pct:
            logger.debug(
                "Disk usage %.1f%% below threshold %.1f%%, no cleanup needed",
                current_usage,
                self._threshold_pct,
            )
            return {"parquet_deleted": 0, "jsonl_deleted": 0, "tier": 0}

        logger.warning(
            "Disk usage %.1f%% exceeds threshold %.1f%%, starting cleanup",
            current_usage,
            self._threshold_pct,
        )

        # Tier 1: Delete expired Parquet (oldest first)
        tier = 1
        expired_parquet: list[Path] = find_expired_parquet(
            self._data_dir, self._parquet_retention
        )
        if expired_parquet:
            parquet_deleted += _delete_files(
                expired_parquet, self._threshold_pct, self._data_dir
            )
            if get_disk_usage_pct(self._data_dir) < self._threshold_pct:
                return {
                    "parquet_deleted": parquet_deleted,
                    "jsonl_deleted": jsonl_deleted,
                    "tier": tier,
                }

        # Tier 2: Delete expired JSONL (oldest first)
        tier = 2
        expired_jsonl: list[Path] = find_expired_jsonl(
            self._data_dir, self._jsonl_retention
        )
        if expired_jsonl:
            jsonl_deleted += _delete_files(
                expired_jsonl, self._threshold_pct, self._data_dir
            )
            if get_disk_usage_pct(self._data_dir) < self._threshold_pct:
                return {
                    "parquet_deleted": parquet_deleted,
                    "jsonl_deleted": jsonl_deleted,
                    "tier": tier,
                }

        # Tier 3: Survival mode -- delete within-retention, Parquet first
        tier = 3
        logger.warning("Entering tier 3 survival mode -- deleting within-retention data")

        all_parquet: list[Path] = _find_all_parquet_sorted(self._data_dir)
        if all_parquet:
            parquet_deleted += _delete_files(
                all_parquet, self._threshold_pct, self._data_dir
            )
            if get_disk_usage_pct(self._data_dir) < self._threshold_pct:
                return {
                    "parquet_deleted": parquet_deleted,
                    "jsonl_deleted": jsonl_deleted,
                    "tier": tier,
                }

        all_jsonl: list[Path] = _find_all_jsonl_sorted(self._data_dir)
        if all_jsonl:
            jsonl_deleted += _delete_files(
                all_jsonl, self._threshold_pct, self._data_dir
            )

        return {
            "parquet_deleted": parquet_deleted,
            "jsonl_deleted": jsonl_deleted,
            "tier": tier,
        }

    def startup_recovery(self) -> None:
        """Clean up stale .tmp files from previous crashes (LOG-07)."""
        cleanup_stale_tmp(self._data_dir)

    async def run_periodic(self) -> None:
        """Run cleanup every cleanup_interval_s seconds.

        Designed to be launched as an asyncio task. Runs until cancelled.
        """
        logger.info(
            "Starting periodic cleanup (every %ds, threshold %.1f%%)",
            self._cleanup_interval,
            self._threshold_pct,
        )

        while True:
            try:
                result: dict[str, int] = self.run_cleanup()
                if result["tier"] > 0:
                    logger.info(
                        "Cleanup complete: tier=%d parquet=%d jsonl=%d",
                        result["tier"],
                        result["parquet_deleted"],
                        result["jsonl_deleted"],
                    )
            except Exception:
                logger.exception("Error during periodic cleanup")

            await asyncio.sleep(self._cleanup_interval)
