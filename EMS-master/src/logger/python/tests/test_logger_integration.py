"""Integration tests -- end-to-end telemetry and query pipeline.

Tests the full pipeline: ZMQ messages -> Parquet/JSONL on disk -> DuckDB queries
return data. Uses tcp://127.0.0.1 endpoints to avoid /run/ems dependency.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import msgpack
import pyarrow.parquet as pq
import pytest
import zmq
import zmq.asyncio

from ems_common.ipc import (
    encode_command_request,
    encode_event,
    encode_telemetry,
    decode_command_response,
)
from ems_logger.cleanup import RetentionManager, cleanup_stale_tmp
from ems_logger.config import LoggerConfig, QueryConfig, StorageConfig
from ems_logger.event_writer import EventConsumer, JsonlEventWriter
from ems_logger.query_handler import QueryServer, query_event_log, query_time_series
from ems_logger.telemetry_writer import TelemetryWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_TOPOLOGY: dict[str, int] = {
    "cluster_count": 1,
    "racks_per_cluster": 2,
    "modules_per_rack": 2,
    "cells_per_module": 3,
    "temps_per_module": 2,
}


def _random_tcp_port() -> int:
    """Return a random high TCP port for test isolation."""
    return random.randint(30000, 59999)


def _make_logger_config(data_dir: Path) -> LoggerConfig:
    """Create a LoggerConfig pointing at a temp directory."""
    return LoggerConfig(
        storage=StorageConfig(data_dir=str(data_dir)),
        query=QueryConfig(
            time_series_timeout_s=5,
            latest_timeout_s=1,
            range_stats_timeout_s=5,
            event_log_timeout_s=3,
            energy_totals_timeout_s=5,
            cell_snapshot_timeout_s=2,
        ),
    )


def _ts_ms(
    year: int, month: int, day: int,
    hour: int = 0, minute: int = 0, second: int = 0,
) -> int:
    """Build epoch-millisecond timestamp from date components."""
    dt: datetime = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Test 1: Telemetry -> Parquet -> DuckDB query
# ---------------------------------------------------------------------------

class TestTelemetryToParquetToQuery:
    """End-to-end: publish telemetry via ZMQ PUB -> TelemetryWriter writes Parquet
    -> query_time_series returns the data."""

    @pytest.fixture
    def tmp_data_dir(self, tmp_path: Path) -> Path:
        data_dir: Path = tmp_path / "data"
        data_dir.mkdir()
        return data_dir

    @pytest.mark.asyncio
    async def test_telemetry_to_parquet_to_query(self, tmp_data_dir: Path) -> None:
        """Publish system telemetry, verify Parquet files and DuckDB query results."""
        config: LoggerConfig = _make_logger_config(tmp_data_dir)
        ctx: zmq.asyncio.Context = zmq.asyncio.Context()

        pub_port: int = _random_tcp_port()
        pub_endpoint: str = f"tcp://127.0.0.1:{pub_port}"

        # Create PUB socket (simulates data_manager)
        pub_sock: zmq.asyncio.Socket = ctx.socket(zmq.PUB)
        pub_sock.bind(pub_endpoint)

        # Create TelemetryWriter with SUB connected to our PUB
        writer: TelemetryWriter = TelemetryWriter(
            config=config,
            zmq_ctx=ctx,
            topology=_TEST_TOPOLOGY,
            endpoint=pub_endpoint,
        )

        # Give ZMQ time to establish subscription
        await asyncio.sleep(0.3)

        try:
            # Publish 5 seconds of system telemetry
            base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
            for i in range(5):
                ts: int = base_ts + (i * 1000)

                # PCS telemetry
                pcs_payload: bytes = encode_telemetry(
                    timestamp_ms=ts, seq=i, source="data_manager",
                    topic="pcs",
                    payload={
                        "ac_voltage": 230.0 + i,
                        "ac_current": 45.0,
                        "active_power": 10000.0 + (i * 100),
                        "reactive_power": 0.0,
                        "dc_voltage": 384.0,
                        "dc_current": 27.0,
                        "frequency": 50.0,
                        "temperature": 40.0,
                        "state": 3,
                        "fault_code": 0,
                    },
                )
                await pub_sock.send_multipart([b"pcs", pcs_payload])

                # Meter telemetry
                meter_payload: bytes = encode_telemetry(
                    timestamp_ms=ts, seq=i, source="data_manager",
                    topic="meter",
                    payload={
                        "voltage": 230.0,
                        "current": 44.0,
                        "active_power": 9900.0,
                        "reactive_power": 0.0,
                        "frequency": 50.0,
                        "power_factor": 0.99,
                        "energy_import": 12345.0 + i,
                        "energy_export": 9876.0,
                    },
                )
                await pub_sock.send_multipart([b"meter", meter_payload])

                # Collect each second's worth
                await writer.collect_and_write_once()
                await asyncio.sleep(0.05)

            # Close writer to flush and rename .tmp -> .parquet
            writer.close()

            # Verify Parquet files exist
            parquet_files: list[Path] = list(tmp_data_dir.rglob("*.parquet"))
            assert len(parquet_files) >= 1, f"Expected Parquet files, found: {parquet_files}"

            # Verify Snappy compression
            for pf in parquet_files:
                meta = pq.read_metadata(str(pf))
                for rg_idx in range(meta.num_row_groups):
                    rg = meta.row_group(rg_idx)
                    for col_idx in range(rg.num_columns):
                        col = rg.column(col_idx)
                        assert col.compression == "SNAPPY", (
                            f"Expected SNAPPY, got {col.compression}"
                        )

            # Verify directory structure: YYYY/MM/DD/
            system_files: list[Path] = [
                f for f in parquet_files if "system" in f.name
            ]
            assert len(system_files) >= 1
            parent_names: list[str] = [p.name for p in system_files[0].parents]
            assert "14" in parent_names  # day
            assert "03" in parent_names  # month
            assert "2026" in parent_names  # year

            # Query via DuckDB
            result: dict = query_time_series(
                data_dir=tmp_data_dir,
                signals=["pcs_active_power"],
                start_ts=base_ts,
                end_ts=base_ts + 5000,
                interval_s=1,
                file_prefix="telemetry_system_*",
            )
            assert result["count"] > 0, f"Expected rows, got: {result}"
            assert "pcs_active_power" in result["columns"]

        finally:
            pub_sock.close(linger=0)
            ctx.term()


# ---------------------------------------------------------------------------
# Test 2: Events -> JSONL -> event_log query
# ---------------------------------------------------------------------------

class TestEventsToJsonlToQuery:
    """End-to-end: push events via ZMQ PUSH -> EventConsumer writes JSONL
    -> query_event_log returns the events."""

    @pytest.fixture
    def tmp_data_dir(self, tmp_path: Path) -> Path:
        data_dir: Path = tmp_path / "data"
        data_dir.mkdir()
        return data_dir

    @pytest.mark.asyncio
    async def test_events_to_jsonl_to_query(self, tmp_data_dir: Path) -> None:
        """Push events via ZMQ, verify JSONL files and event_log query."""
        config: LoggerConfig = _make_logger_config(tmp_data_dir)
        ctx: zmq.asyncio.Context = zmq.asyncio.Context()

        pull_port: int = _random_tcp_port()
        pull_endpoint: str = f"tcp://127.0.0.1:{pull_port}"

        consumer: EventConsumer = EventConsumer(
            config=config, zmq_ctx=ctx, endpoint=pull_endpoint,
        )
        consumer_task: asyncio.Task = asyncio.create_task(consumer.run())

        await asyncio.sleep(0.2)

        try:
            push_sock: zmq.asyncio.Socket = ctx.socket(zmq.PUSH)
            push_sock.connect(pull_endpoint)

            base_ts: int = int(time.time() * 1000)
            severities: list[str] = ["info", "warning", "error", "info", "critical",
                                      "info", "warning", "error", "info", "warning"]

            for i in range(10):
                data: bytes = encode_event(
                    timestamp_ms=base_ts + i,
                    source="test_module" if i < 5 else "other_module",
                    severity=severities[i],
                    event_type="test_event",
                    message=f"event message {i}",
                )
                await push_sock.send(data)

            # Wait for consumer to process
            await asyncio.sleep(0.5)

            # Shutdown consumer to flush
            consumer.close()
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

            push_sock.close(linger=0)

            # Verify JSONL files
            jsonl_files: list[Path] = list(tmp_data_dir.rglob("*.jsonl"))
            assert len(jsonl_files) >= 1, f"Expected JSONL files, found: {jsonl_files}"

            # Read all events
            all_events: list[dict] = JsonlEventWriter.read_events(jsonl_files[0])
            assert len(all_events) == 10

            # Query with severity filter
            result: dict = query_event_log(
                data_dir=tmp_data_dir,
                start_ts=base_ts - 1000,
                end_ts=base_ts + 11000,
                severity_filter="warning",
            )
            warning_count: int = sum(1 for s in severities if s == "warning")
            assert result["count"] == warning_count

            # Query with source filter
            result_src: dict = query_event_log(
                data_dir=tmp_data_dir,
                start_ts=base_ts - 1000,
                end_ts=base_ts + 11000,
                source_filter="test_module",
            )
            assert result_src["count"] == 5

        finally:
            ctx.term()


# ---------------------------------------------------------------------------
# Test 3: Crash recovery
# ---------------------------------------------------------------------------

class TestCrashRecoveryOnStartup:
    """Create .tmp files, run startup_recovery(), verify cleanup."""

    @pytest.fixture
    def tmp_data_dir(self, tmp_path: Path) -> Path:
        data_dir: Path = tmp_path / "data"
        data_dir.mkdir()
        return data_dir

    def test_crash_recovery_on_startup(self, tmp_data_dir: Path) -> None:
        """Stale .tmp files are deleted, .parquet files are untouched."""
        # Create directory structure
        day_dir: Path = tmp_data_dir / "2026" / "03" / "14"
        day_dir.mkdir(parents=True)

        # Create .tmp files (simulating crash)
        tmp1: Path = day_dir / "telemetry_system_10.tmp"
        tmp2: Path = day_dir / "telemetry_0_10.tmp"
        tmp1.write_bytes(b"partial data")
        tmp2.write_bytes(b"partial data")

        # Create a real .parquet file (should not be deleted)
        parquet_file: Path = day_dir / "telemetry_system_09.parquet"
        parquet_file.write_bytes(b"valid parquet data")

        # Run startup_recovery
        config: LoggerConfig = _make_logger_config(tmp_data_dir)
        retention: RetentionManager = RetentionManager(config)
        retention.startup_recovery()

        # Verify .tmp files deleted
        assert not tmp1.exists(), ".tmp file should be deleted"
        assert not tmp2.exists(), ".tmp file should be deleted"

        # Verify .parquet file untouched
        assert parquet_file.exists(), ".parquet file should not be deleted"


# ---------------------------------------------------------------------------
# Test 4: QueryServer round-trip via ZMQ
# ---------------------------------------------------------------------------

class TestQueryServerRoundTrip:
    """Start QueryServer, write Parquet data directly, send ZMQ REQ, verify response."""

    @pytest.fixture
    def tmp_data_dir(self, tmp_path: Path) -> Path:
        data_dir: Path = tmp_path / "data"
        data_dir.mkdir()
        return data_dir

    @pytest.mark.asyncio
    async def test_query_server_round_trip(self, tmp_data_dir: Path) -> None:
        """Write Parquet data, query via ZMQ REQ/REP, verify correct response."""
        import pyarrow as pa

        from ems_logger.parquet_schema import build_system_schema

        config: LoggerConfig = _make_logger_config(tmp_data_dir)
        ctx: zmq.asyncio.Context = zmq.asyncio.Context()

        rep_port: int = _random_tcp_port()
        rep_endpoint: str = f"tcp://127.0.0.1:{rep_port}"

        # Write Parquet data directly
        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        schema: pa.Schema = build_system_schema()

        day_dir: Path = tmp_data_dir / "2026" / "03" / "14"
        day_dir.mkdir(parents=True)
        file_path: Path = day_dir / "telemetry_system_10.parquet"

        # Build columns with correct types
        columns: dict[str, list] = {f.name: [] for f in schema}
        for i in range(3):
            row_ts: int = base_ts + (i * 1000)
            for f in schema:
                if f.name == "ts":
                    columns["ts"].append(row_ts)
                elif f.name == "pcs_active_power":
                    columns[f.name].append(5000.0 + (i * 500))
                elif pa.types.is_list(f.type):
                    columns[f.name].append([0] * 8)
                elif pa.types.is_floating(f.type):
                    columns[f.name].append(0.0)
                else:
                    columns[f.name].append(0)

        table: pa.Table = pa.table(columns, schema=schema)
        pq.write_table(table, file_path, compression="snappy")

        # Start QueryServer
        server: QueryServer = QueryServer(config, ctx, rep_endpoint)
        server_task: asyncio.Task = asyncio.create_task(server.run())

        await asyncio.sleep(0.2)

        try:
            # Send REQ
            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(rep_endpoint)

            request: bytes = encode_command_request("query", {
                "type": "time_series",
                "signals": ["pcs_active_power"],
                "start_ts": base_ts,
                "end_ts": base_ts + 3000,
                "interval_s": 1,
                "file_prefix": "telemetry_system_*",
            })
            await req_sock.send(request)
            response_data: bytes = await asyncio.wait_for(
                req_sock.recv(), timeout=5.0,
            )

            resp: dict = decode_command_response(response_data)
            assert resp["status"] == "ok", f"Expected ok, got: {resp}"
            assert resp["result"]["count"] == 3
            assert "pcs_active_power" in resp["result"]["columns"]

            # Verify values
            rows: list = resp["result"]["rows"]
            power_col_idx: int = resp["result"]["columns"].index("pcs_active_power")
            powers: list[float] = [r[power_col_idx] for r in rows]
            assert powers[0] == pytest.approx(5000.0, abs=1.0)
            assert powers[2] == pytest.approx(6000.0, abs=1.0)

            req_sock.close(linger=0)

        finally:
            server.close()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()
