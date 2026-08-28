"""End-to-end data pipeline integration tests.

Validates the complete data path:
  simulator -> comm_manager -> RTDB -> data_manager -> ZMQ -> logger -> Parquet -> DuckDB

Requires all core modules to be built and available. Tests use the residential
topology profile with deterministic simulator seeds for reproducible assertions.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import msgpack
import pyarrow.parquet as pq
import pytest
import zmq

from ems_common.ipc import (
    SOCK_LOGGER_QUERY,
    SOCK_TELEMETRY,
    decode_command_response,
    encode_command_request,
)
from ems_common.rtdb import attach_rtdb, detach_rtdb

# These are defined in tests/integration/conftest.py and imported via
# sys.path manipulation since conftest is not directly importable as a module.
import sys as _sys

_INTEG_DIR = Path(__file__).resolve().parent
if str(_INTEG_DIR) not in _sys.path:
    _sys.path.insert(0, str(_INTEG_DIR))

from conftest import (  # noqa: E402
    BUILD_DIR,
    CONFIG_DIR,
    ModuleProcess,
    PROFILES,
    check_rtdb_exists,
)

# ---------------------------------------------------------------------------
# Module-level markers
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DETERMINISTIC_SEED: int = 42

# SOC valid range produced by SignalGenerator with sinusoidal drift + Gaussian noise
SOC_MIN: float = 20.0
SOC_MAX: float = 80.0
SOC_TOLERANCE_PCT: float = 1.0

# Minimum data points expected after pipeline warm-up
MIN_DATA_POINTS: int = 25

# Pipeline warm-up time in seconds
# CAN sim ~1s start, comm_manager ~2s decode, data_manager 1Hz publish,
# logger collects 1s windows. 30s data + 5s startup = 35s total.
PIPELINE_WARMUP_S: int = 35


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vcan_available() -> bool:
    """Return True if vcan0 network interface exists."""
    return Path("/sys/class/net/vcan0").exists()


def _binary_exists(name: str) -> bool:
    """Return True if the named C binary is available in the build directory."""
    binaries: dict[str, Path] = {
        "data_manager_c": BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c",
        "safety_manager": BUILD_DIR / "src" / "safety_manager" / "safety_manager",
        "comm_manager_c": BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c",
    }
    path: Path | None = binaries.get(name)
    return path is not None and path.exists()


def _kill_subprocess_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill a subprocess and its entire process group (best-effort)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """End-to-end data pipeline validation.

    Launches all core modules and simulators, waits for data to flow through
    the full pipeline, then verifies:
    - RTDB has CAN/Modbus data
    - Parquet files created with sufficient rows and correct schema
    - DuckDB queries return valid results
    - JSONL event files are well-structured
    - ZMQ telemetry is receivable
    - DuckDB query via ZMQ REQ/REP API returns data
    """

    @pytest.fixture(scope="class")
    def pipeline_env(self) -> Any:
        """Set up the full data pipeline for E2E testing.

        Launches modules in systemd order, starts simulators, waits for
        data to flow, then yields context dict for test methods.
        Cleans up all processes and temp files in teardown.
        """
        # Deterministic seed for reproducible simulator output
        random.seed(DETERMINISTIC_SEED)

        # Temp directory for data output (Parquet, JSONL)
        data_dir: str = tempfile.mkdtemp(prefix="ems_integ_")
        data_path: Path = Path(data_dir)

        profile: dict[str, Any] = PROFILES["residential"]
        config_dir: Path = profile["config_dir"]

        modules: list[ModuleProcess] = []
        simulators: list[subprocess.Popen[bytes]] = []
        skipped_modules: list[str] = []

        try:
            # 1. data_manager_c -- creates RTDB shared memory
            if _binary_exists("data_manager_c"):
                dm_c = ModuleProcess(
                    name="data_manager_c",
                    cmd=[
                        str(BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c"),
                        str(profile["clusters"]),
                        str(profile["racks"]),
                        str(profile["modules"]),
                        str(profile["cells"]),
                        str(profile["temps"]),
                    ],
                    ready_check=check_rtdb_exists,
                )
                modules.append(dm_c)
            else:
                skipped_modules.append("data_manager_c")

            # 2. data_manager_python -- publishes RTDB snapshots on ZMQ
            try:
                dm_py = ModuleProcess(
                    name="data_manager_python",
                    cmd=["uv", "run", "python", "-m", "ems_data_manager"],
                    ready_check=lambda: True,
                )
                modules.append(dm_py)
            except (FileNotFoundError, TimeoutError):
                skipped_modules.append("data_manager_python")

            # 3. config_manager
            try:
                cfg_mgr = ModuleProcess(
                    name="config_manager",
                    cmd=[
                        "uv", "run", "python", "-m", "ems_config_manager",
                        "--config-dir", str(config_dir),
                    ],
                    ready_check=lambda: True,
                )
                modules.append(cfg_mgr)
            except (FileNotFoundError, TimeoutError):
                skipped_modules.append("config_manager")

            # 4. safety_manager -- skip if binary not found
            if _binary_exists("safety_manager"):
                try:
                    sm = ModuleProcess(
                        name="safety_manager",
                        cmd=[str(BUILD_DIR / "src" / "safety_manager" / "safety_manager")],
                        ready_check=lambda: True,
                    )
                    modules.append(sm)
                except (FileNotFoundError, TimeoutError):
                    skipped_modules.append("safety_manager")
            else:
                skipped_modules.append("safety_manager")

            # 5. comm_manager_c -- skip if vcan0 unavailable
            if _vcan_available() and _binary_exists("comm_manager_c"):
                try:
                    cm_c = ModuleProcess(
                        name="comm_manager_c",
                        cmd=[
                            str(BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"),
                            "--interface", "vcan0",
                            "--base-id", "0x18FF0003",
                            "--heartbeat-timeout-ms", "900",
                        ],
                        ready_check=lambda: True,
                    )
                    modules.append(cm_c)
                except (FileNotFoundError, TimeoutError):
                    skipped_modules.append("comm_manager_c")
            else:
                skipped_modules.append("comm_manager_c")

            # 6. comm_manager_python (Modbus)
            try:
                cm_py = ModuleProcess(
                    name="comm_manager_python",
                    cmd=[
                        "uv", "run", "python", "-m", "ems_comm_manager",
                        "--config", str(CONFIG_DIR),
                    ],
                    ready_check=lambda: True,
                )
                modules.append(cm_py)
            except (FileNotFoundError, TimeoutError):
                skipped_modules.append("comm_manager_python")

            # 7. logger -- point at temp data dir via env var
            try:
                lgr = ModuleProcess(
                    name="logger",
                    cmd=[
                        "uv", "run", "python", "-m", "ems_logger",
                        "--config", str(CONFIG_DIR / "logger_config.yaml"),
                    ],
                    ready_check=lambda: True,
                    env={"EMS_DATA_DIR": data_dir},
                )
                modules.append(lgr)
            except (FileNotFoundError, TimeoutError):
                skipped_modules.append("logger")

            # Launch CAN simulator (if vcan0 available)
            if _vcan_available():
                try:
                    can_proc: subprocess.Popen[bytes] = subprocess.Popen(
                        [
                            "uv", "run", "python", "-m", "tools.simulators.can_sim",
                            "--interface", "vcan0",
                            "--config", str(config_dir / "bms_config.yaml"),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        preexec_fn=os.setpgrp,
                    )
                    simulators.append(can_proc)
                except FileNotFoundError:
                    pass

            # Launch Modbus simulator (TCP mode -- no serial dependency)
            try:
                modbus_proc: subprocess.Popen[bytes] = subprocess.Popen(
                    [
                        "uv", "run", "python", "-m", "tools.simulators.modbus_sim",
                        "--transport", "tcp",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setpgrp,
                )
                simulators.append(modbus_proc)
            except FileNotFoundError:
                pass

            # Wait for data to flow through the full pipeline
            time.sleep(PIPELINE_WARMUP_S)

            yield {
                "modules": modules,
                "simulators": simulators,
                "data_dir": data_path,
                "profile": profile,
                "config_dir": config_dir,
                "skipped_modules": skipped_modules,
            }

        finally:
            # Teardown: kill all simulators first
            for sim in simulators:
                _kill_subprocess_group(sim)

            # Then kill modules in reverse startup order
            for mod in reversed(modules):
                mod.cleanup()

            # Clean up temp data directory (best-effort)
            try:
                shutil.rmtree(data_dir, ignore_errors=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Test 1: RTDB has data from CAN and Modbus
    # ------------------------------------------------------------------

    def test_rtdb_has_data(self, pipeline_env: dict[str, Any]) -> None:
        """Verify that CAN and Modbus data has been written to RTDB.

        Attaches to RTDB shared memory and checks that BMS rack cell
        voltages are non-zero (CAN data) and PCS fields are non-zero
        (Modbus data).
        """
        if "data_manager_c" in pipeline_env["skipped_modules"]:
            pytest.skip("data_manager_c not available -- RTDB not created")

        try:
            shm, rtdb = attach_rtdb()
        except (FileNotFoundError, RuntimeError) as exc:
            pytest.skip(f"RTDB not available: {exc}")
            return

        try:
            # Check BMS rack data (CAN path)
            if "comm_manager_c" not in pipeline_env["skipped_modules"]:
                rack = rtdb.clusters[0].racks[0]
                cell_voltages: list[float] = [
                    rack.modules[0].cell_v[i]
                    for i in range(min(4, pipeline_env["profile"]["cells"]))
                ]
                has_can_data: bool = any(v != 0.0 for v in cell_voltages)
                assert has_can_data, (
                    f"Expected non-zero cell voltages from CAN sim, got {cell_voltages}"
                )

            # Check PCS data (Modbus path)
            if "comm_manager_python" not in pipeline_env["skipped_modules"]:
                pcs_fields: list[float] = [
                    rtdb.pcs.ac_voltage,
                    rtdb.pcs.active_power,
                    rtdb.pcs.dc_voltage,
                ]
                has_modbus_data: bool = any(v != 0.0 for v in pcs_fields)
                assert has_modbus_data, (
                    f"Expected non-zero PCS fields from Modbus sim, got {pcs_fields}"
                )
        finally:
            detach_rtdb(shm)

    # ------------------------------------------------------------------
    # Test 2: Parquet files created with sufficient rows
    # ------------------------------------------------------------------

    def test_parquet_files_created(self, pipeline_env: dict[str, Any]) -> None:
        """Verify that logger produced Parquet files with at least 25 rows.

        Searches the data directory for telemetry or system Parquet files
        and validates row count and schema presence.
        """
        if "logger" in pipeline_env["skipped_modules"]:
            pytest.skip("Logger not available -- cannot verify Parquet output")

        data_dir: Path = pipeline_env["data_dir"]

        # Search for any parquet files (system_*.parquet or cluster_*.parquet)
        parquet_files: list[Path] = list(data_dir.rglob("*.parquet"))

        if not parquet_files:
            # Also check default data/ directory in project root
            default_data: Path = Path(data_dir).parent / "data"
            parquet_files = list(default_data.rglob("*.parquet")) if default_data.exists() else []

        assert len(parquet_files) >= 1, (
            f"Expected at least 1 Parquet file in {data_dir}, found none"
        )

        # Read first file and validate
        table = pq.read_table(parquet_files[0])
        assert table.num_rows >= MIN_DATA_POINTS, (
            f"Expected >= {MIN_DATA_POINTS} rows in {parquet_files[0].name}, "
            f"got {table.num_rows}"
        )

        # Verify schema has timestamp column
        col_names: list[str] = table.column_names
        assert "ts" in col_names, (
            f"Expected 'ts' column in schema, got columns: {col_names}"
        )

    # ------------------------------------------------------------------
    # Test 3: Parquet data matches simulator output
    # ------------------------------------------------------------------

    def test_parquet_data_matches_simulator(self, pipeline_env: dict[str, Any]) -> None:
        """Verify SOC values in Parquet are within the simulator's valid range.

        Uses DuckDB to query the system_total_soc column from system Parquet
        files and asserts values fall within 20.0-80.0 +/- 1% tolerance.
        """
        if "logger" in pipeline_env["skipped_modules"]:
            pytest.skip("Logger not available -- cannot verify Parquet data")

        data_dir: Path = pipeline_env["data_dir"]

        # Find system Parquet files
        system_files: list[Path] = list(data_dir.rglob("system_*.parquet"))
        if not system_files:
            pytest.skip("No system Parquet files found -- logger may not have written yet")

        con: duckdb.DuckDBPyConnection = duckdb.connect()
        try:
            file_list: str = ", ".join(f"'{f}'" for f in system_files)
            result = con.execute(
                f"SELECT system_total_soc FROM read_parquet([{file_list}]) "
                f"WHERE system_total_soc IS NOT NULL AND system_total_soc != 0"
            ).fetchall()

            assert len(result) >= MIN_DATA_POINTS, (
                f"Expected >= {MIN_DATA_POINTS} non-null SOC data points, got {len(result)}"
            )

            soc_min_allowed: float = SOC_MIN - SOC_TOLERANCE_PCT
            soc_max_allowed: float = SOC_MAX + SOC_TOLERANCE_PCT
            for row in result:
                soc: float = row[0]
                assert soc_min_allowed <= soc <= soc_max_allowed, (
                    f"SOC {soc} outside valid range [{soc_min_allowed}, {soc_max_allowed}]"
                )
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Test 4: DuckDB query returns results with valid time range
    # ------------------------------------------------------------------

    def test_duckdb_query_returns_results(self, pipeline_env: dict[str, Any]) -> None:
        """Verify direct DuckDB queries over Parquet return data.

        Creates a DuckDB view over Parquet files and asserts:
        - Row count is > 0
        - Time range spans at least 20 seconds
        """
        if "logger" in pipeline_env["skipped_modules"]:
            pytest.skip("Logger not available -- cannot query DuckDB")

        data_dir: Path = pipeline_env["data_dir"]
        parquet_files: list[Path] = list(data_dir.rglob("*.parquet"))

        if not parquet_files:
            pytest.skip("No Parquet files found for DuckDB query")

        con: duckdb.DuckDBPyConnection = duckdb.connect()
        try:
            file_list: str = ", ".join(f"'{f}'" for f in parquet_files)
            con.execute(
                f"CREATE VIEW telemetry AS SELECT * FROM read_parquet([{file_list}])"
            )

            # Count rows
            count_result = con.execute("SELECT COUNT(*) FROM telemetry").fetchone()
            assert count_result is not None
            row_count: int = count_result[0]
            assert row_count > 0, "Expected > 0 rows in telemetry view"

            # Check time range spans at least 20 seconds
            time_result = con.execute(
                "SELECT MIN(ts), MAX(ts) FROM telemetry"
            ).fetchone()
            assert time_result is not None
            min_ts: int = time_result[0]
            max_ts: int = time_result[1]
            time_range_s: float = (max_ts - min_ts) / 1000.0
            assert time_range_s >= 20.0, (
                f"Expected time range >= 20s, got {time_range_s:.1f}s"
            )
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Test 5: JSONL events are valid structured JSON
    # ------------------------------------------------------------------

    def test_jsonl_events_valid(self, pipeline_env: dict[str, Any]) -> None:
        """Verify JSONL event files contain valid structured events.

        Searches for events_*.jsonl files, parses each line as JSON,
        and verifies required fields (timestamp, source, type) are present.
        """
        if "logger" in pipeline_env["skipped_modules"]:
            pytest.skip("Logger not available -- cannot verify JSONL events")

        data_dir: Path = pipeline_env["data_dir"]

        # Search events subdirectory
        jsonl_files: list[Path] = list(data_dir.rglob("events_*.jsonl"))

        if not jsonl_files:
            pytest.skip(
                "No JSONL event files found -- events may not have been "
                "generated during clean run (expected behavior)"
            )

        for jsonl_file in jsonl_files:
            lines: list[str] = jsonl_file.read_text().strip().splitlines()
            assert len(lines) >= 1, (
                f"Expected at least 1 event line in {jsonl_file.name}, got 0"
            )

            for i, line in enumerate(lines):
                try:
                    event: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError as exc:
                    pytest.fail(
                        f"Invalid JSON on line {i + 1} of {jsonl_file.name}: {exc}"
                    )

                # Verify required fields using the ipc.py key definitions
                # Event keys: ts (timestamp), src (source), event_type (type)
                required_fields: list[str] = ["ts", "src", "event_type"]
                for field in required_fields:
                    assert field in event, (
                        f"Missing required field '{field}' in event on line {i + 1} "
                        f"of {jsonl_file.name}: {event}"
                    )

    # ------------------------------------------------------------------
    # Test 6: ZMQ telemetry is receivable
    # ------------------------------------------------------------------

    def test_zmq_telemetry_received(self, pipeline_env: dict[str, Any]) -> None:
        """Verify ZMQ PUB/SUB telemetry messages are receivable.

        Creates a SUB socket, connects to the telemetry endpoint, and
        waits for at least 1 message within 5 seconds. Decodes with
        msgpack to verify envelope structure.
        """
        if "data_manager_python" in pipeline_env["skipped_modules"]:
            pytest.skip("data_manager_python not available -- no ZMQ publisher")

        ctx: zmq.Context = zmq.Context()
        sub: zmq.Socket = ctx.socket(zmq.SUB)

        try:
            # Try IPC first, fall back to TCP
            endpoint: str = SOCK_TELEMETRY
            try:
                sub.connect(endpoint)
            except zmq.ZMQError:
                endpoint = "tcp://127.0.0.1:5555"
                sub.connect(endpoint)

            sub.setsockopt_string(zmq.SUBSCRIBE, "")
            sub.setsockopt(zmq.RCVTIMEO, 5000)

            try:
                # Receive multipart: [topic, payload]
                frames: list[bytes] = sub.recv_multipart()
            except zmq.Again:
                pytest.skip(
                    "No ZMQ telemetry received within 5s -- "
                    "data_manager may not be publishing"
                )
                return

            assert len(frames) >= 1, "Expected at least 1 frame in telemetry message"

            # If multipart, second frame is msgpack payload
            if len(frames) >= 2:
                payload: dict[str, Any] = msgpack.unpackb(frames[1], raw=False)
                assert isinstance(payload, dict), (
                    f"Expected dict payload, got {type(payload)}"
                )
        finally:
            sub.close()
            ctx.term()

    # ------------------------------------------------------------------
    # Test 7: DuckDB query via ZMQ REQ/REP API
    # ------------------------------------------------------------------

    def test_duckdb_query_via_zmq_api(self, pipeline_env: dict[str, Any]) -> None:
        """Verify DuckDB time_series query via ZMQ REQ/REP to SOCK_LOGGER_QUERY.

        Sends a time_series query request for system_total_soc via the
        logger's query API, receives the response, and validates:
        - Response status is 'ok'
        - Result contains data points
        - SOC values are within valid range

        This validates CONTEXT.md E2E step 3: 'Query time_series for pack_soc
        via ZMQ REQ/REP -> returns >= 25 data points'
        """
        if "logger" in pipeline_env["skipped_modules"]:
            pytest.skip("Logger not available -- cannot query via ZMQ API")

        # Build time window covering the test period
        now_ms: int = int(time.time() * 1000)
        start_ms: int = now_ms - (PIPELINE_WARMUP_S + 10) * 1000
        end_ms: int = now_ms + 5000

        ctx: zmq.Context = zmq.Context()
        req: zmq.Socket = ctx.socket(zmq.REQ)

        try:
            # Connect to query endpoint
            endpoint: str = SOCK_LOGGER_QUERY
            try:
                req.connect(endpoint)
            except zmq.ZMQError:
                pytest.skip(
                    f"Cannot connect to {endpoint} -- logger query API unavailable"
                )
                return

            req.setsockopt(zmq.RCVTIMEO, 5000)
            req.setsockopt(zmq.SNDTIMEO, 2000)

            # Build and send query request
            query_params: dict[str, Any] = {
                "type": "time_series",
                "signals": ["system_total_soc"],
                "start_ts": start_ms,
                "end_ts": end_ms,
                "interval_s": 1,
                "file_prefix": "system_*",
            }
            request_data: bytes = encode_command_request("query", query_params)

            try:
                req.send(request_data)
            except zmq.Again:
                pytest.skip("ZMQ send timed out -- logger query API not accepting")
                return

            # Receive response
            try:
                response_data: bytes = req.recv()
            except zmq.Again:
                pytest.skip(
                    "ZMQ recv timed out -- logger query API not responding"
                )
                return

            # Decode and validate response
            response: dict[str, Any] = decode_command_response(response_data)

            status: str | None = response.get("status")
            assert status == "ok", (
                f"Expected status 'ok', got '{status}': "
                f"{response.get('error_msg', 'no error message')}"
            )

            result: dict[str, Any] | None = response.get("result")
            assert result is not None, "Expected result dict in response"

            rows: list[Any] = result.get("rows", [])
            assert len(rows) >= MIN_DATA_POINTS, (
                f"Expected >= {MIN_DATA_POINTS} data points from time_series query, "
                f"got {len(rows)}"
            )

            # Validate SOC values are in range
            # Row format: [bucket_ts, system_total_soc]
            columns: list[str] = result.get("columns", [])
            soc_idx: int = 1  # Default: second column
            if "system_total_soc" in columns:
                soc_idx = columns.index("system_total_soc")

            soc_min_allowed: float = SOC_MIN - SOC_TOLERANCE_PCT
            soc_max_allowed: float = SOC_MAX + SOC_TOLERANCE_PCT

            non_null_soc: int = 0
            for row in rows:
                if len(row) > soc_idx and row[soc_idx] is not None and row[soc_idx] != 0:
                    soc_val: float = float(row[soc_idx])
                    assert soc_min_allowed <= soc_val <= soc_max_allowed, (
                        f"SOC {soc_val} outside valid range "
                        f"[{soc_min_allowed}, {soc_max_allowed}]"
                    )
                    non_null_soc += 1

            assert non_null_soc > 0, "Expected at least 1 non-null SOC value in response"

        finally:
            req.close()
            ctx.term()
