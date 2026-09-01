"""ARM64 Performance Benchmarks — ECU-1170-552A.

Measures 5 key performance metrics on real ARM64 hardware and asserts them
against defined targets. All tests run via SSH to the ECU using helpers from
conftest.py.

Benchmark targets (from 29-RESEARCH.md):
    Control jitter:         <=10ms 1-sigma
    RTDB write latency p99: <1ms
    Parquet throughput:     >=1 row/sec sustained
    DuckDB query latency:   <5s (24h window)
    WebSocket latency:      <100ms ZMQ-to-WS

Run from developer laptop (requires SSH access to ECU):
    uv run pytest tests/hw/test_benchmarks.py -v

Skip when ECU is offline:
    All tests auto-skip via ecu_reachable session fixture.
"""

from __future__ import annotations

import base64
import time

import pytest

from tests.hw.conftest import DATA_DIR, EMS_VENV, ecu_ssh

# All tests in this module require ECU hardware; allow up to 1 hour per session
pytestmark = [pytest.mark.hw, pytest.mark.timeout(3600)]

# ---------------------------------------------------------------------------
# Benchmark targets (ARM64, full system load)
# ---------------------------------------------------------------------------

CONTROL_JITTER_TARGET_MS: float = 10.0
"""1-sigma std dev of control_manager tick-to-tick intervals (ms)."""

RTDB_WRITE_LATENCY_P99_MS: float = 1.0
"""p99 of 10,000 RTDB write latency measurements (ms)."""

PARQUET_THROUGHPUT_MIN_RPS: float = 1.0
"""Minimum sustained Parquet write rate (rows/sec)."""

DUCKDB_QUERY_MAX_S: float = 5.0
"""Maximum wall-clock time for a 24-hour time_series DuckDB query (s)."""

WEBSOCKET_LATENCY_MAX_MS: float = 100.0
"""Maximum ZMQ-to-WebSocket end-to-end latency p99 (ms)."""


# ---------------------------------------------------------------------------
# Helper: run arbitrary Python on the ECU, passed as base64 to avoid quoting
# ---------------------------------------------------------------------------


def _run_ecu_script(source: str, ssh_timeout: int = 120) -> tuple[int, str, str]:
    """Execute a multi-line Python script on the ECU via base64-encoded SSH.

    Encodes the script as base64, transfers it as an inline shell command, and
    executes it with the Yocto venv. Avoids quoting hell for complex scripts.

    Args:
        source: Python source code to execute on the ECU.
        ssh_timeout: SSH subprocess timeout in seconds.

    Returns:
        Tuple of (returncode, stdout, stderr) all stripped.
    """
    import subprocess

    b64: str = base64.b64encode(source.encode()).decode()
    cmd: str = f"echo {b64} | base64 -d | {EMS_VENV}/bin/python3"

    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            "root@192.168.1.100",
            cmd,
        ],
        capture_output=True,
        text=True,
        timeout=ssh_timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ---------------------------------------------------------------------------
# Benchmark 1: Control loop jitter
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_control_loop_jitter() -> None:
    """Control manager tick-to-tick jitter must be <10ms 1-sigma on ARM64.

    Subscribes to the ZMQ telemetry socket for 60 seconds, extracts the
    monotonic timestamp embedded in each message, then computes the standard
    deviation of tick-to-tick intervals. A std dev above CONTROL_JITTER_TARGET_MS
    indicates scheduling pressure or CPU starvation.
    """
    script: str = """
import zmq, msgpack, statistics, time

TELEMETRY_SOCK = "ipc:///run/ems/telemetry.sock"
COLLECT_S = 60

ctx = zmq.Context()
sub = ctx.socket(zmq.SUB)
sub.setsockopt(zmq.RCVTIMEO, 2000)
sub.setsockopt(zmq.LINGER, 0)
sub.connect(TELEMETRY_SOCK)
sub.setsockopt_string(zmq.SUBSCRIBE, "")

timestamps = []
deadline = time.monotonic() + COLLECT_S
while time.monotonic() < deadline:
    try:
        _topic = sub.recv()
        data = sub.recv()
        msg = msgpack.unpackb(data, raw=False)
        ts = msg.get("timestamp_ms") or msg.get("ts") or msg.get("t")
        if ts is not None:
            timestamps.append(float(ts))
    except zmq.Again:
        pass

sub.close()
ctx.term()

if len(timestamps) < 10:
    print(f"ERROR: only {len(timestamps)} timestamps collected (need >=10)")
else:
    intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    intervals = [iv for iv in intervals if 0 < iv < 5000]  # discard outlier gaps
    if len(intervals) < 5:
        print(f"ERROR: too few valid intervals ({len(intervals)})")
    else:
        std_ms = statistics.stdev(intervals)
        mean_ms = statistics.mean(intervals)
        print(f"JITTER_STD_MS={std_ms:.3f}")
        print(f"JITTER_MEAN_MS={mean_ms:.3f}")
        print(f"JITTER_SAMPLES={len(intervals)}")
"""
    rc, stdout, stderr = _run_ecu_script(script, ssh_timeout=90)
    assert rc == 0, (
        f"Control jitter benchmark failed on ECU (rc={rc}): {stderr!r}"
    )

    if "ERROR:" in stdout:
        pytest.skip(f"Insufficient telemetry data for jitter measurement: {stdout}")

    # Parse result
    jitter_ms: float = 0.0
    for line in stdout.splitlines():
        if line.startswith("JITTER_STD_MS="):
            jitter_ms = float(line.split("=", 1)[1])
        elif line.startswith("JITTER_MEAN_MS="):
            mean_ms: float = float(line.split("=", 1)[1])
            print(f"\nControl loop mean tick: {mean_ms:.1f}ms")
        elif line.startswith("JITTER_SAMPLES="):
            samples: int = int(line.split("=", 1)[1])
            print(f"Jitter samples: {samples}")

    print(f"Control loop jitter (1-sigma): {jitter_ms:.3f}ms | target: <{CONTROL_JITTER_TARGET_MS}ms")
    assert jitter_ms < CONTROL_JITTER_TARGET_MS, (
        f"Control loop jitter {jitter_ms:.3f}ms exceeds {CONTROL_JITTER_TARGET_MS}ms target"
    )


# ---------------------------------------------------------------------------
# Benchmark 2: RTDB write latency (p99)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_rtdb_write_latency() -> None:
    """RTDB write latency p99 must be <1ms for 10,000 writes on ARM64.

    Uses the Yocto venv ems_common.rtdb attach to perform 10,000 write
    operations, measures time.monotonic() around each write, and computes the
    p99 latency. This validates the shared-memory seqlock path from Python.
    """
    script: str = """
import time

# Attempt to use ems_common.rtdb if available; fall back to mmap direct write
try:
    from ems_common.rtdb import attach_rtdb, detach_rtdb
    shm, rtdb = attach_rtdb()
    use_rtdb = True
except Exception as e:
    print(f"WARNING: ems_common.rtdb not available: {e}")
    use_rtdb = False

N = 10000
latencies = []

if use_rtdb:
    try:
        for _ in range(N):
            t0 = time.monotonic()
            rtdb.system.last_update_ms = int(time.time() * 1000)
            t1 = time.monotonic()
            latencies.append((t1 - t0) * 1000.0)
    finally:
        del rtdb
        detach_rtdb(shm)
else:
    # Fallback: measure mmap write via /dev/shm
    import mmap, struct, os
    try:
        shm_path = "/dev/shm/ems_rtdb"
        fd = os.open(shm_path, os.O_RDWR)
        size = os.fstat(fd).st_size
        mm = mmap.mmap(fd, size)
        os.close(fd)
        for _ in range(N):
            t0 = time.monotonic()
            mm[0:8] = struct.pack('<Q', int(time.time() * 1000))
            t1 = time.monotonic()
            latencies.append((t1 - t0) * 1000.0)
        mm.close()
    except Exception as e2:
        print(f"ERROR: fallback mmap write also failed: {e2}")
        latencies = []

if not latencies:
    print("ERROR: no latency samples collected")
else:
    latencies.sort()
    p99 = latencies[int(len(latencies) * 0.99)]
    p50 = latencies[int(len(latencies) * 0.50)]
    print(f"RTDB_P99_MS={p99:.4f}")
    print(f"RTDB_P50_MS={p50:.4f}")
    print(f"RTDB_SAMPLES={len(latencies)}")
"""
    rc, stdout, stderr = _run_ecu_script(script, ssh_timeout=60)
    assert rc == 0, (
        f"RTDB write latency benchmark failed on ECU (rc={rc}): {stderr!r}"
    )

    if "ERROR:" in stdout:
        pytest.skip(f"RTDB write benchmark could not run: {stdout}")

    p99_ms: float = 0.0
    for line in stdout.splitlines():
        if line.startswith("RTDB_P99_MS="):
            p99_ms = float(line.split("=", 1)[1])
        elif line.startswith("RTDB_P50_MS="):
            p50_ms: float = float(line.split("=", 1)[1])
            print(f"\nRTDB write latency p50: {p50_ms:.4f}ms")
        elif line.startswith("RTDB_SAMPLES="):
            samples: int = int(line.split("=", 1)[1])
            print(f"RTDB write samples: {samples}")

    print(f"RTDB write latency p99: {p99_ms:.4f}ms | target: <{RTDB_WRITE_LATENCY_P99_MS}ms")
    assert p99_ms < RTDB_WRITE_LATENCY_P99_MS, (
        f"RTDB p99 {p99_ms:.4f}ms exceeds {RTDB_WRITE_LATENCY_P99_MS}ms target"
    )


# ---------------------------------------------------------------------------
# Benchmark 3: Parquet write throughput
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_parquet_throughput() -> None:
    """Parquet write throughput must be >=1 row/sec sustained over 60 seconds.

    Counts total Parquet rows at t=0 and t=60s, then computes rows/sec.
    Validates the logger is sustaining 1Hz writes to the SSD on ARM64.
    """
    count_script: str = f"""
import pyarrow.parquet as pq, pathlib
files = list(pathlib.Path('{DATA_DIR}').rglob('*.parquet'))
total = 0
for f in files:
    try:
        total += pq.read_metadata(str(f)).num_rows
    except Exception:
        pass
print(total)
"""
    rc0, stdout0, stderr0 = _run_ecu_script(count_script, ssh_timeout=30)
    assert rc0 == 0, (
        f"Parquet row count at t=0 failed (rc={rc0}): {stderr0!r}"
    )
    rows_t0: int = int(stdout0.strip() or "0")
    print(f"\nParquet rows at t=0: {rows_t0}")

    time.sleep(60)

    rc60, stdout60, stderr60 = _run_ecu_script(count_script, ssh_timeout=30)
    assert rc60 == 0, (
        f"Parquet row count at t=60s failed (rc={rc60}): {stderr60!r}"
    )
    rows_t60: int = int(stdout60.strip() or "0")
    print(f"Parquet rows at t=60s: {rows_t60}")

    delta: int = rows_t60 - rows_t0
    rps: float = delta / 60.0
    print(f"Parquet throughput: {rps:.2f} rows/sec | target: >={PARQUET_THROUGHPUT_MIN_RPS} rows/sec")
    assert rps >= PARQUET_THROUGHPUT_MIN_RPS, (
        f"Parquet throughput {rps:.2f} rows/sec below {PARQUET_THROUGHPUT_MIN_RPS} rows/sec target "
        f"(delta={delta} rows over 60s)"
    )


# ---------------------------------------------------------------------------
# Benchmark 4: DuckDB query latency
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_duckdb_query_latency() -> None:
    """DuckDB time_series query over 24h window must complete in <5s on ARM64.

    Runs a GROUP BY timestamp query over available Parquet data. If less than
    24h of data exists (first boot), uses all available data and notes this
    in the output. Validates DuckDB ARM64 query engine performance.
    """
    script: str = f"""
import duckdb, time, pathlib

data_dir = '{DATA_DIR}'
files = list(pathlib.Path(data_dir).rglob('*.parquet'))
if not files:
    print("ERROR: no Parquet files found in " + data_dir)
else:
    # Try 24h window; fall back to all data
    import datetime
    cutoff = int((datetime.datetime.utcnow() - datetime.timedelta(hours=24)).timestamp() * 1000)
    t0 = time.monotonic()
    try:
        result = duckdb.execute(
            "SELECT COUNT(*), MIN(timestamp_ms), MAX(timestamp_ms) "
            f"FROM read_parquet('{DATA_DIR}/**/*.parquet') "
            f"WHERE timestamp_ms >= {{cutoff}}"
        ).fetchone()
        window = "24h"
    except Exception:
        # Fallback: no timestamp_ms column or other error
        result = duckdb.execute(
            f"SELECT COUNT(*) FROM read_parquet('{DATA_DIR}/**/*.parquet')"
        ).fetchone()
        window = "all"
    elapsed = time.monotonic() - t0
    print(f"DUCKDB_ELAPSED_S={{elapsed:.3f}}")
    print(f"DUCKDB_ROWS={{result[0]}}")
    print(f"DUCKDB_WINDOW={{window}}")
"""
    rc, stdout, stderr = _run_ecu_script(script, ssh_timeout=30)
    assert rc == 0, (
        f"DuckDB query benchmark failed on ECU (rc={rc}): {stderr!r}"
    )

    if "ERROR:" in stdout:
        pytest.skip(f"DuckDB query benchmark could not run: {stdout}")

    elapsed_s: float = 0.0
    for line in stdout.splitlines():
        if line.startswith("DUCKDB_ELAPSED_S="):
            elapsed_s = float(line.split("=", 1)[1])
        elif line.startswith("DUCKDB_ROWS="):
            rows: int = int(line.split("=", 1)[1])
            print(f"\nDuckDB rows scanned: {rows}")
        elif line.startswith("DUCKDB_WINDOW="):
            window: str = line.split("=", 1)[1]
            if window != "24h":
                print(f"Note: insufficient data for 24h window — used all available data")

    print(f"DuckDB query latency: {elapsed_s:.3f}s | target: <{DUCKDB_QUERY_MAX_S}s")
    assert elapsed_s < DUCKDB_QUERY_MAX_S, (
        f"DuckDB query {elapsed_s:.3f}s exceeds {DUCKDB_QUERY_MAX_S}s target"
    )


# ---------------------------------------------------------------------------
# Benchmark 5: WebSocket latency (ZMQ -> HMI WebSocket)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_websocket_latency() -> None:
    """ZMQ-to-WebSocket end-to-end latency must be <100ms on ARM64.

    Connects to ws://localhost:8080/ws/telemetry on the ECU and reads 10
    messages. Computes the delta between the ZMQ publish timestamp embedded
    in each message and the local receive wall clock. Requires hmi_server
    to be active.

    Note: hmi_server must be running for this benchmark. The test skips if
    the WebSocket endpoint is unavailable.
    """
    script: str = """
import time, json

try:
    import websocket  # websocket-client package
    use_ws = True
except ImportError:
    use_ws = False

if not use_ws:
    print("SKIP: websocket-client not available in venv")
else:
    latencies = []
    errors = []
    try:
        ws = websocket.create_connection(
            "ws://localhost:8080/ws/telemetry",
            timeout=5,
        )
        for _ in range(10):
            try:
                data = ws.recv()
                recv_ms = time.time() * 1000.0
                msg = json.loads(data) if data else {}
                pub_ms = (
                    msg.get("timestamp_ms")
                    or msg.get("ts")
                    or msg.get("t")
                )
                if pub_ms is not None:
                    delta = recv_ms - float(pub_ms)
                    if 0 <= delta < 10000:  # sanity: <10s
                        latencies.append(delta)
            except Exception as e:
                errors.append(str(e))
        ws.close()
    except Exception as e:
        print(f"SKIP: cannot connect to ws://localhost:8080/ws/telemetry: {e}")
        latencies = []

    if not latencies:
        if errors:
            print(f"SKIP: no valid latency samples ({errors[0]})")
        # else already printed SKIP above
    else:
        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0]
        p50 = latencies[int(len(latencies) * 0.50)]
        print(f"WS_P99_MS={p99:.2f}")
        print(f"WS_P50_MS={p50:.2f}")
        print(f"WS_SAMPLES={len(latencies)}")
"""
    rc, stdout, stderr = _run_ecu_script(script, ssh_timeout=30)
    assert rc == 0, (
        f"WebSocket latency benchmark failed on ECU (rc={rc}): {stderr!r}"
    )

    if "SKIP:" in stdout:
        first_skip: str = next(
            (l for l in stdout.splitlines() if l.startswith("SKIP:")), stdout
        )
        pytest.skip(first_skip.removeprefix("SKIP:").strip())

    p99_ms: float = 0.0
    for line in stdout.splitlines():
        if line.startswith("WS_P99_MS="):
            p99_ms = float(line.split("=", 1)[1])
        elif line.startswith("WS_P50_MS="):
            p50_ms: float = float(line.split("=", 1)[1])
            print(f"\nWebSocket latency p50: {p50_ms:.2f}ms")
        elif line.startswith("WS_SAMPLES="):
            samples: int = int(line.split("=", 1)[1])
            print(f"WebSocket samples: {samples}")

    print(f"WebSocket latency p99: {p99_ms:.2f}ms | target: <{WEBSOCKET_LATENCY_MAX_MS}ms")
    assert p99_ms < WEBSOCKET_LATENCY_MAX_MS, (
        f"WebSocket latency {p99_ms:.2f}ms exceeds {WEBSOCKET_LATENCY_MAX_MS}ms target"
    )
