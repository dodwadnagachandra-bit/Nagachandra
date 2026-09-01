"""Tests for DuckDB query handler -- 6 predefined query types and error handling.

Covers time_series, latest, range_stats, event_log, energy_totals, cell_snapshot
query functions using temporary Parquet/JSONL files, plus signal validation and
row limit enforcement.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ems_logger.parquet_schema import build_cluster_schema, build_system_schema


# ---------------------------------------------------------------------------
# Helpers -- write sample Parquet/JSONL data for testing
# ---------------------------------------------------------------------------

def _ts_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> int:
    """Build epoch-millisecond timestamp from date components."""
    dt: datetime = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _write_system_parquet(
    data_dir: Path,
    rows: list[dict[str, Any]],
    year: int = 2026,
    month: int = 3,
    day: int = 14,
    hour: int = 10,
) -> Path:
    """Write a system Parquet file at the expected path."""
    schema: pa.Schema = build_system_schema()
    directory: Path = data_dir / str(year) / f"{month:02d}" / f"{day:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    file_path: Path = directory / f"system_{hour:02d}.parquet"

    # Build table from rows, filling defaults for missing fields
    columns: dict[str, list] = {f.name: [] for f in schema}
    for row in rows:
        for f in schema:
            if f.name in row:
                columns[f.name].append(row[f.name])
            elif pa.types.is_list(f.type):
                # LIST columns need a list default
                columns[f.name].append([0] * 8)
            else:
                columns[f.name].append(0)

    table: pa.Table = pa.table(columns, schema=schema)
    pq.write_table(table, file_path, compression="snappy")
    return file_path


def _write_cluster_parquet(
    data_dir: Path,
    rows: list[dict[str, Any]],
    cluster: int = 0,
    year: int = 2026,
    month: int = 3,
    day: int = 14,
    hour: int = 10,
    racks_per_cluster: int = 2,
    modules_per_rack: int = 2,
    cells_per_module: int = 4,
    temps_per_module: int = 2,
) -> Path:
    """Write a cluster Parquet file at the expected path."""
    schema: pa.Schema = build_cluster_schema(
        racks_per_cluster, modules_per_rack, cells_per_module, temps_per_module
    )
    directory: Path = data_dir / str(year) / f"{month:02d}" / f"{day:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    file_path: Path = directory / f"cluster_{cluster}_{hour:02d}.parquet"

    columns: dict[str, list] = {f.name: [] for f in schema}
    for row in rows:
        for f in schema:
            if f.name in row:
                columns[f.name].append(row[f.name])
            elif pa.types.is_list(f.type):
                # LIST columns (mod*_cell_v, mod*_cell_t) need list defaults
                if "cell_v" in f.name:
                    columns[f.name].append([3.2] * cells_per_module)
                else:
                    columns[f.name].append([25.0] * temps_per_module)
            elif f.name == "ts":
                columns[f.name].append(row.get("ts", 0))
            else:
                columns[f.name].append(0)

    table: pa.Table = pa.table(columns, schema=schema)
    pq.write_table(table, file_path, compression="snappy")
    return file_path


def _write_jsonl_events(
    data_dir: Path,
    events: list[dict[str, Any]],
    year: int = 2026,
    month: int = 3,
    day: int = 14,
) -> Path:
    """Write JSONL event file at the expected path."""
    directory: Path = data_dir / "events" / str(year) / f"{month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    file_path: Path = directory / f"events_{year}{month:02d}{day:02d}.jsonl"

    with open(file_path, "w") as f:
        for evt in events:
            f.write(json.dumps(evt, separators=(",", ":")) + "\n")
    return file_path


# ---------------------------------------------------------------------------
# Test: time_series query
# ---------------------------------------------------------------------------

class TestQueryTimeSeries:
    """Tests for query_time_series function."""

    def test_duckdb_time_series_query(self, tmp_data_dir: Path) -> None:
        """Time series query returns downsampled data within time range."""
        from ems_logger.query_handler import query_time_series

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        rows: list[dict[str, Any]] = []
        for i in range(60):
            rows.append({
                "ts": base_ts + i * 1000,
                "pcs_active_power": float(100 + i),
                "pcs_dc_voltage": 384.0,
            })
        _write_system_parquet(tmp_data_dir, rows)

        result: dict = query_time_series(
            data_dir=tmp_data_dir,
            signals=["pcs_active_power"],
            start_ts=base_ts,
            end_ts=base_ts + 59 * 1000,
            interval_s=10,
            file_prefix="system_*",
        )

        assert result["count"] > 0
        assert result["count"] <= 10  # 60s / 10s = ~6 buckets
        assert "pcs_active_power" in result["columns"]
        assert len(result["rows"]) == result["count"]


# ---------------------------------------------------------------------------
# Test: latest query
# ---------------------------------------------------------------------------

class TestQueryLatest:
    """Tests for query_latest function."""

    def test_duckdb_latest_query(self, tmp_data_dir: Path) -> None:
        """Latest query returns most recent row."""
        from ems_logger.query_handler import query_latest

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        rows: list[dict[str, Any]] = [
            {"ts": base_ts, "pcs_active_power": 100.0},
            {"ts": base_ts + 5000, "pcs_active_power": 200.0},
            {"ts": base_ts + 10000, "pcs_active_power": 300.0},
        ]
        _write_system_parquet(tmp_data_dir, rows)

        result: dict = query_latest(
            data_dir=tmp_data_dir,
            signals=["pcs_active_power"],
            file_prefix="system_*",
        )

        assert result["count"] == 1
        assert len(result["rows"]) == 1
        # Most recent row should have pcs_active_power = 300.0
        row: list = result["rows"][0]
        power_idx: int = result["columns"].index("pcs_active_power")
        assert row[power_idx] == pytest.approx(300.0, abs=0.1)


# ---------------------------------------------------------------------------
# Test: range_stats query
# ---------------------------------------------------------------------------

class TestQueryRangeStats:
    """Tests for query_range_stats function."""

    def test_duckdb_range_stats(self, tmp_data_dir: Path) -> None:
        """Range stats returns min/max/avg/count per signal."""
        from ems_logger.query_handler import query_range_stats

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        rows: list[dict[str, Any]] = [
            {"ts": base_ts, "pcs_active_power": 100.0},
            {"ts": base_ts + 1000, "pcs_active_power": 200.0},
            {"ts": base_ts + 2000, "pcs_active_power": 300.0},
        ]
        _write_system_parquet(tmp_data_dir, rows)

        result: dict = query_range_stats(
            data_dir=tmp_data_dir,
            signals=["pcs_active_power"],
            start_ts=base_ts,
            end_ts=base_ts + 3000,
            file_prefix="system_*",
        )

        assert result["count"] == 1  # one row per signal
        row: list = result["rows"][0]
        cols: list[str] = result["columns"]
        assert "signal" in cols
        assert "min_val" in cols
        assert "max_val" in cols
        assert "avg_val" in cols
        assert "row_count" in cols

        sig_idx: int = cols.index("signal")
        assert row[sig_idx] == "pcs_active_power"
        min_idx: int = cols.index("min_val")
        max_idx: int = cols.index("max_val")
        avg_idx: int = cols.index("avg_val")
        assert row[min_idx] == pytest.approx(100.0, abs=0.1)
        assert row[max_idx] == pytest.approx(300.0, abs=0.1)
        assert row[avg_idx] == pytest.approx(200.0, abs=0.1)


# ---------------------------------------------------------------------------
# Test: event_log query
# ---------------------------------------------------------------------------

class TestQueryEventLog:
    """Tests for query_event_log function."""

    def test_duckdb_event_log_query(self, tmp_data_dir: Path) -> None:
        """Event log query reads JSONL and applies filters."""
        from ems_logger.query_handler import query_event_log

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        events: list[dict[str, Any]] = [
            {"ts": base_ts, "src": "safety_manager", "severity": "warning",
             "event_type": "comm_fault", "message": "BMS timeout"},
            {"ts": base_ts + 1000, "src": "comm_manager", "severity": "error",
             "event_type": "device_offline", "message": "PCS offline"},
            {"ts": base_ts + 2000, "src": "safety_manager", "severity": "info",
             "event_type": "reset", "message": "Manual reset"},
        ]
        _write_jsonl_events(tmp_data_dir, events)

        # Filter by severity=warning
        result: dict = query_event_log(
            data_dir=tmp_data_dir,
            start_ts=base_ts,
            end_ts=base_ts + 3000,
            severity_filter="warning",
        )

        assert result["count"] == 1
        assert result["rows"][0]["severity"] == "warning"

    def test_event_log_source_filter(self, tmp_data_dir: Path) -> None:
        """Event log respects source_filter."""
        from ems_logger.query_handler import query_event_log

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        events: list[dict[str, Any]] = [
            {"ts": base_ts, "src": "safety_manager", "severity": "warning",
             "event_type": "a", "message": "x"},
            {"ts": base_ts + 1000, "src": "comm_manager", "severity": "warning",
             "event_type": "b", "message": "y"},
        ]
        _write_jsonl_events(tmp_data_dir, events)

        result: dict = query_event_log(
            data_dir=tmp_data_dir,
            start_ts=base_ts,
            end_ts=base_ts + 2000,
            source_filter="comm_manager",
        )

        assert result["count"] == 1
        assert result["rows"][0]["src"] == "comm_manager"


# ---------------------------------------------------------------------------
# Test: energy_totals query
# ---------------------------------------------------------------------------

class TestQueryEnergyTotals:
    """Tests for query_energy_totals function."""

    def test_duckdb_energy_totals(self, tmp_data_dir: Path) -> None:
        """Energy totals returns charge/discharge and import/export sums."""
        from ems_logger.query_handler import query_energy_totals

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        rows: list[dict[str, Any]] = [
            {"ts": base_ts, "pcs_active_power": 100.0,
             "meter_energy_import": 1000.0, "meter_energy_export": 500.0},
            {"ts": base_ts + 1000, "pcs_active_power": -50.0,
             "meter_energy_import": 1001.0, "meter_energy_export": 500.0},
            {"ts": base_ts + 2000, "pcs_active_power": 200.0,
             "meter_energy_import": 1003.0, "meter_energy_export": 501.0},
        ]
        _write_system_parquet(tmp_data_dir, rows)

        result: dict = query_energy_totals(
            data_dir=tmp_data_dir,
            start_ts=base_ts,
            end_ts=base_ts + 3000,
        )

        assert result["count"] == 1
        row: list = result["rows"][0]
        cols: list[str] = result["columns"]

        # discharge = sum of positive power (100+200=300), charge = abs(sum of negative)=50
        assert "discharge_wh" in cols
        assert "charge_wh" in cols
        assert "grid_import_wh" in cols
        assert "grid_export_wh" in cols


# ---------------------------------------------------------------------------
# Test: cell_snapshot query
# ---------------------------------------------------------------------------

class TestQueryCellSnapshot:
    """Tests for query_cell_snapshot function."""

    def test_duckdb_cell_snapshot(self, tmp_data_dir: Path) -> None:
        """Cell snapshot returns cell voltages and temps for a specific rack."""
        from ems_logger.query_handler import query_cell_snapshot

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        rows: list[dict[str, Any]] = [
            {
                "ts": base_ts,
                "rack0_pack_v": 51.2,
                "rack0_soc": 78.0,
                "rack0_mod0_cell_v": [3.20, 3.21, 3.22, 3.23],
                "rack0_mod0_cell_t": [24.0, 25.0],
                "rack0_mod1_cell_v": [3.18, 3.19, 3.20, 3.21],
                "rack0_mod1_cell_t": [23.0, 24.0],
                "rack1_pack_v": 50.8,
                "rack1_soc": 76.0,
                "rack1_mod0_cell_v": [3.15, 3.16, 3.17, 3.18],
                "rack1_mod0_cell_t": [22.0, 23.0],
                "rack1_mod1_cell_v": [3.14, 3.15, 3.16, 3.17],
                "rack1_mod1_cell_t": [21.0, 22.0],
            },
        ]
        _write_cluster_parquet(tmp_data_dir, rows, cluster=0)

        result: dict = query_cell_snapshot(
            data_dir=tmp_data_dir,
            cluster=0,
            rack=0,
            timestamp=base_ts,
        )

        assert result["count"] == 1
        # Should return cell_v and cell_t LIST columns for rack 0's modules
        cols: list[str] = result["columns"]
        assert any("cell_v" in c for c in cols)
        assert any("cell_t" in c for c in cols)


# ---------------------------------------------------------------------------
# Test: validation and limits
# ---------------------------------------------------------------------------

class TestQueryValidation:
    """Tests for signal allowlist and row limit enforcement."""

    def test_query_invalid_signal_rejected(self, tmp_data_dir: Path) -> None:
        """Unknown column names are rejected."""
        from ems_logger.query_handler import query_latest, QueryError

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        _write_system_parquet(tmp_data_dir, [{"ts": base_ts}])

        with pytest.raises(QueryError, match="Invalid signal"):
            query_latest(
                data_dir=tmp_data_dir,
                signals=["DROP TABLE users; --"],
                file_prefix="system_*",
            )

    def test_query_row_limit_enforced(self, tmp_data_dir: Path) -> None:
        """Queries exceeding row limit return error."""
        from ems_logger.query_handler import query_time_series, QueryError

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        # Write enough rows that a 1s interval won't downsample enough
        rows: list[dict[str, Any]] = []
        for i in range(100):
            rows.append({
                "ts": base_ts + i * 1000,
                "pcs_active_power": float(i),
            })
        _write_system_parquet(tmp_data_dir, rows)

        # Use max_rows=5 override to test limit enforcement
        with pytest.raises(QueryError, match="row limit"):
            query_time_series(
                data_dir=tmp_data_dir,
                signals=["pcs_active_power"],
                start_ts=base_ts,
                end_ts=base_ts + 99 * 1000,
                interval_s=1,
                file_prefix="system_*",
                max_rows=5,
            )


# ---------------------------------------------------------------------------
# Test: QueryServer ZMQ REP dispatch
# ---------------------------------------------------------------------------

class TestQueryServer:
    """Tests for QueryServer ZMQ REP dispatcher."""

    @pytest.fixture
    def logger_config(self, tmp_data_dir: Path) -> "LoggerConfig":
        """Create a LoggerConfig pointing at tmp data dir."""
        from ems_logger.config import LoggerConfig, StorageConfig, QueryConfig

        return LoggerConfig(
            storage=StorageConfig(data_dir=str(tmp_data_dir)),
            query=QueryConfig(
                time_series_timeout_s=5,
                latest_timeout_s=1,
                range_stats_timeout_s=5,
                event_log_timeout_s=3,
                energy_totals_timeout_s=5,
                cell_snapshot_timeout_s=2,
            ),
        )

    @pytest.mark.asyncio
    async def test_query_server_dispatch(
        self, tmp_data_dir: Path, logger_config: "LoggerConfig"
    ) -> None:
        """Send REQ with time_series type, verify response."""
        import zmq.asyncio
        from ems_common.ipc import encode_command_request, decode_command_response
        from ems_logger.query_handler import QueryServer

        base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
        rows: list[dict[str, Any]] = [
            {"ts": base_ts, "pcs_active_power": 100.0},
            {"ts": base_ts + 1000, "pcs_active_power": 200.0},
        ]
        _write_system_parquet(tmp_data_dir, rows)

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        endpoint: str = f"ipc://{tmp_data_dir}/test_query.sock"

        server: QueryServer = QueryServer(logger_config, ctx, endpoint)
        import asyncio
        server_task: asyncio.Task = asyncio.create_task(server.run())

        try:
            # Send REQ
            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(endpoint)

            request: bytes = encode_command_request("query", {
                "type": "time_series",
                "signals": ["pcs_active_power"],
                "start_ts": base_ts,
                "end_ts": base_ts + 2000,
                "interval_s": 1,
                "file_prefix": "system_*",
            })
            await req_sock.send(request)
            response_data: bytes = await asyncio.wait_for(
                req_sock.recv(), timeout=5.0
            )

            resp: dict = decode_command_response(response_data)
            assert resp["status"] == "ok"
            assert resp["result"]["count"] > 0
            assert "pcs_active_power" in resp["result"]["columns"]

            req_sock.close(linger=0)
        finally:
            server.close()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()

    @pytest.mark.asyncio
    async def test_query_server_unknown_type(
        self, tmp_data_dir: Path, logger_config: "LoggerConfig"
    ) -> None:
        """Send unknown query type, verify error response."""
        import zmq.asyncio
        from ems_common.ipc import encode_command_request, decode_command_response
        from ems_logger.query_handler import QueryServer

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        endpoint: str = f"ipc://{tmp_data_dir}/test_query_unk.sock"

        server: QueryServer = QueryServer(logger_config, ctx, endpoint)
        import asyncio
        server_task: asyncio.Task = asyncio.create_task(server.run())

        try:
            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(endpoint)

            request: bytes = encode_command_request("query", {
                "type": "nonexistent_type",
            })
            await req_sock.send(request)
            response_data: bytes = await asyncio.wait_for(
                req_sock.recv(), timeout=5.0
            )

            resp: dict = decode_command_response(response_data)
            assert resp["status"] == "error"
            assert "Unknown query type" in resp["error_msg"]

            req_sock.close(linger=0)
        finally:
            server.close()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()

    @pytest.mark.asyncio
    async def test_query_server_timeout_enforcement(
        self, tmp_data_dir: Path
    ) -> None:
        """Slow query mock triggers timeout error response."""
        import zmq.asyncio
        from unittest.mock import patch, AsyncMock
        from ems_common.ipc import encode_command_request, decode_command_response
        from ems_logger.config import LoggerConfig, StorageConfig, QueryConfig
        from ems_logger.query_handler import QueryServer

        # Use very short timeout
        config: LoggerConfig = LoggerConfig(
            storage=StorageConfig(data_dir=str(tmp_data_dir)),
            query=QueryConfig(latest_timeout_s=0),  # 0s timeout
        )

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        endpoint: str = f"ipc://{tmp_data_dir}/test_query_timeout.sock"

        server: QueryServer = QueryServer(config, ctx, endpoint)
        import asyncio
        server_task: asyncio.Task = asyncio.create_task(server.run())

        try:
            req_sock: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req_sock.connect(endpoint)

            # Write a parquet file so the query has something to read
            base_ts: int = _ts_ms(2026, 3, 14, 10, 0, 0)
            _write_system_parquet(tmp_data_dir, [{"ts": base_ts, "pcs_active_power": 1.0}])

            # Patch the dispatch to simulate a slow query
            original_dispatch = server._dispatch

            async def slow_dispatch(action: str, params: dict) -> dict:
                await asyncio.sleep(5.0)  # Much longer than timeout
                return await original_dispatch(action, params)

            with patch.object(server, "_dispatch", side_effect=slow_dispatch):
                request: bytes = encode_command_request("query", {
                    "type": "latest",
                    "signals": ["pcs_active_power"],
                    "file_prefix": "system_*",
                })
                await req_sock.send(request)
                response_data: bytes = await asyncio.wait_for(
                    req_sock.recv(), timeout=5.0
                )

                resp: dict = decode_command_response(response_data)
                assert resp["status"] == "error"
                assert "timed out" in resp["error_msg"].lower()

            req_sock.close(linger=0)
        finally:
            server.close()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()
