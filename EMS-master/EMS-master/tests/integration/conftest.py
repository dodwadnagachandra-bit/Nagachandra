"""Integration test shared fixtures, helpers, and process management.

Provides:
- ModuleProcess: subprocess wrapper with health check, signal control, RSS monitoring
- MetricsCollector: performance metric accumulation and threshold assertion
- Profile selector: residential and container topology configurations
- Cleanup fixtures: SHM and IPC socket teardown
- Helper functions: RTDB checks, ZMQ receive checks, multi-criteria polling
"""

from __future__ import annotations

import glob
import os
import signal
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import psutil
import pytest
import zmq

from ems_common.ipc import SOCK_TELEMETRY
from ems_common.rtdb import attach_rtdb, detach_rtdb

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
BUILD_DIR: Path = PROJECT_ROOT / "build"
CONFIG_DIR: Path = PROJECT_ROOT / "config"
SHM_NAME: str = "ems_rtdb"
SHM_PATH: Path = Path("/dev/shm") / SHM_NAME

# ---------------------------------------------------------------------------
# Topology profiles
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict[str, Any]] = {
    "residential": {
        "clusters": 1,
        "racks": 4,
        "modules": 10,
        "cells": 16,
        "temps": 8,
        "config_dir": CONFIG_DIR / "profiles" / "residential",
    },
    "container": {
        "clusters": 4,
        "racks": 16,
        "modules": 20,
        "cells": 108,
        "temps": 40,
        "config_dir": CONFIG_DIR / "profiles" / "container",
    },
}


# ---------------------------------------------------------------------------
# ModuleProcess
# ---------------------------------------------------------------------------


class ModuleProcess:
    """Wrapper around subprocess.Popen with health check and signal control.

    Launches a module as a subprocess, waits for a readiness condition,
    and provides signal-based kill/restart plus psutil RSS monitoring.
    """

    def __init__(
        self,
        name: str,
        cmd: list[str],
        ready_check: Callable[[], bool],
        env: dict[str, str] | None = None,
    ) -> None:
        self.name: str = name
        self.cmd: list[str] = cmd
        self.ready_check: Callable[[], bool] = ready_check
        self._env: dict[str, str] | None = env
        self.proc: subprocess.Popen[bytes] | None = None
        self.start()

    def start(self) -> None:
        """Launch the subprocess and wait for readiness."""
        merged_env: dict[str, str] = {**os.environ, **(self._env or {})}
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged_env,
            preexec_fn=os.setpgrp,
        )
        self._wait_ready()

    def _wait_ready(self, timeout: float = 15.0) -> None:
        """Poll ready_check until True or timeout."""
        deadline: float = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready_check():
                return
            time.sleep(0.2)
        raise TimeoutError(f"{self.name} not ready within {timeout}s")

    def restart(self) -> None:
        """Terminate the existing process (if alive) and start a new one."""
        if self.proc is not None and self.is_alive:
            self.terminate()
        self.start()

    def kill(self, sig: int = signal.SIGKILL) -> None:
        """Send a signal to the process and wait for exit."""
        if self.proc is not None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def terminate(self) -> None:
        """Send SIGTERM, wait 5s, fall back to SIGKILL."""
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    @property
    def pid(self) -> int:
        """Return the PID of the managed process."""
        if self.proc is None:
            raise RuntimeError(f"{self.name}: no process running")
        return self.proc.pid

    @property
    def rss_bytes(self) -> int:
        """Return the RSS in bytes via psutil, or 0 if process exited."""
        if self.proc is None:
            return 0
        try:
            return psutil.Process(self.proc.pid).memory_info().rss
        except psutil.NoSuchProcess:
            return 0

    @property
    def is_alive(self) -> bool:
        """Return True if the process has not exited."""
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def cleanup(self) -> None:
        """Terminate process and clean up process group."""
        if self.proc is not None and self.is_alive:
            self.terminate()
        # Best-effort process group cleanup
        if self.proc is not None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------


@dataclass
class MetricsCollector:
    """Collects performance samples over a test duration and asserts thresholds."""

    gpio_latencies_ms: list[float] = field(default_factory=list)
    rtdb_write_latencies_ms: list[float] = field(default_factory=list)
    zmq_lag_messages: list[int] = field(default_factory=list)
    rss_samples: dict[str, list[int]] = field(default_factory=dict)
    logger_rows_written: int = 0
    logger_rows_expected: int = 0

    @staticmethod
    def percentile(data: list[float], pct: float) -> float:
        """Compute the given percentile from a sorted copy of data."""
        if not data:
            return 0.0
        sorted_data: list[float] = sorted(data)
        idx: int = int(len(sorted_data) * pct)
        idx = min(idx, len(sorted_data) - 1)
        return sorted_data[idx]

    def assert_thresholds(self) -> None:
        """Assert all 5 performance thresholds from CONTEXT.md.

        Thresholds:
        - GPIO p99 < 100ms
        - RTDB write p99 < 10ms
        - ZMQ max lag < 5 messages
        - Logger write rate >= 95%
        - RSS growth < 10% after first minute (compare first 60 vs last 60 samples)
        """
        # GPIO latency p99
        if self.gpio_latencies_ms:
            gpio_p99: float = self.percentile(self.gpio_latencies_ms, 0.99)
            assert gpio_p99 < 100.0, f"GPIO p99={gpio_p99:.1f}ms > 100ms"

        # RTDB write latency p99
        if self.rtdb_write_latencies_ms:
            rtdb_p99: float = self.percentile(self.rtdb_write_latencies_ms, 0.99)
            assert rtdb_p99 < 10.0, f"RTDB write p99={rtdb_p99:.1f}ms > 10ms"

        # ZMQ lag
        if self.zmq_lag_messages:
            max_lag: int = max(self.zmq_lag_messages)
            assert max_lag < 5, f"ZMQ lag={max_lag} messages > 5"

        # Logger write rate
        if self.logger_rows_expected > 0:
            write_rate: float = self.logger_rows_written / self.logger_rows_expected * 100
            assert write_rate >= 95.0, (
                f"Logger write rate={write_rate:.1f}% < 95% "
                f"({self.logger_rows_written}/{self.logger_rows_expected})"
            )

        # RSS growth
        for name, samples in self.rss_samples.items():
            if len(samples) < 120:
                continue  # Need at least 2 minutes of 1Hz samples
            baseline: float = sum(samples[:60]) / 60.0
            final: float = sum(samples[-60:]) / 60.0
            if baseline > 0:
                growth_pct: float = ((final - baseline) / baseline) * 100.0
                assert growth_pct < 10.0, (
                    f"{name} RSS grew {growth_pct:.1f}% > 10% "
                    f"(baseline={baseline / 1024 / 1024:.1f}MB, "
                    f"final={final / 1024 / 1024:.1f}MB)"
                )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def check_rtdb_exists() -> bool:
    """Return True if the RTDB shared memory segment exists."""
    return SHM_PATH.exists()


def check_rtdb_fresh(max_age_ms: float = 2000.0) -> bool:
    """Check if RTDB system.last_update_ms is within max_age_ms of wall clock.

    Returns True if RTDB is fresh, False on any error or stale data.
    """
    try:
        shm, rtdb = attach_rtdb()
        last_update_ms: int = rtdb.system.last_update_ms
        del rtdb
        detach_rtdb(shm)
        if last_update_ms == 0:
            return False
        # Compare against wall clock (approximate, good enough for health check)
        now_ms: float = time.time() * 1000.0
        age_ms: float = now_ms - last_update_ms
        return 0 <= age_ms <= max_age_ms
    except (FileNotFoundError, RuntimeError, OSError):
        return False


def check_zmq_receiving(
    endpoint: str = SOCK_TELEMETRY,
    topic: str = "",
    timeout_ms: int = 3000,
) -> bool:
    """Connect a SUB socket and try to receive one message within timeout.

    Returns True if a message was received, False otherwise.
    """
    ctx: zmq.Context = zmq.Context()
    sub: zmq.Socket = ctx.socket(zmq.SUB)
    received: bool = False
    try:
        sub.connect(endpoint)
        sub.setsockopt_string(zmq.SUBSCRIBE, topic)
        sub.setsockopt(zmq.RCVTIMEO, timeout_ms)
        try:
            sub.recv()
            received = True
        except zmq.Again:
            received = False
    finally:
        sub.close()
        ctx.term()
    return received


def _check_http_health(port: int) -> bool:
    """Return True if hmi_server health endpoint responds 200.

    Used as a ready_check callback for ModuleProcess when launching
    hmi_server in integration tests.
    """
    try:
        resp: httpx.Response = httpx.get(
            f"http://127.0.0.1:{port}/api/health/", timeout=1.0,
        )
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.ReadTimeout):
        return False


def wait_for_criteria(
    checks: dict[str, Callable[[], bool]],
    timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> dict[str, bool]:
    """Poll all checks until all True or timeout.

    Args:
        checks: Name -> callable returning bool.
        timeout: Maximum seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        Final state dict mapping check name to pass/fail.
    """
    deadline: float = time.monotonic() + timeout
    state: dict[str, bool] = {name: False for name in checks}

    while time.monotonic() < deadline:
        for name, check_fn in checks.items():
            if not state[name]:
                try:
                    state[name] = check_fn()
                except Exception:
                    state[name] = False
        if all(state.values()):
            break
        time.sleep(poll_interval)

    return state


# ---------------------------------------------------------------------------
# pytest configuration
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register --profile option for topology selection."""
    parser.addoption(
        "--profile",
        choices=["residential", "container"],
        default="residential",
        help="Topology profile for integration tests (default: residential)",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cleanup_shm() -> Generator[None, None, None]:
    """Remove RTDB shared memory before and after each test."""
    _force_cleanup_shm()
    yield
    _force_cleanup_shm()


def _force_cleanup_shm() -> None:
    """Remove the RTDB shm file if it exists."""
    if SHM_PATH.exists():
        try:
            os.unlink(str(SHM_PATH))
        except OSError:
            pass


@pytest.fixture(autouse=True)
def cleanup_ipc_sockets() -> Generator[None, None, None]:
    """Remove IPC socket files in teardown (best-effort)."""
    yield
    for sock_file in glob.glob("/run/ems/*.sock"):
        try:
            os.unlink(sock_file)
        except OSError:
            pass


@pytest.fixture(scope="session")
def profile_name(request: pytest.FixtureRequest) -> str:
    """Return the selected topology profile name."""
    name: str = request.config.getoption("--profile")
    return name


@pytest.fixture(scope="session")
def profile(profile_name: str) -> dict[str, Any]:
    """Return the full profile dict for the selected topology."""
    return PROFILES[profile_name]


@pytest.fixture(scope="session")
def ensure_build() -> None:
    """Assert that C binaries are built. Skip all tests if not."""
    if not BUILD_DIR.exists():
        pytest.skip("Build directory not found -- run 'make build' first")

    binaries: list[Path] = [
        BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c",
        BUILD_DIR / "src" / "safety_manager" / "safety_manager",
        BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c",
    ]
    for binary in binaries:
        if not binary.exists():
            pytest.skip(f"Binary not found: {binary} -- run 'make build' first")


@pytest.fixture(scope="session")
def ensure_vcan() -> None:
    """Check vcan0 is up. Skip CAN-dependent tests if not."""
    vcan_path: Path = Path("/sys/class/net/vcan0")
    if not vcan_path.exists():
        pytest.skip("vcan0 not available -- run 'make sim' to set up virtual CAN")


@pytest.fixture(scope="session")
def ensure_run_ems() -> None:
    """Create /run/ems/ directory if it doesn't exist (best-effort)."""
    run_ems: Path = Path("/run/ems")
    if not run_ems.exists():
        try:
            run_ems.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass  # Tests may use TCP endpoints instead
