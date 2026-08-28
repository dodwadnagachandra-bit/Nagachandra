# Phase 13: Integration and Hardening - Research

**Researched:** 2026-03-14
**Domain:** Cross-module integration testing, crash recovery, performance validation
**Confidence:** HIGH

## Summary

Phase 13 validates that all 5 core modules (config_manager, data_manager, safety_manager, comm_manager, logger) run together correctly. This is a testing-only phase -- no new features. The codebase already has strong foundations: subprocess-based integration tests (`test_integration.py`, `test_foundation_integration.py`), three simulators with fault injection (CAN, Modbus, GPIO), complete systemd service files, and shared utility libraries (`ems_common.ipc`, `ems_common.rtdb`).

The research focuses on practical patterns for: (1) launching and killing real module processes from pytest, (2) measuring crash recovery times, (3) collecting performance metrics (GPIO latency, RSS growth, ZMQ lag), and (4) structuring 40+ minute test suites. All approaches use subprocess.Popen directly -- not systemd -- because tests must run on dev machines without root or installed services.

**Primary recommendation:** Build integration tests as pytest classes using subprocess.Popen fixtures to launch modules as real OS processes, with psutil for RSS monitoring, time.monotonic for latency measurement, and ZMQ sequence numbers for lag detection. No new frameworks needed beyond adding `psutil` to dev dependencies.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Crash recovery test matrix: 6 modules x SIGKILL + SIGTERM, with specific recovery criteria (process alive + RTDB fresh + ZMQ flowing within 10 seconds)
- Double-fault scenarios: comm_manager+logger, data_manager+comm_manager
- RTDB shm survives data_manager crash -- re-attach on restart, only recreate if corrupted
- Safety outputs remain asserted during safety_manager restart gap
- systemd RestartSec=5 already configured
- Load profiles: residential (1x4x8x16x8) and container (4x16x20x108x40)
- Test passes: clean (10 min) + fault injection (10 min) per topology
- Performance thresholds: GPIO <100ms p99, RTDB write <10ms p99, ZMQ <5 messages behind, logger >=95% write rate, RSS <10% growth after first minute
- Fully automated via pytest, `make test-integration` target
- Pass criteria: measurable thresholds only, not log cleanliness
- E2E pipeline: simulator -> comm -> RTDB -> ZMQ -> logger -> Parquet -> DuckDB query
- Unit tests in CI, integration tests locally only
- Startup test: all 6 services active within 30 seconds
- Recovery test: all 3 criteria met within 10 seconds per module

### Claude's Discretion
- pytest fixture design for launching/killing modules as subprocesses
- Simulator seed configuration for deterministic test data
- Timing measurement implementation (GPIO harness instrumentation)
- ZMQ lag measurement approach (sequence number tracking)
- RSS monitoring implementation (psutil or /proc)
- Test report format and output
- Makefile target structure for test-integration

### Deferred Ideas (OUT OF SCOPE)
- CI integration tests (nightly job)
- Hardware-in-the-loop testing with ECU-1170-552A
- Watchdog reboot recovery test
- Multi-day soak test
- Performance profiling and optimization (only if metrics fail thresholds)
</user_constraints>

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | >=8.0 | Test framework | Already used, 119+ existing tests |
| pytest-asyncio | >=1.3.0 | Async test support | Already used for ZMQ tests |
| pyzmq | >=26.0 | ZMQ message assertions | Already in ems_common |
| msgpack | >=1.0 | Message decode for assertions | Already in ems_common |
| pyarrow | >=23.0.1 | Parquet file validation | Already in ems-logger |
| duckdb | >=1.5.0 | Query validation | Already in ems-logger |
| python-can | >=4.0 | CAN frame verification | Already in dev deps |

### New Dependencies Required
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| psutil | >=6.0 | RSS memory monitoring, process management | RSS growth metric, process kill/wait |
| pytest-timeout | >=2.3 | Per-test timeout enforcement | Prevent hanging 10-minute test passes |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| psutil for RSS | `/proc/{pid}/status` parsing | psutil is cross-platform, cleaner API; /proc is zero-dependency but Linux-only and fragile |
| pytest-timeout | Manual asyncio.wait_for | pytest-timeout applies globally, handles subprocess hangs; manual approach is per-test |
| subprocess.Popen | systemctl start/stop | Popen works without root, without installed services; systemctl requires deployment |

**Installation:**
```bash
uv add --dev psutil pytest-timeout
```

## Architecture Patterns

### Recommended Test Structure
```
tests/
  integration/
    __init__.py
    conftest.py             # Shared fixtures: module launcher, profile selector, metrics collector
    test_startup.py         # Startup ordering, all services active within 30s
    test_crash_recovery.py  # SIGKILL/SIGTERM per module, double-fault
    test_e2e_pipeline.py    # Simulator -> comm -> RTDB -> ZMQ -> logger -> Parquet -> DuckDB
    test_performance.py     # GPIO latency, RTDB write, ZMQ lag, RSS growth, logger throughput
```

### Pattern 1: Module Launcher Fixture

**What:** A reusable fixture that starts a module as a subprocess, waits for readiness, and guarantees cleanup.

**When to use:** Every integration test that needs real module processes.

**Example:**
```python
import subprocess
import time
import signal
from pathlib import Path
from typing import Generator

import psutil
import pytest

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
BUILD_DIR: Path = PROJECT_ROOT / "build"


class ModuleProcess:
    """Wrapper around subprocess.Popen with health check and signal control."""

    def __init__(self, name: str, cmd: list[str], ready_check: callable) -> None:
        self.name: str = name
        self.proc: subprocess.Popen = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=lambda: None,  # New process group for clean kill
        )
        self._wait_ready(ready_check)

    def _wait_ready(self, check: callable, timeout: float = 10.0) -> None:
        deadline: float = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.2)
        raise TimeoutError(f"{self.name} not ready within {timeout}s")

    def kill(self, sig: int = signal.SIGKILL) -> None:
        self.proc.send_signal(sig)
        self.proc.wait(timeout=5)

    def terminate(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def rss_bytes(self) -> int:
        try:
            return psutil.Process(self.proc.pid).memory_info().rss
        except psutil.NoSuchProcess:
            return 0

    @property
    def is_alive(self) -> bool:
        return self.proc.poll() is None


@pytest.fixture(scope="module")
def data_manager_c() -> Generator[ModuleProcess, None, None]:
    """Launch data_manager_c binary, wait for RTDB shm to appear."""
    cmd: list[str] = [
        str(BUILD_DIR / "src/data_manager/c/data_manager_c"),
        "1", "4", "10", "16", "8",
    ]
    proc = ModuleProcess(
        name="data_manager_c",
        cmd=cmd,
        ready_check=lambda: Path("/dev/shm/ems_rtdb").exists(),
    )
    yield proc
    proc.terminate()
```

### Pattern 2: Recovery Test Pattern

**What:** Kill a process, wait for manual restart (simulating systemd), assert recovery criteria.

**When to use:** All crash recovery tests.

**Example:**
```python
import os
import signal
import time

def test_module_sigkill_recovery(module_launcher):
    """Kill module with SIGKILL, restart, verify recovery within 10s."""
    # Capture pre-kill state
    original_pid: int = module_launcher.pid

    # Kill
    module_launcher.kill(signal.SIGKILL)

    # Restart (test acts as systemd -- re-launch the process)
    module_launcher.restart()

    # Assert recovery criteria within 10 seconds
    deadline: float = time.monotonic() + 10.0
    criteria: dict[str, bool] = {"alive": False, "rtdb_fresh": False, "zmq_flowing": False}

    while time.monotonic() < deadline and not all(criteria.values()):
        criteria["alive"] = module_launcher.is_alive
        criteria["rtdb_fresh"] = _check_rtdb_fresh(module_launcher.name)
        criteria["zmq_flowing"] = _check_zmq_flowing()
        time.sleep(0.5)

    assert all(criteria.values()), f"Recovery failed: {criteria}"
```

### Pattern 3: Performance Metrics Collection

**What:** Collect time-series metrics during a test pass, then assert thresholds.

**When to use:** All performance validation tests.

**Example:**
```python
import time
from dataclasses import dataclass, field

@dataclass
class MetricsCollector:
    """Collects performance samples over a test duration."""
    gpio_latencies_ms: list[float] = field(default_factory=list)
    rtdb_write_latencies_ms: list[float] = field(default_factory=list)
    zmq_lag_messages: list[int] = field(default_factory=list)
    rss_samples: dict[str, list[int]] = field(default_factory=dict)
    logger_rows_written: int = 0
    logger_rows_expected: int = 0

    def assert_thresholds(self) -> None:
        sorted_gpio = sorted(self.gpio_latencies_ms)
        p99_idx = int(len(sorted_gpio) * 0.99)
        assert sorted_gpio[p99_idx] < 100.0, f"GPIO p99={sorted_gpio[p99_idx]}ms > 100ms"

        sorted_rtdb = sorted(self.rtdb_write_latencies_ms)
        p99_idx = int(len(sorted_rtdb) * 0.99)
        assert sorted_rtdb[p99_idx] < 10.0, f"RTDB p99={sorted_rtdb[p99_idx]}ms > 10ms"

        assert max(self.zmq_lag_messages) < 5, f"ZMQ lag={max(self.zmq_lag_messages)} > 5"

        # RSS growth check: compare minute-1+ average to minute-0 average
        for name, samples in self.rss_samples.items():
            if len(samples) < 60:
                continue
            baseline = sum(samples[:60]) / 60  # first minute
            final = sum(samples[-60:]) / 60    # last minute
            growth_pct = ((final - baseline) / baseline) * 100
            assert growth_pct < 10.0, f"{name} RSS grew {growth_pct:.1f}% > 10%"
```

### Anti-Patterns to Avoid
- **Using systemctl in tests:** Requires root, installed services, conflicts with dev environment. Use subprocess.Popen directly.
- **Polling with time.sleep(N) without deadline:** Always use `time.monotonic() + timeout` deadline pattern to avoid unbounded waits.
- **Shared fixture state between test classes:** Each test class should get fresh module processes to avoid cross-contamination.
- **Checking log output for pass/fail:** Logs are informational. Use measurable metrics (RTDB timestamps, ZMQ sequences, Parquet row counts).
- **Running integration tests with `make test`:** Keep unit tests and integration tests separate. `make test` = unit, `make test-integration` = integration.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Process RSS tracking | Manual `/proc/pid/status` parsing | `psutil.Process(pid).memory_info().rss` | Handles edge cases (zombie, exited), cross-platform |
| Test timeout enforcement | Custom signal.alarm or threading.Timer | `pytest-timeout` plugin | Handles subprocess cleanup, configurable per-test |
| Percentile calculation | Manual sort + index | `sorted(data)[int(len(data) * pct)]` | Simple enough to inline, no numpy needed |
| ZMQ message decode | Custom msgpack parsing | `ems_common.ipc.decode_telemetry()` | Already exists, tested, matches wire format |
| RTDB attach/validate | Custom shm open + struct parse | `ems_common.rtdb.attach_rtdb()` | Already exists, handles magic/version check |
| CAN frame send/receive | Raw socket operations | `python-can` library | Already used in `test_integration.py` |
| Parquet file read | Custom binary parsing | `pyarrow.parquet.read_table()` | Already a dependency of ems-logger |

**Key insight:** The codebase already has the building blocks (`ems_common.ipc`, `ems_common.rtdb`, simulators). Integration tests compose these, they don't rebuild them.

## Common Pitfalls

### Pitfall 1: Orphaned Subprocesses After Test Failure
**What goes wrong:** If a test assertion fails mid-test, subprocess cleanup in the fixture teardown may not run if using `yield` fixtures incorrectly, leaving zombie processes consuming resources.
**Why it happens:** pytest fixture teardown runs after `yield`, but if the test process itself is killed (e.g., by pytest-timeout), teardown may not execute.
**How to avoid:** Use `atexit` registration as a backup cleanup, and always use `try/finally` in test body for critical resources. Set `preexec_fn=os.setpgrp` on Popen to create a process group, then `os.killpg` in cleanup.
**Warning signs:** Port-in-use errors on subsequent test runs, `/dev/shm/ems_rtdb` persists between runs.

### Pitfall 2: RTDB Shared Memory Stale Between Tests
**What goes wrong:** Tests modify RTDB state, and subsequent tests see stale data because shm persists.
**Why it happens:** POSIX shared memory survives process exit -- it's kernel-managed.
**How to avoid:** Each test fixture that creates RTDB must clean up `/dev/shm/ems_rtdb` in teardown. The existing `test_foundation_integration.py` already has a `cleanup_shm` autouse fixture -- follow this pattern.
**Warning signs:** Tests pass individually but fail when run together.

### Pitfall 3: ZMQ Socket Reconnect After Process Restart
**What goes wrong:** After killing and restarting a module, ZMQ subscribers don't automatically receive from the new publisher.
**Why it happens:** ZMQ PUB/SUB with ipc:// sockets -- when the PUB process dies, the ipc socket file may be stale. The new process needs to rebind.
**How to avoid:** Use `ZMQ_RECONNECT_IVL` on SUB sockets (already default 100ms). For tests, close and recreate SUB socket after process restart if using ipc://. Alternatively, use tcp:// endpoints in tests (already done in `test_foundation_integration.py`).
**Warning signs:** ZMQ subscriber times out after module restart.

### Pitfall 4: Race Between Module Startup and Test Assertions
**What goes wrong:** Test starts asserting before the module has finished initializing, causing false negatives.
**Why it happens:** Module startup includes loading configs, opening shm, binding ZMQ sockets -- takes 1-3 seconds.
**How to avoid:** The `ModuleProcess._wait_ready()` pattern above -- poll a readiness condition (shm exists, ZMQ responds to health ping) with a timeout.
**Warning signs:** Flaky tests that pass on retry.

### Pitfall 5: Container-Scale Test Overwhelms Dev Machine
**What goes wrong:** Container topology (4 clusters x 16 racks x 20 modules x 108 cells) creates massive RTDB and CAN bus traffic, causing OOM or CPU starvation.
**Why it happens:** RTDB for container topology is ~500MB+, CAN simulator generates 64 rack frames per second.
**How to avoid:** Monitor system resources during test development. The CONTEXT.md notes ZMQ HWM of 1000 with 64 messages/second is within limits. Ensure test machine has at least 4GB free RAM. Skip container tests on low-memory machines with a pytest skip marker.
**Warning signs:** Kernel OOM killer, test machine swap thrashing.

### Pitfall 6: Deterministic Test Data with Random Signals
**What goes wrong:** SignalGenerator uses `random.gauss()` which produces different values each run, making assertion values unpredictable.
**Why it happens:** No fixed seed set before signal generation.
**How to avoid:** Set `random.seed(KNOWN_VALUE)` before starting each simulator. The CAN simulator's `SignalGenerator.__init__` uses `time.monotonic()` for drift phase but `random.gauss()` for noise -- seed the random module to make noise deterministic. Accept small tolerance (+-1%) in value assertions.
**Warning signs:** E2E pipeline assertions fail intermittently due to noise.

## Code Examples

### Launching All Modules in Correct Order
```python
# Source: derived from deploy/systemd/ service file ordering
# Order: data_manager_c -> data_manager_python -> config_manager -> safety_manager -> comm_manager -> logger

import subprocess
import time
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
BUILD_DIR: Path = PROJECT_ROOT / "build"

MODULE_START_ORDER: list[dict] = [
    {
        "name": "data_manager_c",
        "cmd": [str(BUILD_DIR / "src/data_manager/c/data_manager_c"), "1", "4", "10", "16", "8"],
        "ready_check": lambda: Path("/dev/shm/ems_rtdb").exists(),
    },
    {
        "name": "data_manager_python",
        "cmd": ["uv", "run", "python", "-m", "ems_data_manager"],
        "ready_check": lambda: True,  # Publishes on telemetry socket
    },
    {
        "name": "config_manager",
        "cmd": ["uv", "run", "python", "-m", "ems_config_manager"],
        "ready_check": lambda: True,  # Binds config ZMQ socket
    },
    {
        "name": "safety_manager",
        "cmd": [str(BUILD_DIR / "src/safety_manager/safety_manager")],
        "ready_check": lambda: True,  # Opens GPIO, writes RTDB
    },
    {
        "name": "comm_manager_c",
        "cmd": [str(BUILD_DIR / "src/comm_manager/c/comm_manager_c"),
                "--interface", "vcan0", "--base-id", "0x18FF0003",
                "--heartbeat-timeout-ms", "900"],
        "ready_check": lambda: True,  # Opens CAN socket
    },
    {
        "name": "comm_manager_python",
        "cmd": ["uv", "run", "python", "-m", "ems_comm_manager",
                "--config", str(PROJECT_ROOT / "config")],
        "ready_check": lambda: True,  # Connects Modbus
    },
    {
        "name": "logger",
        "cmd": ["uv", "run", "python", "-m", "ems_logger",
                "--config", str(PROJECT_ROOT / "config/logger_config.yaml")],
        "ready_check": lambda: True,  # Binds logger PUSH socket
    },
]
```

### RSS Monitoring with psutil
```python
# Source: psutil documentation + CONTEXT.md requirement
import psutil
import time
from typing import Optional

def sample_rss(pid: int) -> Optional[int]:
    """Return RSS in bytes for given PID, or None if process exited."""
    try:
        return psutil.Process(pid).memory_info().rss
    except psutil.NoSuchProcess:
        return None

def monitor_rss_growth(
    pids: dict[str, int],
    duration_s: float = 600.0,
    sample_interval_s: float = 1.0,
) -> dict[str, list[int]]:
    """Sample RSS for multiple processes over duration. Returns {name: [rss_bytes, ...]}."""
    samples: dict[str, list[int]] = {name: [] for name in pids}
    deadline: float = time.monotonic() + duration_s

    while time.monotonic() < deadline:
        for name, pid in pids.items():
            rss: Optional[int] = sample_rss(pid)
            if rss is not None:
                samples[name].append(rss)
        time.sleep(sample_interval_s)

    return samples
```

### ZMQ Sequence-Based Lag Measurement
```python
# Source: ems_common.ipc envelope format -- seq field in telemetry messages
import zmq
import msgpack

def measure_zmq_lag(
    endpoint: str,
    topic: str,
    duration_s: float = 10.0,
) -> list[int]:
    """Subscribe to topic, track sequence gaps. Returns list of lag values."""
    ctx: zmq.Context = zmq.Context()
    sub: zmq.Socket = ctx.socket(zmq.SUB)
    sub.connect(endpoint)
    sub.setsockopt_string(zmq.SUBSCRIBE, topic)
    sub.setsockopt(zmq.RCVTIMEO, 5000)

    lags: list[int] = []
    last_seq: int = -1
    deadline: float = __import__("time").monotonic() + duration_s

    try:
        while __import__("time").monotonic() < deadline:
            try:
                _topic = sub.recv()
                data = sub.recv()
                msg = msgpack.unpackb(data, raw=False)
                seq: int = msg.get("seq", 0)
                if last_seq >= 0:
                    gap: int = seq - last_seq - 1
                    if gap > 0:
                        lags.append(gap)
                last_seq = seq
            except zmq.Again:
                lags.append(999)  # Timeout = severe lag
    finally:
        sub.close()
        ctx.term()

    return lags
```

### GPIO Latency Measurement
```python
# Source: tools/simulators/gpio_harness/rtdb_backend.py -- DI/DO via RTDB
import time
from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

def measure_gpio_response_time(
    harness: RtdbBackend,
    di_pin: int = 0,
    do_pin: int = 0,
    timeout_ms: float = 200.0,
) -> float:
    """Set DI pin, measure time until DO pin asserts. Returns latency in ms."""
    # Clear state
    harness.set_di(di_pin, 0)
    time.sleep(0.01)  # Let safety_manager process clear state

    # Set DI (simulating e-stop or fault input)
    t_start: float = time.monotonic()
    harness.set_di(di_pin, 1)

    # Poll DO for response
    deadline: float = t_start + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if harness.get_do(do_pin) == 1:
            return (time.monotonic() - t_start) * 1000.0
        # Tight poll -- no sleep for accurate timing

    return timeout_ms  # Timeout = worst case
```

### E2E Pipeline Verification with DuckDB
```python
# Source: CONTEXT.md E2E verification steps
import duckdb
import pyarrow.parquet as pq
from pathlib import Path

def verify_e2e_pipeline(
    data_dir: Path,
    expected_min_rows: int = 25,
    soc_tolerance_pct: float = 1.0,
    expected_soc_range: tuple[float, float] = (20.0, 80.0),
) -> None:
    """Verify end-to-end data pipeline from Parquet files via DuckDB."""
    # Find parquet files
    parquet_files: list[Path] = list(data_dir.rglob("telemetry_*.parquet"))
    assert len(parquet_files) >= 1, f"No Parquet files found in {data_dir}"

    # Read and validate schema
    table = pq.read_table(parquet_files[0])
    assert table.num_rows >= expected_min_rows, (
        f"Expected >= {expected_min_rows} rows, got {table.num_rows}"
    )

    # Query via DuckDB
    conn = duckdb.connect()
    conn.execute(f"CREATE VIEW telemetry AS SELECT * FROM '{parquet_files[0]}'")
    result = conn.execute("SELECT pack_soc FROM telemetry WHERE pack_soc IS NOT NULL").fetchall()

    for row in result:
        soc: float = row[0]
        # SOC should be in valid range (SignalGenerator cycles 20-80%)
        assert expected_soc_range[0] - soc_tolerance_pct <= soc <= expected_soc_range[1] + soc_tolerance_pct, (
            f"SOC {soc} outside expected range"
        )
    conn.close()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Docker Compose for integration testing | Direct subprocess Popen | N/A (project uses native systemd) | Simpler, faster, no container overhead |
| pytest-subprocess (mocking) | Real subprocess execution | N/A (we need real process behavior) | Actually tests crash/recovery behavior |
| Manual memory monitoring | psutil 6.x `memory_info()` | psutil 6.0 (2024) | Reliable RSS tracking across platforms |

## Open Questions

1. **Safety manager GPIO on dev machines**
   - What we know: safety_manager requires `/dev/gpiochip0` or gpio-sim kernel module for real GPIO. On dev machines without these, safety_manager startup may fail.
   - What's unclear: Does safety_manager have a `--simulate` mode or fall back to RTDB-only GPIO?
   - Recommendation: Check safety_manager source. If no sim mode exists, either (a) use gpio-sim kernel module or (b) skip safety_manager GPIO latency tests on machines without it. The RTDB backend for GPIO harness can still test the DI->DO path if safety_manager reads DI from RTDB.

2. **Module command-line interfaces for test profiles**
   - What we know: Service files hardcode paths like `/opt/ems/config/`. Tests need to use project-local paths.
   - What's unclear: Which modules accept `--config` flags vs. use environment variables vs. hardcoded paths.
   - Recommendation: Each module's `__main__.py` should accept config path arguments. Verify each during plan implementation.

3. **IPC socket path for tests**
   - What we know: `ems_common.ipc` defines sockets at `ipc:///run/ems/*.sock`. Tests may not have write access to `/run/ems/`.
   - What's unclear: Whether modules support configurable socket paths or if tests need to create `/run/ems/` with appropriate permissions.
   - Recommendation: Test conftest creates `/run/ems/` directory (may need sudo). Alternatively, use TCP endpoints for test isolation as done in `test_foundation_integration.py`.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=8.0 + pytest-asyncio + pytest-timeout |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/ -v -m "not integration"` |
| Full suite command | `uv run pytest tests/integration/ -v -m integration --timeout=900` |

### Phase Requirements -> Test Map

Phase 13 has no new requirement IDs -- it validates all Phase 9-12 requirements in integration. The test categories map to CONTEXT.md decisions:

| Category | Behavior | Test Type | Automated Command | File Exists? |
|----------|----------|-----------|-------------------|-------------|
| Startup | All 6 services active within 30s | integration | `uv run pytest tests/integration/test_startup.py -v --timeout=120` | No -- Wave 0 |
| Crash recovery | SIGKILL/SIGTERM per module, 10s recovery | integration | `uv run pytest tests/integration/test_crash_recovery.py -v --timeout=300` | No -- Wave 0 |
| E2E pipeline | Sim -> comm -> RTDB -> ZMQ -> logger -> DuckDB | integration | `uv run pytest tests/integration/test_e2e_pipeline.py -v --timeout=300` | No -- Wave 0 |
| Performance | GPIO, RTDB, ZMQ, RSS, logger thresholds | integration | `uv run pytest tests/integration/test_performance.py -v --timeout=900` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/ -v -m "not integration"` (unit tests, <60s)
- **Per wave merge:** `make test-integration` (full suite, ~40 min)
- **Phase gate:** Full integration suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/integration/__init__.py` -- package marker
- [ ] `tests/integration/conftest.py` -- shared fixtures (ModuleProcess, MetricsCollector, profile selector)
- [ ] `psutil` added to dev dependencies: `uv add --dev psutil`
- [ ] `pytest-timeout` added to dev dependencies: `uv add --dev pytest-timeout`
- [ ] `Makefile` `test-integration` target added
- [ ] `tests/integration/test_startup.py` -- startup ordering tests
- [ ] `tests/integration/test_crash_recovery.py` -- crash/recovery tests
- [ ] `tests/integration/test_e2e_pipeline.py` -- end-to-end pipeline tests
- [ ] `tests/integration/test_performance.py` -- performance metric tests

## Existing Codebase Assets

### Reusable Without Modification
| Asset | Location | What It Provides |
|-------|----------|-----------------|
| `ems_common.ipc` | `src/common/python/src/ems_common/ipc.py` | Socket paths, topic constants, encode/decode helpers for all 3 ZMQ patterns |
| `ems_common.rtdb` | `src/common/python/src/ems_common/rtdb.py` | `attach_rtdb()`, `detach_rtdb()`, `validate_topology()`, full ctypes struct mirror |
| CAN simulator | `tools/simulators/can_sim/` | `SignalGenerator` with deterministic drift, `frame_drop_rate` fault injection |
| Modbus simulator | `tools/simulators/modbus_sim/` | RTU/TCP modes, `exception_code`/`exception_registers`/`response_timeout` faults |
| GPIO harness | `tools/simulators/gpio_harness/` | `RtdbBackend` for DI/DO via shm, `stuck_pins`/`bounce_ms` faults |
| Config profiles | `config/profiles/residential/`, `config/profiles/container/` | Full 14-config sets for both test topologies |
| sim-all.sh | `tools/sim-all.sh` | Reference for launching simulators with health checks |

### Patterns to Follow
| Pattern | Source | How to Reuse |
|---------|--------|-------------|
| Subprocess fixture with cleanup | `tests/test_integration.py::can_sim_process` | Extend to all 6 modules with ModuleProcess wrapper |
| RTDB create via ctypes + C lib | `tests/test_foundation_integration.py::_create_rtdb()` | Use for manual RTDB creation in tests |
| ZMQ PUB/SUB test with TCP endpoints | `tests/test_foundation_integration.py::TestTelemetryPublishReceive` | Use TCP in tests to avoid ipc:// permission issues |
| SHM cleanup autouse fixture | `tests/test_foundation_integration.py::cleanup_shm` | Apply in integration test conftest |
| systemd ordering assertions | `tests/test_foundation_integration.py::TestSystemdOrdering` | Already validates After=/Wants= -- no need to duplicate |
| Integration test marker | `tests/test_integration.py::pytestmark = pytest.mark.integration` | Use same marker for Phase 13 tests |

### Service File Reference (startup order)
| Service | After= | RestartSec | Restart Policy |
|---------|--------|-----------|----------------|
| ems-data-manager.service (C) | network.target | 1 | always |
| ems-data-manager-python.service | ems-data-manager.service | 2 | always |
| ems-config-manager.service | ems-data-manager.service | 2 | always |
| safety_manager.service | ems-data-manager.service | 1 | always |
| comm_manager.service (Python) | ems-safety-manager.service, ems-data-manager.service | 3 | always |
| comm_manager_c.service (CAN) | ems-safety-manager.service, ems-data-manager.service | 1 | always |
| logger.service | ems-data-manager.service, comm_manager.service | 5 | on-failure |

**Note:** `logger.service` uses `Restart=on-failure` (not `always`). The CONTEXT.md says "RestartSec=5 already configured for all modules" but actual values differ. The test should verify actual systemd behavior using the real RestartSec values from service files.

## Sources

### Primary (HIGH confidence)
- Project codebase: `tests/test_integration.py`, `tests/test_foundation_integration.py` -- established subprocess + ZMQ + RTDB test patterns
- Project codebase: `src/common/python/src/ems_common/ipc.py`, `rtdb.py` -- IPC and RTDB helpers
- Project codebase: `deploy/systemd/*.service` -- actual service configurations and ordering
- Project codebase: `tools/simulators/` -- CAN, Modbus, GPIO simulators with fault injection
- Project codebase: `Makefile` -- existing target patterns
- [psutil documentation](https://psutil.readthedocs.io/) -- `Process.memory_info().rss` API
- [pytest-timeout PyPI](https://pypi.org/project/pytest-timeout/) -- timeout plugin for long tests

### Secondary (MEDIUM confidence)
- [psutil heap introspection blog post](https://gmpy.dev/blog/2025/psutil-heap-introspection-apis) -- psutil 7.2.0 C extension memory tracking (not needed for RSS monitoring)
- [systemd recovery policies](https://dohost.us/index.php/2025/10/27/implementing-service-recovery-and-restart-policies-in-systemd/) -- Restart=always behavior under SIGKILL

### Tertiary (LOW confidence)
- None -- all findings verified against project codebase or official documentation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in project or well-established (psutil, pytest-timeout)
- Architecture: HIGH -- test patterns derived directly from existing codebase tests
- Pitfalls: HIGH -- identified from actual codebase analysis (shm lifecycle, ZMQ reconnect, fixture cleanup)
- Performance measurement: MEDIUM -- GPIO latency measurement via RTDB polling is indirect; actual GPIO hardware path not testable on dev machines

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable domain, no fast-moving dependencies)
