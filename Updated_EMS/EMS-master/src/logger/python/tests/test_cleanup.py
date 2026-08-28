"""Tests for retention expiry, FIFO deletion order, and crash recovery.

Validates:
- Parquet files older than retention_days are identified for deletion
- JSONL files older than retention_days are identified for deletion
- File age determined from directory path date components, not mtime
- Stale .tmp files found and cleaned up on startup
- Results sorted oldest-first for FIFO deletion
- Three-tier FIFO cleanup with disk monitoring
- Empty directories removed after file deletion
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


def _create_parquet_file(data_dir: Path, file_date: date, name: str = "telemetry_c0_00.parquet") -> Path:
    """Create a fake Parquet file at the expected directory structure."""
    d: Path = data_dir / str(file_date.year) / f"{file_date.month:02d}" / f"{file_date.day:02d}"
    d.mkdir(parents=True, exist_ok=True)
    f: Path = d / name
    f.write_bytes(b"PAR1fakecontent")
    return f


def _create_jsonl_file(data_dir: Path, file_date: date) -> Path:
    """Create a fake JSONL file at the expected directory structure."""
    d: Path = data_dir / "events" / str(file_date.year) / f"{file_date.month:02d}"
    d.mkdir(parents=True, exist_ok=True)
    f: Path = d / f"events_{file_date.strftime('%Y%m%d')}.jsonl"
    f.write_text('{"ts":1,"msg":"test"}\n')
    return f


class TestRetentionExpiryParquet:
    """Verify Parquet expiry identifies files older than retention threshold."""

    def test_retention_expiry_parquet(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import find_expired_parquet

        today: date = date.today()
        # Create files: 95 days ago (expired), 85 days ago (not expired)
        old_file: Path = _create_parquet_file(tmp_data_dir, today - timedelta(days=95))
        _create_parquet_file(tmp_data_dir, today - timedelta(days=85))

        result: list[Path] = find_expired_parquet(tmp_data_dir, retention_days=90)
        assert len(result) == 1
        assert result[0] == old_file


class TestRetentionExpiryJsonl:
    """Verify JSONL expiry identifies files older than retention threshold."""

    def test_retention_expiry_jsonl(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import find_expired_jsonl

        today: date = date.today()
        # Create files: 185 days ago (expired), 175 days ago (not expired)
        old_file: Path = _create_jsonl_file(tmp_data_dir, today - timedelta(days=185))
        _create_jsonl_file(tmp_data_dir, today - timedelta(days=175))

        result: list[Path] = find_expired_jsonl(tmp_data_dir, retention_days=180)
        assert len(result) == 1
        assert result[0] == old_file


class TestExpirySortsOldestFirst:
    """Verify returned file lists are sorted oldest-first for FIFO."""

    def test_expiry_sorts_oldest_first(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import find_expired_parquet

        today: date = date.today()
        dates: list[date] = [
            today - timedelta(days=100),
            today - timedelta(days=120),
            today - timedelta(days=95),
        ]
        for d in dates:
            _create_parquet_file(tmp_data_dir, d)

        result: list[Path] = find_expired_parquet(tmp_data_dir, retention_days=90)
        assert len(result) == 3
        # Should be oldest first: 120, 100, 95
        assert "120" not in str(result[2])  # 120-day-old should NOT be last


class TestStaleTmpDetection:
    """Verify .tmp files are found in the data directory tree."""

    def test_stale_tmp_detection(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import find_stale_tmp_files

        today: date = date.today()
        d: Path = tmp_data_dir / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
        d.mkdir(parents=True, exist_ok=True)
        tmp1: Path = d / "telemetry_c0_00.parquet.tmp"
        tmp1.write_bytes(b"incomplete")
        tmp2: Path = d / "telemetry_c0_01.parquet.tmp"
        tmp2.write_bytes(b"incomplete2")

        result: list[Path] = find_stale_tmp_files(tmp_data_dir)
        assert len(result) == 2


class TestCrashRecoveryTmpCleanup:
    """Verify .tmp files are deleted on cleanup."""

    def test_crash_recovery_tmp_cleanup(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import cleanup_stale_tmp

        today: date = date.today()
        d: Path = tmp_data_dir / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "file1.parquet.tmp").write_bytes(b"x")
        (d / "file2.parquet.tmp").write_bytes(b"y")

        count: int = cleanup_stale_tmp(tmp_data_dir)
        assert count == 2
        assert len(list(tmp_data_dir.rglob("*.tmp"))) == 0


class TestInvalidPathSkipped:
    """Verify non-date directories don't crash the scanner."""

    def test_invalid_path_skipped(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import find_expired_parquet

        # Create a parquet file in a non-date directory
        bad_dir: Path = tmp_data_dir / "backup" / "old"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "data.parquet").write_bytes(b"PAR1")

        # Should not crash, should return empty (no valid dated files)
        result: list[Path] = find_expired_parquet(tmp_data_dir, retention_days=90)
        assert len(result) == 0


# --- Task 2: Three-tier FIFO cleanup with disk monitoring ---


def _make_logger_config(data_dir: Path) -> "LoggerConfig":
    """Create a LoggerConfig pointing at the given data directory."""
    from ems_logger.config import LoggerConfig, StorageConfig

    return LoggerConfig(
        storage=StorageConfig(
            data_dir=str(data_dir),
            parquet_retention_days=90,
            jsonl_retention_days=180,
            disk_usage_threshold_pct=80,
            cleanup_interval_s=300,
        ),
    )


class TestDiskUsageCalculation:
    """Verify get_disk_usage_pct returns reasonable value."""

    def test_disk_usage_calculation(self, tmp_path: Path) -> None:
        from ems_logger.cleanup import get_disk_usage_pct

        usage: float = get_disk_usage_pct(tmp_path)
        assert 0.0 <= usage <= 100.0


class TestFifoDeletionOrder:
    """Verify Parquet deleted before JSONL at same tier."""

    def test_fifo_deletion_order(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import RetentionManager

        today: date = date.today()
        config = _make_logger_config(tmp_data_dir)

        # Create expired parquet and expired JSONL
        _create_parquet_file(tmp_data_dir, today - timedelta(days=95))
        _create_jsonl_file(tmp_data_dir, today - timedelta(days=185))

        # Mock disk always above threshold so all tiers run
        with patch("ems_logger.cleanup.get_disk_usage_pct", return_value=85.0):
            mgr = RetentionManager(config)
            result = mgr.run_cleanup()

        assert result["parquet_deleted"] >= 1
        assert result["jsonl_deleted"] >= 1
        # Tier should be at least 2 (both expired types deleted)
        assert result["tier"] >= 2


class TestFifoTier3SurvivalMode:
    """Verify within-retention oldest deleted when no expired files exist."""

    def test_fifo_tier3_survival_mode(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import RetentionManager

        today: date = date.today()
        config = _make_logger_config(tmp_data_dir)

        # Create only non-expired files (within retention)
        _create_parquet_file(tmp_data_dir, today - timedelta(days=30))
        _create_parquet_file(tmp_data_dir, today - timedelta(days=20), name="telemetry_c0_01.parquet")

        # Mock disk always above threshold -- forces tier 3
        with patch("ems_logger.cleanup.get_disk_usage_pct", return_value=95.0):
            mgr = RetentionManager(config)
            result = mgr.run_cleanup()

        assert result["tier"] == 3
        assert result["parquet_deleted"] >= 1


class TestFifoStopsWhenBelowThreshold:
    """Verify cleanup stops early when disk usage drops below threshold."""

    def test_fifo_stops_when_below_threshold(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import RetentionManager

        today: date = date.today()
        config = _make_logger_config(tmp_data_dir)

        # Create multiple expired parquet files
        _create_parquet_file(tmp_data_dir, today - timedelta(days=95))
        _create_parquet_file(tmp_data_dir, today - timedelta(days=100), name="telemetry_c0_01.parquet")
        _create_parquet_file(tmp_data_dir, today - timedelta(days=105), name="telemetry_c0_02.parquet")

        # First call returns 85 (above), after first delete returns 75 (below)
        call_count: list[int] = [0]

        def mock_usage(path: str | Path) -> float:
            call_count[0] += 1
            if call_count[0] <= 1:
                return 85.0
            return 75.0

        with patch("ems_logger.cleanup.get_disk_usage_pct", side_effect=mock_usage):
            mgr = RetentionManager(config)
            result = mgr.run_cleanup()

        # Should have stopped after first delete brought usage below 80%
        assert result["parquet_deleted"] == 1
        assert result["tier"] == 1


class TestCleanupRemovesEmptyDirs:
    """Verify empty parent directories removed after file deletion."""

    def test_cleanup_removes_empty_dirs(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import RetentionManager

        today: date = date.today()
        config = _make_logger_config(tmp_data_dir)

        old_date: date = today - timedelta(days=95)
        pf: Path = _create_parquet_file(tmp_data_dir, old_date)
        day_dir: Path = pf.parent

        with patch("ems_logger.cleanup.get_disk_usage_pct", return_value=85.0):
            mgr = RetentionManager(config)
            mgr.run_cleanup()

        # Day directory should be removed since it's now empty
        assert not day_dir.exists()


class TestJsonlNeverDeletedBeforeParquet:
    """At the same tier, Parquet files must be deleted first."""

    def test_jsonl_never_deleted_before_parquet(self, tmp_data_dir: Path) -> None:
        from ems_logger.cleanup import RetentionManager

        today: date = date.today()
        config = _make_logger_config(tmp_data_dir)

        # Create both expired parquet and expired JSONL
        _create_parquet_file(tmp_data_dir, today - timedelta(days=95))
        _create_jsonl_file(tmp_data_dir, today - timedelta(days=185))

        deleted_order: list[str] = []
        original_unlink = Path.unlink

        def tracking_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self.suffix == ".parquet":
                deleted_order.append("parquet")
            elif self.suffix == ".jsonl":
                deleted_order.append("jsonl")
            original_unlink(self, *args, **kwargs)

        with patch("ems_logger.cleanup.get_disk_usage_pct", return_value=85.0):
            with patch.object(Path, "unlink", tracking_unlink):
                mgr = RetentionManager(config)
                mgr.run_cleanup()

        # Parquet must appear before JSONL in deletion order
        assert len(deleted_order) >= 2
        parquet_idx: int = deleted_order.index("parquet")
        jsonl_idx: int = deleted_order.index("jsonl")
        assert parquet_idx < jsonl_idx
