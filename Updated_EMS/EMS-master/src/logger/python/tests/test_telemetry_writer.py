"""Tests for ParquetRotatingWriter and TelemetryWriter.

Covers Parquet writing, hourly rotation, directory structure,
Snappy compression, atomic .tmp rename, and topology metadata.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import msgpack
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import zmq
import zmq.asyncio

from ems_logger.parquet_schema import (
    build_cluster_schema,
    build_system_schema,
    build_topology_metadata,
)
from ems_logger.telemetry_writer import ParquetRotatingWriter, TelemetryWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cluster_row(ts_ms: int, racks: int = 2, modules: int = 2) -> dict[str, Any]:
    """Build a sample cluster row dict matching the schema."""
    row: dict[str, Any] = {"ts": ts_ms}
    for r in range(racks):
        prefix: str = f"rack{r}_"
        row[f"{prefix}pack_v"] = 51.2
        row[f"{prefix}pack_i"] = -12.5
        row[f"{prefix}soc"] = 78.3
        row[f"{prefix}soh"] = 99.1
        row[f"{prefix}min_cell_v"] = 3.18
        row[f"{prefix}max_cell_v"] = 3.25
        row[f"{prefix}avg_cell_v"] = 3.21
        row[f"{prefix}min_cell_t"] = 22.0
        row[f"{prefix}max_cell_t"] = 28.5
        row[f"{prefix}avg_cell_t"] = 25.2
        row[f"{prefix}fault_code"] = 0
        row[f"{prefix}online"] = 1
        for m in range(modules):
            mod_prefix: str = f"{prefix}mod{m}_"
            row[f"{mod_prefix}cell_v"] = [3.2, 3.21, 3.19]
            row[f"{mod_prefix}cell_t"] = [24.0, 25.0]
    return row


def _make_system_row(ts_ms: int) -> dict[str, Any]:
    """Build a sample system row dict matching the system schema."""
    return {
        "ts": ts_ms,
        "pcs_ac_voltage": 230.1,
        "pcs_ac_current": 45.2,
        "pcs_active_power": 10350.0,
        "pcs_reactive_power": 120.0,
        "pcs_dc_voltage": 384.0,
        "pcs_dc_current": 27.5,
        "pcs_frequency": 50.01,
        "pcs_temperature": 42.3,
        "pcs_state": 3,
        "pcs_fault_code": 0,
        "meter_voltage": 230.0,
        "meter_current": 44.8,
        "meter_active_power": 10300.0,
        "meter_reactive_power": 110.0,
        "meter_frequency": 50.0,
        "meter_power_factor": 0.99,
        "meter_energy_import": 12345.6,
        "meter_energy_export": 9876.5,
        "btms_inlet_temp": 20.5,
        "btms_outlet_temp": 25.3,
        "btms_fan_speed_pct": 60.0,
        "btms_cooling_active": 1,
        "gpio_di": [1, 0, 1, 0, 0, 0, 0, 1],
        "gpio_do": [0, 0, 0, 0, 0, 0, 0, 0],
        "system_control_state": 2,
        "system_source_priority": 1,
        "system_active_setpoint_kw": 10.0,
        "system_total_soc": 78.3,
        "system_total_power_kw": 10.35,
        "system_total_energy_kwh": 48.0,
        "system_ems_uptime_s": 86400,
    }


def _ts_for_hour(year: int, month: int, day: int, hour: int) -> int:
    """Return ms-since-epoch for a given date/hour (minute=0, sec=0)."""
    dt = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# ParquetRotatingWriter Tests
# ---------------------------------------------------------------------------


class TestParquetRotatingWriter:
    """Tests for the ParquetRotatingWriter class."""

    def test_parquet_write_from_telemetry(self, tmp_data_dir: Path) -> None:
        """Write 5 rows, close, verify Parquet readable with correct data."""
        schema: pa.Schema = build_cluster_schema(2, 2, 3, 2)
        metadata: dict[str, str] = build_topology_metadata(1, 2, 2, 3)
        ts_base: int = _ts_for_hour(2026, 3, 14, 10)

        writer = ParquetRotatingWriter(schema, tmp_data_dir, "telemetry_0", metadata)
        for i in range(5):
            row = _make_cluster_row(ts_base + i * 1000, racks=2, modules=2)
            writer.write_row(row)
        writer.close()

        # Find the written file
        parquet_files = list(tmp_data_dir.rglob("*.parquet"))
        assert len(parquet_files) == 1

        table: pa.Table = pq.read_table(parquet_files[0])
        assert table.num_rows == 5
        assert table.column("ts")[0].as_py() == ts_base
        assert table.column("rack0_pack_v")[0].as_py() == pytest.approx(51.2, abs=0.1)

    def test_parquet_hourly_rotation(self, tmp_data_dir: Path) -> None:
        """Write rows spanning 2 hours, verify 2 files created."""
        schema: pa.Schema = build_cluster_schema(2, 2, 3, 2)
        metadata: dict[str, str] = build_topology_metadata(1, 2, 2, 3)

        hour_10_ts: int = _ts_for_hour(2026, 3, 14, 10)
        hour_11_ts: int = _ts_for_hour(2026, 3, 14, 11)

        writer = ParquetRotatingWriter(schema, tmp_data_dir, "telemetry_0", metadata)
        # 3 rows in hour 10
        for i in range(3):
            writer.write_row(_make_cluster_row(hour_10_ts + i * 1000, 2, 2))
        # 2 rows in hour 11
        for i in range(2):
            writer.write_row(_make_cluster_row(hour_11_ts + i * 1000, 2, 2))
        writer.close()

        parquet_files = sorted(tmp_data_dir.rglob("*.parquet"))
        assert len(parquet_files) == 2

        t1: pa.Table = pq.read_table(parquet_files[0])
        t2: pa.Table = pq.read_table(parquet_files[1])
        assert t1.num_rows == 3
        assert t2.num_rows == 2

    def test_parquet_directory_structure(self, tmp_data_dir: Path) -> None:
        """Verify data/{year}/{month}/{day}/ path structure."""
        schema: pa.Schema = build_system_schema()
        metadata: dict[str, str] = {}
        ts: int = _ts_for_hour(2026, 3, 14, 10)

        writer = ParquetRotatingWriter(schema, tmp_data_dir, "telemetry_system", metadata)
        writer.write_row(_make_system_row(ts))
        writer.close()

        parquet_files = list(tmp_data_dir.rglob("*.parquet"))
        assert len(parquet_files) == 1
        expected_dir: Path = tmp_data_dir / "2026" / "03" / "14"
        assert parquet_files[0].parent == expected_dir
        assert "telemetry_system_10.parquet" == parquet_files[0].name

    def test_snappy_compression(self, tmp_data_dir: Path) -> None:
        """Verify Parquet metadata shows snappy codec."""
        schema: pa.Schema = build_system_schema()
        ts: int = _ts_for_hour(2026, 3, 14, 10)

        writer = ParquetRotatingWriter(schema, tmp_data_dir, "telemetry_system", {})
        writer.write_row(_make_system_row(ts))
        writer.close()

        parquet_files = list(tmp_data_dir.rglob("*.parquet"))
        pf = pq.ParquetFile(parquet_files[0])
        # Check row group column chunk compression
        rg_meta = pf.metadata.row_group(0)
        col_meta = rg_meta.column(0)
        assert col_meta.compression == "SNAPPY"

    def test_atomic_tmp_rename(self, tmp_data_dir: Path) -> None:
        """Verify no .tmp files after close, .parquet exists."""
        schema: pa.Schema = build_system_schema()
        ts: int = _ts_for_hour(2026, 3, 14, 10)

        writer = ParquetRotatingWriter(schema, tmp_data_dir, "telemetry_system", {})
        writer.write_row(_make_system_row(ts))

        # Before close, .tmp should exist
        tmp_files = list(tmp_data_dir.rglob("*.tmp"))
        assert len(tmp_files) == 1

        writer.close()

        # After close, no .tmp files, .parquet exists
        tmp_files = list(tmp_data_dir.rglob("*.tmp"))
        assert len(tmp_files) == 0
        parquet_files = list(tmp_data_dir.rglob("*.parquet"))
        assert len(parquet_files) == 1

    def test_topology_metadata_in_file(self, tmp_data_dir: Path) -> None:
        """Verify topology metadata readable from Parquet file."""
        schema: pa.Schema = build_cluster_schema(2, 2, 3, 2)
        metadata: dict[str, str] = build_topology_metadata(1, 2, 2, 3)
        ts: int = _ts_for_hour(2026, 3, 14, 10)

        writer = ParquetRotatingWriter(schema, tmp_data_dir, "telemetry_0", metadata)
        writer.write_row(_make_cluster_row(ts, 2, 2))
        writer.close()

        parquet_files = list(tmp_data_dir.rglob("*.parquet"))
        pf = pq.ParquetFile(parquet_files[0])
        file_meta = pf.schema_arrow.metadata
        assert file_meta[b"racks_per_cluster"] == b"2"
        assert file_meta[b"modules_per_rack"] == b"2"
        assert file_meta[b"cells_per_module"] == b"3"
        assert file_meta[b"cluster_count"] == b"1"


# ---------------------------------------------------------------------------
# TelemetryWriter Tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeStorageConfig:
    data_dir: str = "/tmp"
    parquet_retention_days: int = 90
    jsonl_retention_days: int = 180
    disk_usage_threshold_pct: int = 80
    cleanup_interval_s: int = 300


@dataclass(frozen=True)
class _FakeParquetConfig:
    compression: str = "snappy"
    rotation_period_s: int = 3600
    row_group_size: int = 3600


@dataclass(frozen=True)
class _FakeQueryConfig:
    socket: str = "ipc:///tmp/test_query.sock"
    time_series_max_rows: int = 10000
    time_series_timeout_s: int = 5
    latest_timeout_s: int = 1
    range_stats_timeout_s: int = 5
    event_log_max_rows: int = 1000
    event_log_timeout_s: int = 3
    energy_totals_timeout_s: int = 5
    cell_snapshot_timeout_s: int = 2


@dataclass(frozen=True)
class _FakeLoggerConfig:
    schema_version: str = "1.0"
    storage: _FakeStorageConfig = field(default_factory=_FakeStorageConfig)
    parquet: _FakeParquetConfig = field(default_factory=_FakeParquetConfig)
    query: _FakeQueryConfig = field(default_factory=_FakeQueryConfig)


def _send_telemetry(pub_sock: zmq.Socket, topic: str, ts_ms: int, payload: dict) -> None:
    """Send a telemetry multipart message on ZMQ PUB."""
    envelope: dict = {
        "ts": ts_ms,
        "seq": 1,
        "src": "data_manager",
        "topic": topic,
        "payload": payload,
    }
    pub_sock.send_string(topic, zmq.SNDMORE)
    pub_sock.send(msgpack.packb(envelope, use_bin_type=True))


class TestTelemetryWriter:
    """Tests for TelemetryWriter ZMQ SUB consumer."""

    @pytest.fixture
    def zmq_pair(self) -> tuple[zmq.asyncio.Context, str]:
        """Create ZMQ context and tcp endpoint for testing."""
        ctx = zmq.asyncio.Context()
        # Use tcp for testing (no /run/ems needed)
        endpoint = "tcp://127.0.0.1"
        yield ctx, endpoint
        ctx.term()

    @pytest.fixture
    def topology(self) -> dict[str, int]:
        return {
            "cluster_count": 1,
            "racks_per_cluster": 2,
            "modules_per_rack": 2,
            "cells_per_module": 3,
            "temps_per_module": 2,
        }

    @pytest.mark.asyncio
    async def test_telemetry_topic_routing(
        self, tmp_data_dir: Path, topology: dict[str, int]
    ) -> None:
        """Send mixed topics via ZMQ, verify cluster vs system file assignment."""
        ctx = zmq.asyncio.Context()
        try:
            # PUB socket (acts as data_manager)
            pub = ctx.socket(zmq.PUB)
            port = pub.bind_to_random_port("tcp://127.0.0.1")
            endpoint = f"tcp://127.0.0.1:{port}"

            config = _FakeLoggerConfig(
                storage=_FakeStorageConfig(data_dir=str(tmp_data_dir))
            )

            writer = TelemetryWriter(config, ctx, topology, endpoint=endpoint)

            # Let SUB connect
            await asyncio.sleep(0.1)

            ts = _ts_for_hour(2026, 3, 14, 10)

            # Send BMS rack topic -> should go to cluster file
            rack_payload = {
                "pack_v": 51.2, "pack_i": -12.5, "soc": 78.3, "soh": 99.1,
                "min_cell_v": 3.18, "max_cell_v": 3.25, "avg_cell_v": 3.21,
                "min_cell_t": 22.0, "max_cell_t": 28.5, "avg_cell_t": 25.2,
                "fault_code": 0, "online": 1,
            }
            _send_telemetry(pub, "bms.rack.0.0", ts, rack_payload)
            _send_telemetry(pub, "bms.rack.0.1", ts, rack_payload)

            # Send system topics -> should go to system file
            pcs_payload = {
                "ac_voltage": 230.1, "ac_current": 45.2, "active_power": 10350.0,
                "reactive_power": 120.0, "dc_voltage": 384.0, "dc_current": 27.5,
                "frequency": 50.01, "temperature": 42.3, "state": 3, "fault_code": 0,
            }
            _send_telemetry(pub, "pcs", ts, pcs_payload)

            # Collect one window
            await writer.collect_and_write_once()
            writer.close()

            # Verify cluster file exists
            cluster_files = list(tmp_data_dir.rglob("telemetry_0_*.parquet"))
            assert len(cluster_files) == 1

            # Verify system file exists
            system_files = list(tmp_data_dir.rglob("telemetry_system_*.parquet"))
            assert len(system_files) == 1

            pub.close()
        finally:
            ctx.term()

    @pytest.mark.asyncio
    async def test_telemetry_1s_buffering(
        self, tmp_data_dir: Path, topology: dict[str, int]
    ) -> None:
        """Send multiple topic messages, verify single row per second per file."""
        ctx = zmq.asyncio.Context()
        try:
            pub = ctx.socket(zmq.PUB)
            port = pub.bind_to_random_port("tcp://127.0.0.1")
            endpoint = f"tcp://127.0.0.1:{port}"

            config = _FakeLoggerConfig(
                storage=_FakeStorageConfig(data_dir=str(tmp_data_dir))
            )
            writer = TelemetryWriter(config, ctx, topology, endpoint=endpoint)
            await asyncio.sleep(0.1)

            ts = _ts_for_hour(2026, 3, 14, 10)

            # Send multiple rack messages (same window)
            rack_payload = {
                "pack_v": 51.2, "pack_i": -12.5, "soc": 78.3, "soh": 99.1,
                "min_cell_v": 3.18, "max_cell_v": 3.25, "avg_cell_v": 3.21,
                "min_cell_t": 22.0, "max_cell_t": 28.5, "avg_cell_t": 25.2,
                "fault_code": 0, "online": 1,
            }
            for r in range(2):
                _send_telemetry(pub, f"bms.rack.0.{r}", ts, rack_payload)

            await writer.collect_and_write_once()
            writer.close()

            cluster_files = list(tmp_data_dir.rglob("telemetry_0_*.parquet"))
            assert len(cluster_files) == 1
            table = pq.read_table(cluster_files[0])
            # Should be exactly 1 row (one 1-second window)
            assert table.num_rows == 1

            pub.close()
        finally:
            ctx.term()

    @pytest.mark.asyncio
    async def test_telemetry_missing_topic_fills_defaults(
        self, tmp_data_dir: Path, topology: dict[str, int]
    ) -> None:
        """Omit a topic, verify row still written with default zero values."""
        ctx = zmq.asyncio.Context()
        try:
            pub = ctx.socket(zmq.PUB)
            port = pub.bind_to_random_port("tcp://127.0.0.1")
            endpoint = f"tcp://127.0.0.1:{port}"

            config = _FakeLoggerConfig(
                storage=_FakeStorageConfig(data_dir=str(tmp_data_dir))
            )
            writer = TelemetryWriter(config, ctx, topology, endpoint=endpoint)
            await asyncio.sleep(0.1)

            ts = _ts_for_hour(2026, 3, 14, 10)

            # Only send PCS, omit meter/btms/gpio/system
            pcs_payload = {
                "ac_voltage": 230.1, "ac_current": 45.2, "active_power": 10350.0,
                "reactive_power": 120.0, "dc_voltage": 384.0, "dc_current": 27.5,
                "frequency": 50.01, "temperature": 42.3, "state": 3, "fault_code": 0,
            }
            _send_telemetry(pub, "pcs", ts, pcs_payload)

            await writer.collect_and_write_once()
            writer.close()

            system_files = list(tmp_data_dir.rglob("telemetry_system_*.parquet"))
            assert len(system_files) == 1
            table = pq.read_table(system_files[0])
            assert table.num_rows == 1

            # PCS field should have data
            assert table.column("pcs_ac_voltage")[0].as_py() == pytest.approx(230.1, abs=0.1)
            # Missing meter field should be zero/default
            assert table.column("meter_voltage")[0].as_py() == pytest.approx(0.0, abs=0.01)

            pub.close()
        finally:
            ctx.term()
