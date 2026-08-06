#!/opt/ems/python/.venv/bin/python3
"""Standalone EMS ARM64 Performance Benchmark Harness.

Designed to run directly ON the ECU-1170-552A via the Yocto venv — no pytest,
no laptop SSH needed. Suitable for hands-on hardware bring-up when running
pytest remotely is impractical.

Usage (on ECU):
    /opt/ems/python/.venv/bin/python3 /opt/ems/tools/benchmark.py
    # or if chmod +x:
    /opt/ems/tools/benchmark.py

Output:
    Structured pass/fail results to stdout + machine-readable JSON to
    /data/benchmark_results.json

Benchmarks:
    1. Control loop jitter  — ZMQ tick std dev (60s)
    2. RTDB write latency   — 10K writes, p99 (Python shm path)
    3. Parquet throughput   — rows/sec over 60s window
    4. DuckDB query latency — 24h time_series query wall clock
    5. WebSocket latency    — ZMQ-to-WS end-to-end p99 (10 messages)
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import statistics
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR: str = os.environ.get("EMS_DATA_DIR", "/data")
TELEMETRY_SOCK: str = os.environ.get(
    "EMS_TELEMETRY_SOCK", "ipc:///run/ems/telemetry.sock"
)
HMI_WS_URL: str = os.environ.get(
    "EMS_HMI_WS_URL", "ws://localhost:8080/ws/telemetry"
)
RESULTS_FILE: str = os.path.join(DATA_DIR, "benchmark_results.json")

# Benchmark targets
CONTROL_JITTER_TARGET_MS: float = 10.0
RTDB_WRITE_LATENCY_P99_MS: float = 1.0
PARQUET_THROUGHPUT_MIN_RPS: float = 1.0
DUCKDB_QUERY_MAX_S: float = 5.0
WEBSOCKET_LATENCY_MAX_MS: float = 100.0


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class BenchmarkResult:
    """Single benchmark measurement result."""

    def __init__(
        self,
        name: str,
        value: float | None,
        unit: str,
        target: float,
        passed: bool,
        error: str | None = None,
    ) -> None:
        self.name: str = name
        self.value: float | None = value
        self.unit: str = unit
        self.target: float = target
        self.passed: bool = passed
        self.error: str | None = error

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "target": self.target,
            "pass": self.passed,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def _percentile(data: list[float], pct: float) -> float:
    """Compute the p-th percentile of a sorted or unsorted list.

    Args:
        data: List of float values.
        pct: Percentile fraction (0.0 to 1.0).

    Returns:
        Interpolated percentile value.
    """
    if not data:
        return 0.0
    sorted_data: list[float] = sorted(data)
    idx: float = pct * (len(sorted_data) - 1)
    lo: int = int(idx)
    hi: int = min(lo + 1, len(sorted_data) - 1)
    frac: float = idx - lo
    return sorted_data[lo] * (1.0 - frac) + sorted_data[hi] * frac


# ---------------------------------------------------------------------------
# Benchmark 1: Control loop jitter
# ---------------------------------------------------------------------------


def bench_control_jitter() -> BenchmarkResult:
    """Measure control_manager tick-to-tick jitter via ZMQ telemetry.

    Subscribes to the telemetry socket for 60 seconds, collects timestamps
    from each message, and computes the 1-sigma standard deviation of
    tick-to-tick intervals.

    Returns:
        BenchmarkResult with std dev in ms.
    """
    try:
        import msgpack
        import zmq
    except ImportError as exc:
        return BenchmarkResult(
            "control_jitter", None, "ms", CONTROL_JITTER_TARGET_MS,
            False, f"import error: {exc}"
        )

    COLLECT_S: int = 60
    ctx: zmq.Context = zmq.Context()
    sub: zmq.Socket = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    sub.setsockopt(zmq.LINGER, 0)
    sub.connect(TELEMETRY_SOCK)
    sub.setsockopt_string(zmq.SUBSCRIBE, "")

    timestamps: list[float] = []
    deadline: float = time.monotonic() + COLLECT_S
    while time.monotonic() < deadline:
        try:
            _topic: bytes = sub.recv()
            data: bytes = sub.recv()
            msg: dict[str, Any] = msgpack.unpackb(data, raw=False)
            ts: Any = msg.get("timestamp_ms") or msg.get("ts") or msg.get("t")
            if ts is not None:
                timestamps.append(float(ts))
        except zmq.Again:
            pass

    sub.close()
    ctx.term()

    if len(timestamps) < 10:
        return BenchmarkResult(
            "control_jitter", None, "ms", CONTROL_JITTER_TARGET_MS,
            False, f"only {len(timestamps)} timestamps (need >=10)"
        )

    intervals: list[float] = [
        timestamps[i + 1] - timestamps[i]
        for i in range(len(timestamps) - 1)
        if 0 < timestamps[i + 1] - timestamps[i] < 5000
    ]
    if len(intervals) < 5:
        return BenchmarkResult(
            "control_jitter", None, "ms", CONTROL_JITTER_TARGET_MS,
            False, f"too few valid intervals ({len(intervals)})"
        )

    std_ms: float = statistics.stdev(intervals)
    return BenchmarkResult(
        "control_jitter",
        round(std_ms, 3),
        "ms",
        CONTROL_JITTER_TARGET_MS,
        std_ms < CONTROL_JITTER_TARGET_MS,
    )


# ---------------------------------------------------------------------------
# Benchmark 2: RTDB write latency (p99)
# ---------------------------------------------------------------------------


def bench_rtdb_write_latency() -> BenchmarkResult:
    """Measure RTDB write latency via 10,000 Python shm writes.

    Attempts to use ems_common.rtdb; falls back to a direct mmap write
    to /dev/shm/ems_rtdb if the Python module is unavailable.

    Returns:
        BenchmarkResult with p99 latency in ms.
    """
    import mmap
    import struct

    N: int = 10000
    latencies: list[float] = []

    # Try ems_common.rtdb first
    try:
        from ems_common.rtdb import attach_rtdb, detach_rtdb  # type: ignore[import]

        shm_obj: Any
        rtdb_obj: Any
        shm_obj, rtdb_obj = attach_rtdb()
        try:
            for _ in range(N):
                t0: float = time.monotonic()
                rtdb_obj.system.last_update_ms = int(time.time() * 1000)
                t1: float = time.monotonic()
                latencies.append((t1 - t0) * 1000.0)
        finally:
            del rtdb_obj
            detach_rtdb(shm_obj)
    except Exception:
        # Fallback: mmap direct write
        try:
            shm_path: str = "/dev/shm/ems_rtdb"
            fd: int = os.open(shm_path, os.O_RDWR)
            size: int = os.fstat(fd).st_size
            mm: mmap.mmap = mmap.mmap(fd, size)
            os.close(fd)
            try:
                for _ in range(N):
                    t0 = time.monotonic()
                    mm[0:8] = struct.pack("<Q", int(time.time() * 1000))
                    t1 = time.monotonic()
                    latencies.append((t1 - t0) * 1000.0)
            finally:
                mm.close()
        except Exception as exc:
            return BenchmarkResult(
                "rtdb_write_latency", None, "ms", RTDB_WRITE_LATENCY_P99_MS,
                False, f"RTDB unavailable: {exc}"
            )

    p99: float = _percentile(latencies, 0.99)
    return BenchmarkResult(
        "rtdb_write_latency",
        round(p99, 4),
        "ms",
        RTDB_WRITE_LATENCY_P99_MS,
        p99 < RTDB_WRITE_LATENCY_P99_MS,
    )


# ---------------------------------------------------------------------------
# Benchmark 3: Parquet write throughput
# ---------------------------------------------------------------------------


def bench_parquet_throughput() -> BenchmarkResult:
    """Measure Parquet write throughput over a 60-second window.

    Counts total Parquet rows at t=0 and t=60s, then computes rows/sec.

    Returns:
        BenchmarkResult with rows/sec.
    """
    try:
        import pyarrow.parquet as pq  # type: ignore[import]
        import pathlib
    except ImportError as exc:
        return BenchmarkResult(
            "parquet_throughput", None, "rows/sec", PARQUET_THROUGHPUT_MIN_RPS,
            False, f"pyarrow unavailable: {exc}"
        )

    def _count_rows() -> int:
        total: int = 0
        for pf in pathlib.Path(DATA_DIR).rglob("*.parquet"):
            try:
                total += pq.read_metadata(str(pf)).num_rows
            except Exception:
                pass
        return total

    rows_t0: int = _count_rows()
    time.sleep(60)
    rows_t60: int = _count_rows()

    delta: int = rows_t60 - rows_t0
    rps: float = delta / 60.0
    return BenchmarkResult(
        "parquet_throughput",
        round(rps, 3),
        "rows/sec",
        PARQUET_THROUGHPUT_MIN_RPS,
        rps >= PARQUET_THROUGHPUT_MIN_RPS,
    )


# ---------------------------------------------------------------------------
# Benchmark 4: DuckDB query latency
# ---------------------------------------------------------------------------


def bench_duckdb_query_latency() -> BenchmarkResult:
    """Measure DuckDB query latency over a 24h Parquet window.

    Runs a time_series GROUP BY query over the last 24 hours of Parquet data.
    Falls back to a simple COUNT(*) if no timestamp_ms column exists.

    Returns:
        BenchmarkResult with elapsed seconds.
    """
    try:
        import duckdb  # type: ignore[import]
    except ImportError as exc:
        return BenchmarkResult(
            "duckdb_query_latency", None, "s", DUCKDB_QUERY_MAX_S,
            False, f"duckdb unavailable: {exc}"
        )

    import glob

    files: list[str] = glob.glob(f"{DATA_DIR}/**/*.parquet", recursive=True)
    if not files:
        return BenchmarkResult(
            "duckdb_query_latency", None, "s", DUCKDB_QUERY_MAX_S,
            False, f"no Parquet files in {DATA_DIR}"
        )

    cutoff_ms: int = int(
        (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).timestamp() * 1000
    )

    t_start: float = time.monotonic()
    try:
        duckdb.execute(
            f"SELECT COUNT(*), MIN(timestamp_ms), MAX(timestamp_ms) "
            f"FROM read_parquet('{DATA_DIR}/**/*.parquet') "
            f"WHERE timestamp_ms >= {cutoff_ms}"
        ).fetchone()
    except Exception:
        # Fallback: schema may differ
        try:
            duckdb.execute(
                f"SELECT COUNT(*) FROM read_parquet('{DATA_DIR}/**/*.parquet')"
            ).fetchone()
        except Exception as exc2:
            return BenchmarkResult(
                "duckdb_query_latency", None, "s", DUCKDB_QUERY_MAX_S,
                False, f"DuckDB query error: {exc2}"
            )
    elapsed: float = time.monotonic() - t_start

    return BenchmarkResult(
        "duckdb_query_latency",
        round(elapsed, 3),
        "s",
        DUCKDB_QUERY_MAX_S,
        elapsed < DUCKDB_QUERY_MAX_S,
    )


# ---------------------------------------------------------------------------
# Benchmark 5: WebSocket latency
# ---------------------------------------------------------------------------


def bench_websocket_latency() -> BenchmarkResult:
    """Measure ZMQ-to-WebSocket end-to-end latency (p99 of 10 messages).

    Connects to the HMI server WebSocket endpoint and reads 10 messages,
    computing the delta between the embedded ZMQ publish timestamp and the
    local receive wall clock.

    Returns:
        BenchmarkResult with p99 latency in ms.
    """
    try:
        import json as _json
        import websocket  # type: ignore[import]
    except ImportError as exc:
        return BenchmarkResult(
            "websocket_latency", None, "ms", WEBSOCKET_LATENCY_MAX_MS,
            False, f"websocket-client unavailable: {exc}"
        )

    latencies: list[float] = []
    try:
        ws: websocket.WebSocket = websocket.create_connection(
            HMI_WS_URL, timeout=5
        )
        try:
            for _ in range(10):
                data: str = ws.recv()
                recv_ms: float = time.time() * 1000.0
                if data:
                    try:
                        msg: dict[str, Any] = _json.loads(data)
                        pub_ms: Any = (
                            msg.get("timestamp_ms")
                            or msg.get("ts")
                            or msg.get("t")
                        )
                        if pub_ms is not None:
                            delta: float = recv_ms - float(pub_ms)
                            if 0.0 <= delta < 10000.0:
                                latencies.append(delta)
                    except (_json.JSONDecodeError, ValueError):
                        pass
        finally:
            ws.close()
    except Exception as exc:
        return BenchmarkResult(
            "websocket_latency", None, "ms", WEBSOCKET_LATENCY_MAX_MS,
            False, f"WebSocket connect failed: {exc}"
        )

    if not latencies:
        return BenchmarkResult(
            "websocket_latency", None, "ms", WEBSOCKET_LATENCY_MAX_MS,
            False, "no valid latency samples (missing timestamp in messages)"
        )

    p99: float = _percentile(latencies, 0.99)
    return BenchmarkResult(
        "websocket_latency",
        round(p99, 2),
        "ms",
        WEBSOCKET_LATENCY_MAX_MS,
        p99 < WEBSOCKET_LATENCY_MAX_MS,
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_result(index: int, total: int, result: BenchmarkResult) -> None:
    """Print a single benchmark result in human-readable format.

    Args:
        index: 1-based benchmark index.
        total: Total benchmark count.
        result: BenchmarkResult to display.
    """
    name_map: dict[str, str] = {
        "control_jitter": "Control Loop Jitter",
        "rtdb_write_latency": "RTDB Write Latency (p99)",
        "parquet_throughput": "Parquet Write Throughput",
        "duckdb_query_latency": "DuckDB Query Latency",
        "websocket_latency": "WebSocket Latency (p99)",
    }
    display_name: str = name_map.get(result.name, result.name)
    status: str = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
    value_str: str = (
        f"{result.value:.3g}{result.unit}" if result.value is not None else "N/A"
    )
    target_str: str = f"{result.target:.3g}{result.unit}"

    print(f"\n[{index}/{total}] {display_name}")
    print(f"  Measured: {value_str}")
    print(f"  Target:   {target_str}")
    print(f"  Result:   {status}")
    if result.error:
        print(f"  Error:    {result.error}")


def _write_json(results: list[BenchmarkResult], hostname: str) -> None:
    """Write benchmark results to /data/benchmark_results.json.

    Args:
        results: List of BenchmarkResult objects.
        hostname: ECU hostname from socket.gethostname().
    """
    payload: dict[str, Any] = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "hostname": hostname,
        "benchmarks": [r.to_dict() for r in results],
    }
    try:
        os.makedirs(os.path.dirname(RESULTS_FILE) or ".", exist_ok=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nResults written to {RESULTS_FILE}")
    except OSError as exc:
        print(f"\nWarning: could not write {RESULTS_FILE}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Run all benchmarks and report results.

    Returns:
        Exit code: 0 if all benchmarks pass, 1 otherwise.
    """
    hostname: str = socket.gethostname()
    now: str = datetime.datetime.utcnow().isoformat() + "Z"

    print("=== EMS ARM64 Performance Benchmarks ===")
    print(f"Date: {now}")
    print(f"Host: {hostname}")
    print(f"Data: {DATA_DIR}")

    benchmarks: list[tuple[str, Any]] = [
        ("control_jitter", bench_control_jitter),
        ("rtdb_write_latency", bench_rtdb_write_latency),
        ("parquet_throughput", bench_parquet_throughput),
        ("duckdb_query_latency", bench_duckdb_query_latency),
        ("websocket_latency", bench_websocket_latency),
    ]

    results: list[BenchmarkResult] = []
    total: int = len(benchmarks)

    for idx, (name, bench_fn) in enumerate(benchmarks, start=1):
        print(f"\nRunning [{idx}/{total}] {name}...", flush=True)
        try:
            result: BenchmarkResult = bench_fn()
        except Exception as exc:
            result = BenchmarkResult(
                name, None, "", 0.0, False, f"unhandled exception: {exc}"
            )
        results.append(result)
        _print_result(idx, total, result)

    # Summary
    passed: int = sum(1 for r in results if r.passed)
    failed: int = total - passed
    print(f"\n{'=' * 40}")
    print(f"=== Summary: {passed}/{total} PASS ===")
    if failed > 0:
        print("Failed benchmarks:")
        for r in results:
            if not r.passed:
                err: str = f" ({r.error})" if r.error else ""
                print(f"  - {r.name}{err}")
    print("=" * 40)

    # Write JSON results
    _write_json(results, hostname)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
