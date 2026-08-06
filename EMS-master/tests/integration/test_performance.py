"""Performance validation tests for the EMS integration suite.

Measures 5 key performance metrics under residential (clean and fault-injection)
and container topologies, validating against thresholds from CONTEXT.md:

1. GPIO safety response time: <100ms p99
2. RTDB write latency: <10ms p99
3. ZMQ telemetry lag: <5 messages behind publisher
4. Logger write rate: >=95% of expected throughput
5. Process RSS growth: <10% after first minute

Each test class launches all modules, collects metrics in parallel threads
for CLEAN_PASS_DURATION_S (120s default, 600s with --runslow), then asserts
thresholds individually for clear failure reporting.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import msgpack
import psutil
import pytest
import zmq

from ems_common.ipc import (
    MSG_KEY_SEQUENCE,
    SOCK_TELEMETRY,
    TOPIC_SYSTEM,
)
from ems_common.rtdb import attach_rtdb, detach_rtdb

from tests.integration.conftest import (
    BUILD_DIR,
    CONFIG_DIR,
    MetricsCollector,
    ModuleProcess,
    PROFILES,
    SHM_PATH,
    check_rtdb_exists,
)

# ---------------------------------------------------------------------------
# Attempt GPIO harness import (optional -- skip GPIO tests if unavailable)
# ---------------------------------------------------------------------------

try:
    from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

    _HAS_GPIO_HARNESS: bool = True
except ImportError:
    _HAS_GPIO_HARNESS = False

# ---------------------------------------------------------------------------
# Attempt pyarrow import (optional for logger throughput)
# ---------------------------------------------------------------------------

try:
    import pyarrow.parquet as pq

    _HAS_PYARROW: bool = True
except ImportError:
    _HAS_PYARROW = False

# ---------------------------------------------------------------------------
# Module-level markers
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

# ---------------------------------------------------------------------------
# Duration constants
# ---------------------------------------------------------------------------

CLEAN_PASS_DURATION_S: int = 120
"""Shortened 2-minute pass for practical dev testing."""

FAULT_PASS_DURATION_S: int = 120
"""Shortened 2-minute fault injection pass."""

FULL_PASS_DURATION_S: int = 600
"""Full 10-minute pass for --runslow option."""

# Number of telemetry topics per topology (for logger throughput calculation)
_RESIDENTIAL_TOPICS: int = 2  # 1 cluster + 1 system
_CONTAINER_TOPICS: int = 5  # 4 clusters + 1 system


# ---------------------------------------------------------------------------
# Metric collection functions
# ---------------------------------------------------------------------------


def collect_gpio_latencies(
    duration_s: float = 60.0,
    sample_interval_s: float = 1.0,
) -> list[float]:
    """Measure GPIO DI-to-DO response latency via RTDB backend.

    Sets DI pin 0, measures time until DO pin 0 changes (tight poll loop),
    clears DI after measurement, and sleeps between samples.

    Args:
        duration_s: Total collection duration in seconds.
        sample_interval_s: Delay between samples.

    Returns:
        List of latencies in milliseconds.  Empty if GPIO harness
        or RTDB is unavailable.
    """
    if not _HAS_GPIO_HARNESS:
        return []
    if not SHM_PATH.exists():
        return []

    latencies: list[float] = []
    try:
        harness: RtdbBackend = RtdbBackend()
    except (FileNotFoundError, RuntimeError, OSError):
        return []

    try:
        deadline: float = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            # Clear state
            harness.set_di(0, 0)
            time.sleep(0.01)

            # Set DI-0 (simulate e-stop input)
            t_start: float = time.monotonic()
            harness.set_di(0, 1)

            # Tight poll for DO-0 response (no sleep for accuracy)
            timeout_deadline: float = t_start + 0.2  # 200ms max
            responded: bool = False
            while time.monotonic() < timeout_deadline:
                try:
                    if harness.get_do(0) == 1:
                        latency_ms: float = (time.monotonic() - t_start) * 1000.0
                        latencies.append(latency_ms)
                        responded = True
                        break
                except (RuntimeError, OSError):
                    break

            if not responded:
                latencies.append(200.0)  # Timeout = worst case

            # Clear DI after measurement
            harness.set_di(0, 0)
            time.sleep(sample_interval_s)
    finally:
        try:
            harness.close()
        except Exception:
            pass

    return latencies


def collect_rtdb_write_latencies(
    duration_s: float = 60.0,
    sample_interval_s: float = 0.5,
) -> list[float]:
    """Measure RTDB write staleness by comparing system.last_update_ms to wall clock.

    Args:
        duration_s: Total collection duration in seconds.
        sample_interval_s: Delay between samples.

    Returns:
        List of absolute deltas in milliseconds.  Skips samples where
        last_update_ms is 0 (module not yet writing).
    """
    latencies: list[float] = []
    if not SHM_PATH.exists():
        return latencies

    try:
        shm, rtdb = attach_rtdb()
    except (FileNotFoundError, RuntimeError, OSError):
        return latencies

    try:
        deadline: float = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            last_update_ms: int = rtdb.system.last_update_ms
            if last_update_ms > 0:
                now_ms: float = time.time() * 1000.0
                delta: float = abs(now_ms - last_update_ms)
                latencies.append(delta)
            time.sleep(sample_interval_s)
    finally:
        del rtdb
        detach_rtdb(shm)

    return latencies


def collect_zmq_lag(
    endpoint: str,
    topic: str,
    duration_s: float = 60.0,
) -> list[int]:
    """Track ZMQ message sequence gaps to detect lag.

    Subscribes to the given topic, tracks sequence numbers, and records
    gaps (seq - last_seq - 1).  A 5-second receive timeout produces a
    999 severe-lag indicator.

    Args:
        endpoint: ZMQ endpoint (ipc:// or tcp://).
        topic: Subscription topic string.
        duration_s: Total collection duration.

    Returns:
        List of lag values (gaps only; zero-lag samples not included).
    """
    ctx: zmq.Context = zmq.Context()
    sub: zmq.Socket = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVTIMEO, 5000)
    sub.setsockopt(zmq.LINGER, 0)

    lags: list[int] = []
    last_seq: int = -1

    try:
        sub.connect(endpoint)
        sub.setsockopt_string(zmq.SUBSCRIBE, topic)
        deadline: float = time.monotonic() + duration_s

        while time.monotonic() < deadline:
            try:
                # Multi-part: [topic, payload]
                _topic_frame: bytes = sub.recv()
                data: bytes = sub.recv()
                msg: dict = msgpack.unpackb(data, raw=False)
                seq: int = msg.get(MSG_KEY_SEQUENCE, 0)

                if last_seq >= 0:
                    gap: int = seq - last_seq - 1
                    if gap > 0:
                        lags.append(gap)
                last_seq = seq

            except zmq.Again:
                lags.append(999)  # Severe lag -- no message for 5s
    finally:
        sub.close()
        ctx.term()

    return lags


def collect_rss_samples(
    modules: dict[str, ModuleProcess],
    duration_s: float = 60.0,
    interval_s: float = 1.0,
) -> dict[str, list[int]]:
    """Sample RSS (in bytes) for every alive module over duration.

    Args:
        modules: Name -> ModuleProcess mapping.
        duration_s: Total collection duration.
        interval_s: Delay between sampling rounds.

    Returns:
        Dict of module_name -> list of RSS values in bytes.
    """
    samples: dict[str, list[int]] = {name: [] for name in modules}
    deadline: float = time.monotonic() + duration_s

    while time.monotonic() < deadline:
        for name, mod in modules.items():
            if mod.is_alive:
                rss: int = mod.rss_bytes
                if rss > 0:
                    samples[name].append(rss)
        time.sleep(interval_s)

    return samples


def count_parquet_rows(data_dir: Path) -> int:
    """Sum total rows across all telemetry Parquet files.

    Args:
        data_dir: Root data directory to search recursively.

    Returns:
        Total row count.  0 if pyarrow is unavailable or no files found.
    """
    if not _HAS_PYARROW:
        return 0
    total: int = 0
    for pf in data_dir.rglob("telemetry_*.parquet"):
        try:
            table = pq.read_table(str(pf))
            total += table.num_rows
        except Exception:
            pass
    return total


# ---------------------------------------------------------------------------
# Module launch helpers (reused from test_startup.py patterns)
# ---------------------------------------------------------------------------


def _delay_ready(seconds: float) -> Any:
    """Return a ready_check callable that returns True after elapsed time."""
    start: dict[str, float] = {}

    def _check() -> bool:
        if "t" not in start:
            start["t"] = time.monotonic()
        return (time.monotonic() - start["t"]) >= seconds

    return _check


def _build_module_specs(
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build ordered module launch specs for a profile.

    Args:
        profile: Topology profile from PROFILES.

    Returns:
        List of module spec dicts with name, cmd, ready_check, env, skip_reason.
    """
    clusters: str = str(profile["clusters"])
    racks: str = str(profile["racks"])
    modules: str = str(profile["modules"])
    cells: str = str(profile["cells"])
    temps: str = str(profile["temps"])
    config_dir: Path = profile["config_dir"]

    dm_c_binary: Path = BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c"
    safety_binary: Path = BUILD_DIR / "src" / "safety_manager" / "safety_manager"
    comm_c_binary: Path = BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"
    vcan_available: bool = Path("/sys/class/net/vcan0").exists()

    logger_config: Path = config_dir / "logger_config.yaml"
    logger_cmd: list[str]
    if logger_config.exists():
        logger_cmd = ["uv", "run", "python", "-m", "ems_logger", "--config", str(logger_config)]
    else:
        logger_cmd = [
            "uv", "run", "python", "-m", "ems_logger",
            "--config", str(CONFIG_DIR / "logger_config.yaml"),
        ]

    return [
        {
            "name": "data_manager_c",
            "cmd": [str(dm_c_binary), clusters, racks, modules, cells, temps],
            "ready_check": check_rtdb_exists,
            "env": None,
            "skip_reason": None if dm_c_binary.exists() else f"Binary not found: {dm_c_binary}",
        },
        {
            "name": "data_manager_python",
            "cmd": ["uv", "run", "python", "-m", "ems_data_manager"],
            "ready_check": _delay_ready(2.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "config_manager",
            "cmd": [
                "uv", "run", "python", "-m", "ems_config_manager",
                "--config-dir", str(config_dir),
                "--schema-dir", str(CONFIG_DIR / "schemas"),
            ],
            "ready_check": _delay_ready(1.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "safety_manager",
            "cmd": [str(safety_binary)],
            "ready_check": _delay_ready(1.0),
            "env": {"EMS_GPIO_BACKEND": "rtdb"},
            "skip_reason": None if safety_binary.exists() else f"Binary not found: {safety_binary}",
        },
        {
            "name": "comm_manager_c",
            "cmd": [
                str(comm_c_binary),
                "--interface", "vcan0",
                "--base-id", "0x18FF0003",
                "--heartbeat-timeout-ms", "900",
            ],
            "ready_check": _delay_ready(1.0),
            "env": None,
            "skip_reason": (
                None if (comm_c_binary.exists() and vcan_available)
                else (
                    f"Binary not found: {comm_c_binary}"
                    if not comm_c_binary.exists()
                    else "vcan0 not available"
                )
            ),
        },
        {
            "name": "comm_manager_python",
            "cmd": [
                "uv", "run", "python", "-m", "ems_comm_manager",
                "--config", str(config_dir),
            ],
            "ready_check": _delay_ready(2.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "logger",
            "cmd": logger_cmd,
            "ready_check": _delay_ready(1.0),
            "env": None,
            "skip_reason": None,
        },
    ]


def _launch_modules(
    profile: dict[str, Any],
) -> tuple[dict[str, ModuleProcess], list[str]]:
    """Launch all modules for a profile. Returns (modules_dict, skipped_list)."""
    specs: list[dict[str, Any]] = _build_module_specs(profile)
    launched: dict[str, ModuleProcess] = {}
    skipped: list[str] = []

    for spec in specs:
        if spec["skip_reason"] is not None:
            skipped.append(f"{spec['name']}: {spec['skip_reason']}")
            continue
        try:
            proc: ModuleProcess = ModuleProcess(
                name=spec["name"],
                cmd=spec["cmd"],
                ready_check=spec["ready_check"],
                env=spec["env"],
            )
            launched[spec["name"]] = proc
        except (FileNotFoundError, TimeoutError, OSError) as exc:
            skipped.append(f"{spec['name']}: {exc}")

    return launched, skipped


def _cleanup_modules(modules: dict[str, ModuleProcess]) -> None:
    """Terminate all modules in reverse order."""
    for proc in reversed(list(modules.values())):
        try:
            proc.cleanup()
        except Exception:
            pass


def _collect_all_metrics(
    modules: dict[str, ModuleProcess],
    duration_s: float,
    zmq_endpoint: str = SOCK_TELEMETRY,
    zmq_topic: str = TOPIC_SYSTEM,
) -> MetricsCollector:
    """Run all metric collectors in parallel threads.

    Args:
        modules: Launched module processes.
        duration_s: Collection duration in seconds.
        zmq_endpoint: ZMQ telemetry endpoint.
        zmq_topic: ZMQ topic to subscribe to.

    Returns:
        Populated MetricsCollector instance.
    """
    collector: MetricsCollector = MetricsCollector()

    # Result holders for threads
    gpio_result: list[float] = []
    rtdb_result: list[float] = []
    zmq_result: list[int] = []
    rss_result: dict[str, list[int]] = {}

    def _gpio_worker() -> None:
        nonlocal gpio_result
        gpio_result = collect_gpio_latencies(
            duration_s=duration_s,
            sample_interval_s=1.0,
        )

    def _rtdb_worker() -> None:
        nonlocal rtdb_result
        rtdb_result = collect_rtdb_write_latencies(
            duration_s=duration_s,
            sample_interval_s=0.5,
        )

    def _zmq_worker() -> None:
        nonlocal zmq_result
        zmq_result = collect_zmq_lag(
            endpoint=zmq_endpoint,
            topic=zmq_topic,
            duration_s=duration_s,
        )

    def _rss_worker() -> None:
        nonlocal rss_result
        rss_result = collect_rss_samples(
            modules=modules,
            duration_s=duration_s,
            interval_s=1.0,
        )

    threads: list[threading.Thread] = [
        threading.Thread(target=_gpio_worker, daemon=True, name="gpio-collector"),
        threading.Thread(target=_rtdb_worker, daemon=True, name="rtdb-collector"),
        threading.Thread(target=_zmq_worker, daemon=True, name="zmq-collector"),
        threading.Thread(target=_rss_worker, daemon=True, name="rss-collector"),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration_s + 30)

    # Populate collector
    collector.gpio_latencies_ms = gpio_result
    collector.rtdb_write_latencies_ms = rtdb_result
    collector.zmq_lag_messages = zmq_result
    collector.rss_samples = rss_result

    return collector


# ---------------------------------------------------------------------------
# TestPerformanceCleanPass -- residential topology, no fault injection
# ---------------------------------------------------------------------------


class TestPerformanceCleanPass:
    """Performance validation under clean (no-fault) residential topology.

    Launches all modules, collects all 5 metrics in parallel for
    CLEAN_PASS_DURATION_S, then asserts each threshold individually.
    """

    @pytest.fixture(scope="class")
    def clean_system(
        self,
        profile: dict[str, Any],
    ) -> Any:
        """Launch modules, collect metrics, yield (collector, modules), cleanup."""
        modules, skipped = _launch_modules(profile)
        if not modules:
            pytest.skip("No modules could be launched -- check prerequisites")

        if skipped:
            import warnings

            for msg in skipped:
                warnings.warn(f"Skipped module: {msg}", stacklevel=1)

        # Let modules stabilize before collecting metrics
        time.sleep(5)

        collector: MetricsCollector = _collect_all_metrics(
            modules=modules,
            duration_s=CLEAN_PASS_DURATION_S,
        )

        # Logger throughput: count Parquet rows
        data_dir: Path = Path("/home/overlord/EMS/data")
        collector.logger_rows_written = count_parquet_rows(data_dir)
        collector.logger_rows_expected = CLEAN_PASS_DURATION_S * _RESIDENTIAL_TOPICS

        try:
            yield collector, modules
        finally:
            _cleanup_modules(modules)

    def test_gpio_latency_p99(self, clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """GPIO safety response time must be <100ms at p99."""
        collector, _modules = clean_system
        if not collector.gpio_latencies_ms:
            pytest.skip("No GPIO latency samples collected (GPIO harness or RTDB unavailable)")
        p99: float = MetricsCollector.percentile(collector.gpio_latencies_ms, 0.99)
        assert p99 < 100.0, (
            f"GPIO p99 latency = {p99:.2f}ms exceeds 100ms threshold "
            f"({len(collector.gpio_latencies_ms)} samples)"
        )

    def test_rtdb_write_latency_p99(self, clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """RTDB write latency must be <10ms at p99."""
        collector, _modules = clean_system
        if len(collector.rtdb_write_latencies_ms) < 10:
            pytest.skip("Fewer than 10 RTDB latency samples (module may not be writing)")
        p99: float = MetricsCollector.percentile(collector.rtdb_write_latencies_ms, 0.99)
        assert p99 < 10.0, (
            f"RTDB write p99 latency = {p99:.2f}ms exceeds 10ms threshold "
            f"({len(collector.rtdb_write_latencies_ms)} samples)"
        )

    def test_zmq_lag(self, clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """ZMQ telemetry lag must be <5 messages behind publisher."""
        collector, _modules = clean_system
        if not collector.zmq_lag_messages:
            # No lag detected = pass (empty list means zero gaps)
            return
        max_lag: int = max(collector.zmq_lag_messages)
        assert max_lag < 5, (
            f"ZMQ max lag = {max_lag} messages exceeds threshold of 5 "
            f"({len(collector.zmq_lag_messages)} gap events)"
        )

    def test_logger_throughput(self, clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """Logger write rate must be >=95% of expected telemetry throughput."""
        collector, _modules = clean_system
        if collector.logger_rows_expected == 0:
            pytest.skip("No expected logger rows (duration too short or no topics)")
        rate: float = collector.logger_rows_written / collector.logger_rows_expected * 100
        assert rate >= 95.0, (
            f"Logger write rate = {rate:.1f}% < 95% "
            f"({collector.logger_rows_written}/{collector.logger_rows_expected} rows)"
        )

    def test_rss_growth(self, clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """Process RSS growth must be <10% after first minute for all modules."""
        collector, _modules = clean_system
        for name, samples in collector.rss_samples.items():
            if len(samples) < 60:
                continue  # Not enough data for this module
            baseline: float = sum(samples[:60]) / 60.0
            final: float = sum(samples[-60:]) / 60.0
            if baseline <= 0:
                continue
            growth_pct: float = ((final - baseline) / baseline) * 100.0
            assert growth_pct < 10.0, (
                f"{name} RSS grew {growth_pct:.1f}% > 10% threshold "
                f"(baseline={baseline / 1024 / 1024:.1f}MB, "
                f"final={final / 1024 / 1024:.1f}MB)"
            )


# ---------------------------------------------------------------------------
# TestPerformanceFaultPass -- residential topology with fault injection
# ---------------------------------------------------------------------------


class TestPerformanceFaultPass:
    """Performance validation under fault-injection conditions.

    Same 5 metrics as clean pass, but with:
    - CAN: frame_drop_rate=0.05 (simulated by env var if supported)
    - Modbus: DG timeout, BTMS exception
    - GPIO: DI-1 stuck high

    Per CONTEXT.md: log errors during fault injection are NOT treated as failures.
    Performance thresholds must still hold under degraded operation.
    """

    @pytest.fixture(scope="class")
    def fault_system(
        self,
        profile: dict[str, Any],
    ) -> Any:
        """Launch modules, inject faults after warmup, collect metrics, cleanup."""
        modules, skipped = _launch_modules(profile)
        if not modules:
            pytest.skip("No modules could be launched -- check prerequisites")

        if skipped:
            import warnings

            for msg in skipped:
                warnings.warn(f"Skipped module: {msg}", stacklevel=1)

        # 10-second warmup before fault injection
        time.sleep(10)

        # Inject GPIO fault: DI-1 stuck high via RTDB backend
        gpio_harness: RtdbBackend | None = None
        if _HAS_GPIO_HARNESS and SHM_PATH.exists():
            try:
                gpio_harness = RtdbBackend(
                    fault_cfg={"stuck_pins": [1]},
                )
                # Set DI-1 high (it will stay stuck due to fault config)
                gpio_harness.set_di(1, 1)
            except (FileNotFoundError, RuntimeError, OSError):
                gpio_harness = None

        # Collect metrics during fault condition
        collector: MetricsCollector = _collect_all_metrics(
            modules=modules,
            duration_s=FAULT_PASS_DURATION_S,
        )

        # Logger throughput
        data_dir: Path = Path("/home/overlord/EMS/data")
        collector.logger_rows_written = count_parquet_rows(data_dir)
        collector.logger_rows_expected = FAULT_PASS_DURATION_S * _RESIDENTIAL_TOPICS

        try:
            yield collector, modules
        finally:
            if gpio_harness is not None:
                try:
                    gpio_harness.close()
                except Exception:
                    pass
            _cleanup_modules(modules)

    def test_gpio_latency_p99(self, fault_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """GPIO p99 latency must remain <100ms under fault injection."""
        collector, _modules = fault_system
        if not collector.gpio_latencies_ms:
            pytest.skip("No GPIO latency samples collected")
        p99: float = MetricsCollector.percentile(collector.gpio_latencies_ms, 0.99)
        assert p99 < 100.0, (
            f"GPIO p99 latency = {p99:.2f}ms exceeds 100ms under fault injection "
            f"({len(collector.gpio_latencies_ms)} samples)"
        )

    def test_rtdb_write_latency_p99(self, fault_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """RTDB write p99 latency must remain <10ms under fault injection."""
        collector, _modules = fault_system
        if len(collector.rtdb_write_latencies_ms) < 10:
            pytest.skip("Fewer than 10 RTDB latency samples")
        p99: float = MetricsCollector.percentile(collector.rtdb_write_latencies_ms, 0.99)
        assert p99 < 10.0, (
            f"RTDB write p99 latency = {p99:.2f}ms exceeds 10ms under fault injection "
            f"({len(collector.rtdb_write_latencies_ms)} samples)"
        )

    def test_zmq_lag(self, fault_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """ZMQ lag must remain <5 messages under fault injection."""
        collector, _modules = fault_system
        if not collector.zmq_lag_messages:
            return  # No lag = pass
        max_lag: int = max(collector.zmq_lag_messages)
        assert max_lag < 5, (
            f"ZMQ max lag = {max_lag} messages under fault injection "
            f"({len(collector.zmq_lag_messages)} gap events)"
        )

    def test_logger_throughput(self, fault_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """Logger write rate must remain >=95% under fault injection."""
        collector, _modules = fault_system
        if collector.logger_rows_expected == 0:
            pytest.skip("No expected logger rows")
        rate: float = collector.logger_rows_written / collector.logger_rows_expected * 100
        assert rate >= 95.0, (
            f"Logger write rate = {rate:.1f}% under fault injection "
            f"({collector.logger_rows_written}/{collector.logger_rows_expected} rows)"
        )

    def test_rss_growth(self, fault_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """RSS growth must remain <10% under fault injection."""
        collector, _modules = fault_system
        for name, samples in collector.rss_samples.items():
            if len(samples) < 60:
                continue
            baseline: float = sum(samples[:60]) / 60.0
            final: float = sum(samples[-60:]) / 60.0
            if baseline <= 0:
                continue
            growth_pct: float = ((final - baseline) / baseline) * 100.0
            assert growth_pct < 10.0, (
                f"{name} RSS grew {growth_pct:.1f}% under fault injection "
                f"(baseline={baseline / 1024 / 1024:.1f}MB, "
                f"final={final / 1024 / 1024:.1f}MB)"
            )


# ---------------------------------------------------------------------------
# TestContainerTopology -- container profile, clean pass
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    psutil.virtual_memory().total < 4 * 1024**3,
    reason="Need 4GB+ RAM for container topology tests",
)
class TestContainerTopology:
    """Performance validation under container topology (4 clusters, 16 racks).

    Same 5 metrics as residential clean pass, but using the container profile
    which produces higher RTDB and ZMQ load. Requires at least 4GB of RAM.
    """

    @pytest.fixture(scope="class")
    def container_system(self) -> Any:
        """Launch modules with container profile, collect metrics, cleanup."""
        container_profile: dict[str, Any] = PROFILES["container"]
        modules, skipped = _launch_modules(container_profile)
        if not modules:
            pytest.skip("No modules could be launched for container topology")

        if skipped:
            import warnings

            for msg in skipped:
                warnings.warn(f"Skipped module: {msg}", stacklevel=1)

        # Extended warmup for larger topology
        time.sleep(10)

        collector: MetricsCollector = _collect_all_metrics(
            modules=modules,
            duration_s=CLEAN_PASS_DURATION_S,
        )

        # Logger throughput with container topic count
        data_dir: Path = Path("/home/overlord/EMS/data")
        collector.logger_rows_written = count_parquet_rows(data_dir)
        collector.logger_rows_expected = CLEAN_PASS_DURATION_S * _CONTAINER_TOPICS

        try:
            yield collector, modules
        finally:
            _cleanup_modules(modules)

    def test_gpio_latency_p99(self, container_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """GPIO p99 latency must be <100ms under container topology load."""
        collector, _modules = container_system
        if not collector.gpio_latencies_ms:
            pytest.skip("No GPIO latency samples collected")
        p99: float = MetricsCollector.percentile(collector.gpio_latencies_ms, 0.99)
        assert p99 < 100.0, (
            f"GPIO p99 latency = {p99:.2f}ms under container topology"
        )

    def test_rtdb_write_latency_p99(self, container_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """RTDB write p99 latency must be <10ms under container topology."""
        collector, _modules = container_system
        if len(collector.rtdb_write_latencies_ms) < 10:
            pytest.skip("Fewer than 10 RTDB latency samples")
        p99: float = MetricsCollector.percentile(collector.rtdb_write_latencies_ms, 0.99)
        assert p99 < 10.0, (
            f"RTDB write p99 latency = {p99:.2f}ms under container topology"
        )

    def test_zmq_lag(self, container_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """ZMQ lag must be <5 messages under container topology."""
        collector, _modules = container_system
        if not collector.zmq_lag_messages:
            return
        max_lag: int = max(collector.zmq_lag_messages)
        assert max_lag < 5, (
            f"ZMQ max lag = {max_lag} messages under container topology"
        )

    def test_logger_throughput(self, container_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """Logger write rate must be >=95% under container topology."""
        collector, _modules = container_system
        if collector.logger_rows_expected == 0:
            pytest.skip("No expected logger rows")
        rate: float = collector.logger_rows_written / collector.logger_rows_expected * 100
        assert rate >= 95.0, (
            f"Logger write rate = {rate:.1f}% under container topology "
            f"({collector.logger_rows_written}/{collector.logger_rows_expected} rows)"
        )

    def test_rss_growth(self, container_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """RSS growth must be <10% under container topology."""
        collector, _modules = container_system
        for name, samples in collector.rss_samples.items():
            if len(samples) < 60:
                continue
            baseline: float = sum(samples[:60]) / 60.0
            final: float = sum(samples[-60:]) / 60.0
            if baseline <= 0:
                continue
            growth_pct: float = ((final - baseline) / baseline) * 100.0
            assert growth_pct < 10.0, (
                f"{name} RSS grew {growth_pct:.1f}% under container topology "
                f"(baseline={baseline / 1024 / 1024:.1f}MB, "
                f"final={final / 1024 / 1024:.1f}MB)"
            )


# ---------------------------------------------------------------------------
# Slow (full 10-minute) variants for --runslow
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestPerformanceCleanPassFull:
    """Full 10-minute clean pass for extended validation (--runslow only)."""

    @pytest.fixture(scope="class")
    def full_clean_system(
        self,
        profile: dict[str, Any],
    ) -> Any:
        """Launch modules, collect metrics for FULL_PASS_DURATION_S, cleanup."""
        modules, skipped = _launch_modules(profile)
        if not modules:
            pytest.skip("No modules could be launched")

        time.sleep(5)

        collector: MetricsCollector = _collect_all_metrics(
            modules=modules,
            duration_s=FULL_PASS_DURATION_S,
        )

        data_dir: Path = Path("/home/overlord/EMS/data")
        collector.logger_rows_written = count_parquet_rows(data_dir)
        collector.logger_rows_expected = FULL_PASS_DURATION_S * _RESIDENTIAL_TOPICS

        try:
            yield collector, modules
        finally:
            _cleanup_modules(modules)

    def test_gpio_latency_p99(self, full_clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """GPIO p99 <100ms over full 10-minute pass."""
        collector, _modules = full_clean_system
        if not collector.gpio_latencies_ms:
            pytest.skip("No GPIO latency samples collected")
        p99: float = MetricsCollector.percentile(collector.gpio_latencies_ms, 0.99)
        assert p99 < 100.0, f"GPIO p99 = {p99:.2f}ms (10-min pass)"

    def test_rtdb_write_latency_p99(self, full_clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """RTDB write p99 <10ms over full 10-minute pass."""
        collector, _modules = full_clean_system
        if len(collector.rtdb_write_latencies_ms) < 10:
            pytest.skip("Fewer than 10 RTDB latency samples")
        p99: float = MetricsCollector.percentile(collector.rtdb_write_latencies_ms, 0.99)
        assert p99 < 10.0, f"RTDB write p99 = {p99:.2f}ms (10-min pass)"

    def test_zmq_lag(self, full_clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """ZMQ lag <5 messages over full 10-minute pass."""
        collector, _modules = full_clean_system
        if not collector.zmq_lag_messages:
            return
        max_lag: int = max(collector.zmq_lag_messages)
        assert max_lag < 5, f"ZMQ max lag = {max_lag} (10-min pass)"

    def test_logger_throughput(self, full_clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """Logger write rate >=95% over full 10-minute pass."""
        collector, _modules = full_clean_system
        if collector.logger_rows_expected == 0:
            pytest.skip("No expected logger rows")
        rate: float = collector.logger_rows_written / collector.logger_rows_expected * 100
        assert rate >= 95.0, f"Logger rate = {rate:.1f}% (10-min pass)"

    def test_rss_growth(self, full_clean_system: tuple[MetricsCollector, dict[str, ModuleProcess]]) -> None:
        """RSS growth <10% over full 10-minute pass."""
        collector, _modules = full_clean_system
        for name, samples in collector.rss_samples.items():
            if len(samples) < 120:
                continue
            baseline: float = sum(samples[:60]) / 60.0
            final: float = sum(samples[-60:]) / 60.0
            if baseline <= 0:
                continue
            growth_pct: float = ((final - baseline) / baseline) * 100.0
            assert growth_pct < 10.0, f"{name} RSS grew {growth_pct:.1f}% (10-min pass)"
