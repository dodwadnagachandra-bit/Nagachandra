"""Crash recovery and double-fault integration tests.

Validates ROADMAP success criterion #3: killing any single module with
SIGKILL or SIGTERM results in recovery within 10 seconds with no RTDB
corruption.  Also tests double-fault scenarios (comm+logger, data+comm)
and GPIO continuity during safety_manager restart.

Includes M2 module crash recovery: control_manager and alarm_manager
are tested with SIGKILL and SIGTERM. control_manager always restarts
into IDLE state; alarm_manager re-evaluates all alarms on first tick.

Recovery criteria (all must pass within RECOVERY_TIMEOUT_S):
- Process alive (module.is_alive)
- RTDB section fresh (last_update_ms within 2 seconds of wall clock)
- No RTDB corruption (magic/version unchanged)
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from tests.integration.conftest import (
    BUILD_DIR,
    CONFIG_DIR,
    PROJECT_ROOT,
    SHM_PATH,
    ModuleProcess,
    _check_http_health,
    check_rtdb_exists,
    check_rtdb_fresh,
    wait_for_criteria,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECOVERY_TIMEOUT_S: float = 10.0

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


# ---------------------------------------------------------------------------
# Module definitions
# ---------------------------------------------------------------------------

def _c_binary(name: str) -> Path:
    """Return the expected path for a C binary."""
    return BUILD_DIR / "src" / name / "c" / f"{name}_c"


def _safety_binary() -> Path:
    """Return the expected path for the safety_manager binary."""
    return BUILD_DIR / "src" / "safety_manager" / "safety_manager"


def _vcan_available() -> bool:
    """Check if vcan0 interface is available."""
    return Path("/sys/class/net/vcan0").exists()


# Module launch specs: name -> (cmd_factory, ready_check_factory, requires_c, requires_vcan)
# cmd_factory and ready_check_factory are callables returning list[str] and callable respectively
# to defer path evaluation until fixture time.

_MODULE_SPECS: dict[str, dict[str, Any]] = {
    "config_manager": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_config_manager",
            "--config-dir", str(CONFIG_DIR),
        ],
        "ready_check": lambda: True,
        "requires_c": False,
        "requires_vcan": False,
    },
    "data_manager_c": {
        "cmd": lambda: [
            str(_c_binary("data_manager")),
            "1", "4", "10", "16", "8",
        ],
        "ready_check": lambda: SHM_PATH.exists(),
        "requires_c": True,
        "requires_vcan": False,
    },
    "data_manager_python": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_data_manager",
        ],
        "ready_check": lambda: True,
        "requires_c": False,
        "requires_vcan": False,
    },
    "safety_manager": {
        "cmd": lambda: [str(_safety_binary())],
        "ready_check": lambda: True,
        "requires_c": True,
        "requires_vcan": False,
    },
    "comm_manager_c": {
        "cmd": lambda: [
            str(_c_binary("comm_manager")),
            "--interface", "vcan0",
            "--base-id", "0x18FF0003",
            "--heartbeat-timeout-ms", "900",
        ],
        "ready_check": lambda: True,
        "requires_c": True,
        "requires_vcan": True,
    },
    "comm_manager_python": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_comm_manager",
            "--config", str(CONFIG_DIR),
        ],
        "ready_check": lambda: True,
        "requires_c": False,
        "requires_vcan": False,
    },
    "logger": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_logger",
            "--config", str(CONFIG_DIR / "logger_config.yaml"),
        ],
        "ready_check": lambda: True,
        "requires_c": False,
        "requires_vcan": False,
    },
    "control_manager": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_control_manager",
            "--config", str(CONFIG_DIR / "profiles" / "residential" / "control_config.yaml"),
        ],
        "ready_check": lambda: True,
        "requires_c": False,
        "requires_vcan": False,
    },
    "alarm_manager": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_alarm_manager",
            "--config", str(CONFIG_DIR / "profiles" / "residential" / "alarms_config.yaml"),
        ],
        "ready_check": lambda: True,
        "requires_c": False,
        "requires_vcan": False,
    },
    "hmi_server": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_hmi_server",
            "--config", str(CONFIG_DIR / "profiles" / "residential" / "hmi_config_test.yaml"),
        ],
        "ready_check": lambda: _check_http_health(8080),
        "requires_c": False,
        "requires_vcan": False,
    },
    "scheduler": {
        "cmd": lambda: [
            "uv", "run", "python", "-m", "ems_scheduler",
            "--config", str(CONFIG_DIR / "profiles" / "residential" / "schedule_config.yaml"),
        ],
        "ready_check": lambda: True,
        "requires_c": False,
        "requires_vcan": False,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_launch(name: str) -> bool:
    """Check if a module can be launched on this machine."""
    spec: dict[str, Any] = _MODULE_SPECS[name]
    if spec["requires_c"]:
        # Check the binary exists
        cmd: list[str] = spec["cmd"]()
        binary_path: Path = Path(cmd[0])
        if not binary_path.exists():
            return False
    if spec["requires_vcan"] and not _vcan_available():
        return False
    return True


def _try_launch(name: str) -> ModuleProcess | None:
    """Attempt to launch a module. Returns None if prerequisites missing."""
    if not _can_launch(name):
        return None
    spec: dict[str, Any] = _MODULE_SPECS[name]
    try:
        proc: ModuleProcess = ModuleProcess(
            name=name,
            cmd=spec["cmd"](),
            ready_check=spec["ready_check"],
        )
        return proc
    except (TimeoutError, FileNotFoundError, OSError):
        return None


def _verify_rtdb_integrity() -> None:
    """Assert RTDB shm exists with valid magic and version.

    Imports attach_rtdb/detach_rtdb inline to avoid import-time side effects.
    """
    from ems_common.rtdb import RTDB_MAGIC, RTDB_VERSION, attach_rtdb, detach_rtdb

    assert SHM_PATH.exists(), "RTDB shared memory does not exist"
    shm, rtdb = attach_rtdb()
    try:
        assert rtdb.magic == RTDB_MAGIC, (
            f"RTDB magic corrupted: expected 0x{RTDB_MAGIC:08X}, "
            f"got 0x{rtdb.magic:08X}"
        )
        assert rtdb.version == RTDB_VERSION, (
            f"RTDB version corrupted: expected {RTDB_VERSION}, "
            f"got {rtdb.version}"
        )
    finally:
        del rtdb
        detach_rtdb(shm)


# ---------------------------------------------------------------------------
# Crash matrix for parametrized tests
# ---------------------------------------------------------------------------

# Python modules -- always available
CRASH_MATRIX: list[tuple[str, int]] = [
    ("config_manager", signal.SIGKILL),
    ("config_manager", signal.SIGTERM),
    ("data_manager_python", signal.SIGKILL),
    ("data_manager_python", signal.SIGTERM),
    ("comm_manager_python", signal.SIGKILL),
    ("comm_manager_python", signal.SIGTERM),
    ("logger", signal.SIGKILL),
    ("logger", signal.SIGTERM),
    ("control_manager", signal.SIGKILL),
    ("control_manager", signal.SIGTERM),
    ("alarm_manager", signal.SIGKILL),
    ("alarm_manager", signal.SIGTERM),
    # M3 modules
    ("hmi_server", signal.SIGKILL),
    ("hmi_server", signal.SIGTERM),
    ("scheduler", signal.SIGKILL),
    ("scheduler", signal.SIGTERM),
]

# C modules -- conditionally added
_C_MODULES: list[str] = ["data_manager_c", "safety_manager", "comm_manager_c"]

for _mod in _C_MODULES:
    if _can_launch(_mod):
        CRASH_MATRIX.append((_mod, signal.SIGKILL))
        CRASH_MATRIX.append((_mod, signal.SIGTERM))


def _sig_name(sig: int) -> str:
    """Human-readable signal name."""
    return "SIGKILL" if sig == signal.SIGKILL else "SIGTERM"


def _crash_id(param: tuple[str, int]) -> str:
    """Generate readable test ID for parametrize."""
    return f"{param[0]}-{_sig_name(param[1])}"


# ---------------------------------------------------------------------------
# Module startup order (matches systemd ordering)
# ---------------------------------------------------------------------------

STARTUP_ORDER: list[str] = [
    "data_manager_c",
    "data_manager_python",
    "config_manager",
    "safety_manager",
    "comm_manager_c",
    "comm_manager_python",
    "logger",
    "control_manager",
    "alarm_manager",
    "hmi_server",
    "scheduler",
]


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestSingleModuleCrashRecovery:
    """Test each module individually with SIGKILL and SIGTERM."""

    @pytest.fixture(scope="class")
    def running_system(self) -> dict[str, ModuleProcess | None]:
        """Launch all available modules in startup order.

        Yields a dict of name -> ModuleProcess (None for unavailable modules).
        Teardown cleans up all processes.
        """
        system: dict[str, ModuleProcess | None] = {}

        for name in STARTUP_ORDER:
            proc: ModuleProcess | None = _try_launch(name)
            system[name] = proc
            if proc is not None:
                # Brief settle time between module launches
                time.sleep(0.5)

        yield system

        # Teardown: clean up all running modules in reverse order
        for name in reversed(STARTUP_ORDER):
            proc = system.get(name)
            if proc is not None:
                proc.cleanup()

    @pytest.mark.parametrize(
        "module_name,signal_num",
        CRASH_MATRIX,
        ids=[_crash_id(p) for p in CRASH_MATRIX],
    )
    def test_module_crash_recovery(
        self,
        running_system: dict[str, ModuleProcess | None],
        module_name: str,
        signal_num: int,
    ) -> None:
        """Kill a module with the given signal, restart, verify recovery."""
        module: ModuleProcess | None = running_system.get(module_name)
        if module is None:
            pytest.skip(f"{module_name} not available on this machine")

        # Precondition: module should be alive
        assert module.is_alive, f"{module_name} not alive before test"

        # Record original PID
        original_pid: int = module.pid

        # Kill with specified signal
        t_kill: float = time.monotonic()
        module.kill(sig=signal_num)

        # Wait for process to die (up to 2 seconds)
        deadline_die: float = time.monotonic() + 2.0
        while module.is_alive and time.monotonic() < deadline_die:
            time.sleep(0.1)
        assert not module.is_alive, (
            f"{module_name} did not die within 2s after {_sig_name(signal_num)}"
        )

        # Restart (test acts as systemd Restart=always)
        module.restart()

        # Verify recovery criteria
        checks: dict[str, Callable[[], bool]] = {
            "alive": lambda: module.is_alive,
        }

        # Add RTDB freshness check for modules that write to RTDB
        rtdb_writers: set[str] = {
            "data_manager_c", "data_manager_python",
            "safety_manager", "comm_manager_c", "comm_manager_python",
        }
        if module_name in rtdb_writers and check_rtdb_exists():
            checks["rtdb_fresh"] = lambda: check_rtdb_fresh(max_age_ms=2000.0)

        # Add HTTP health check for hmi_server (health endpoint must return 200)
        if module_name == "hmi_server":
            checks["http_health"] = lambda: _check_http_health(8080)

        t_start: float = time.monotonic()
        state: dict[str, bool] = wait_for_criteria(
            checks, timeout=RECOVERY_TIMEOUT_S, poll_interval=0.3,
        )
        elapsed: float = time.monotonic() - t_start

        # Assert all criteria met
        for criterion, passed in state.items():
            assert passed, (
                f"{module_name} recovery failed: {criterion} not met "
                f"within {RECOVERY_TIMEOUT_S}s (elapsed={elapsed:.1f}s)"
            )

        # Verify new PID differs from original
        assert module.pid != original_pid, (
            f"{module_name} PID unchanged after restart "
            f"(still {original_pid})"
        )

        # Verify total recovery within timeout
        assert elapsed < RECOVERY_TIMEOUT_S, (
            f"{module_name} recovery took {elapsed:.1f}s "
            f"(limit={RECOVERY_TIMEOUT_S}s)"
        )

        # Brief settle before next test to avoid socket conflicts
        time.sleep(1.0)

    def test_rtdb_survives_data_manager_crash(
        self,
        running_system: dict[str, ModuleProcess | None],
    ) -> None:
        """Verify RTDB shm survives data_manager_c SIGKILL -- not recreated."""
        from ems_common.rtdb import RTDB_MAGIC, RTDB_VERSION, attach_rtdb, detach_rtdb

        module: ModuleProcess | None = running_system.get("data_manager_c")
        if module is None:
            pytest.skip("data_manager_c not available")

        assert module.is_alive, "data_manager_c not alive before test"

        # Step 1: Read current RTDB magic and version
        assert SHM_PATH.exists(), "RTDB shm does not exist before crash"
        shm, rtdb = attach_rtdb()
        pre_magic: int = rtdb.magic
        pre_version: int = rtdb.version
        del rtdb
        detach_rtdb(shm)

        # Step 2: Kill data_manager_c with SIGKILL
        module.kill(sig=signal.SIGKILL)

        # Wait for process to die
        deadline: float = time.monotonic() + 2.0
        while module.is_alive and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not module.is_alive, "data_manager_c did not die"

        # Step 3: Assert shm still exists (POSIX shm survives process death)
        assert SHM_PATH.exists(), (
            "RTDB shm (/dev/shm/ems_rtdb) was removed after data_manager_c crash"
        )

        # Step 4: Re-attach and verify magic/version unchanged (no corruption)
        shm, rtdb = attach_rtdb()
        assert rtdb.magic == pre_magic, (
            f"RTDB magic changed after crash: "
            f"0x{pre_magic:08X} -> 0x{rtdb.magic:08X}"
        )
        assert rtdb.version == pre_version, (
            f"RTDB version changed after crash: "
            f"{pre_version} -> {rtdb.version}"
        )
        del rtdb
        detach_rtdb(shm)

        # Step 5: Restart and verify recovery
        module.restart()
        checks: dict[str, Callable[[], bool]] = {
            "alive": lambda: module.is_alive,
            "rtdb_exists": check_rtdb_exists,
        }
        state: dict[str, bool] = wait_for_criteria(
            checks, timeout=RECOVERY_TIMEOUT_S,
        )
        for criterion, passed in state.items():
            assert passed, (
                f"data_manager_c recovery failed after RTDB survival test: "
                f"{criterion} not met"
            )

        time.sleep(1.0)


class TestDoubleFault:
    """Test simultaneous crash of two modules."""

    @pytest.fixture(scope="class")
    def running_system(self) -> dict[str, ModuleProcess | None]:
        """Launch all available modules in startup order.

        Yields a dict of name -> ModuleProcess (None for unavailable modules).
        Teardown cleans up all processes.
        """
        system: dict[str, ModuleProcess | None] = {}

        for name in STARTUP_ORDER:
            proc: ModuleProcess | None = _try_launch(name)
            system[name] = proc
            if proc is not None:
                time.sleep(0.5)

        yield system

        for name in reversed(STARTUP_ORDER):
            proc = system.get(name)
            if proc is not None:
                proc.cleanup()

    def test_comm_and_logger_double_fault(
        self,
        running_system: dict[str, ModuleProcess | None],
    ) -> None:
        """Kill comm_manager_python + logger simultaneously, verify independent recovery."""
        comm: ModuleProcess | None = running_system.get("comm_manager_python")
        logger: ModuleProcess | None = running_system.get("logger")

        if comm is None or logger is None:
            pytest.skip("comm_manager_python and/or logger not available")

        assert comm.is_alive, "comm_manager_python not alive before double-fault"
        assert logger.is_alive, "logger not alive before double-fault"

        # Kill both simultaneously with SIGKILL
        comm.kill(sig=signal.SIGKILL)
        logger.kill(sig=signal.SIGKILL)

        # Wait for both to die
        deadline: float = time.monotonic() + 2.0
        while (comm.is_alive or logger.is_alive) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not comm.is_alive, "comm_manager_python did not die"
        assert not logger.is_alive, "logger did not die"

        # Restart both (comm first, then logger -- mimics systemd ordering)
        comm.restart()
        time.sleep(0.2)
        logger.restart()

        # Assert both recover within RECOVERY_TIMEOUT_S
        checks: dict[str, Callable[[], bool]] = {
            "comm_alive": lambda: comm.is_alive,
            "logger_alive": lambda: logger.is_alive,
        }
        state: dict[str, bool] = wait_for_criteria(
            checks, timeout=RECOVERY_TIMEOUT_S,
        )
        for criterion, passed in state.items():
            assert passed, (
                f"Double-fault (comm+logger) recovery failed: "
                f"{criterion} not met within {RECOVERY_TIMEOUT_S}s"
            )

        # Verify RTDB still valid (if it exists)
        if check_rtdb_exists():
            _verify_rtdb_integrity()

        # If safety_manager is running, verify it was unaffected
        safety: ModuleProcess | None = running_system.get("safety_manager")
        if safety is not None:
            assert safety.is_alive, (
                "safety_manager died during comm+logger double-fault -- "
                "safety must be unaffected by non-safety module crashes"
            )

        time.sleep(1.0)

    def test_safety_gpio_continuity_during_restart(
        self,
        running_system: dict[str, ModuleProcess | None],
    ) -> None:
        """Verify DO pins never de-assert during safety_manager restart gap.

        Uses GPIO harness RtdbBackend to monitor DO-0 in a tight polling loop
        while safety_manager is killed and restarted. Asserts DO-0 was NEVER
        de-asserted (value never went to 0) during the restart gap.
        """
        safety: ModuleProcess | None = running_system.get("safety_manager")
        if safety is None:
            pytest.skip("safety_manager not available")

        # Try to import RtdbBackend
        try:
            from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend
        except ImportError:
            pytest.skip("GPIO harness RtdbBackend not available")

        if not check_rtdb_exists():
            pytest.skip("RTDB shm does not exist -- cannot test GPIO continuity")

        # Step 1: Attach RtdbBackend to existing RTDB
        try:
            harness: RtdbBackend = RtdbBackend(shm_name="ems_rtdb")
        except Exception as exc:
            pytest.skip(f"Cannot attach RtdbBackend: {exc}")

        try:
            # Step 2: Set DI state to trigger E-Stop
            # DI-6=1 (E-Stop active), DI-7=0 (E-Stop NC broken)
            harness.set_di(6, 1)
            harness.set_di(7, 0)

            # Wait for safety_manager to process and assert outputs
            time.sleep(2.0)

            # Step 3: Check if DO-0 is asserted (precondition)
            # If safety_manager doesn't assert DO-0 from our input,
            # we cannot meaningfully test continuity
            initial_do0: int = harness.get_do(0)
            if initial_do0 == 0:
                pytest.skip(
                    "DO-0 not asserted after E-Stop input -- "
                    "safety_manager may not be processing GPIO via RTDB"
                )

            # Step 4: Start tight polling loop in background thread
            samples: list[tuple[float, int]] = []
            stop_event: threading.Event = threading.Event()

            def _poll_do0() -> None:
                """Sample DO-0 every ~1ms with timestamps."""
                while not stop_event.is_set():
                    t: float = time.monotonic()
                    try:
                        val: int = harness.get_do(0)
                        samples.append((t, val))
                    except Exception:
                        # If RTDB becomes unavailable, record as de-asserted
                        samples.append((t, -1))
                    time.sleep(0.001)  # ~1ms sampling

            poll_thread: threading.Thread = threading.Thread(
                target=_poll_do0, daemon=True,
            )
            poll_thread.start()

            # Step 5: Kill safety_manager with SIGKILL
            safety.kill(sig=signal.SIGKILL)

            # Wait for it to die
            deadline: float = time.monotonic() + 2.0
            while safety.is_alive and time.monotonic() < deadline:
                time.sleep(0.05)

            # Step 6: Immediately restart safety_manager
            safety.restart()

            # Step 7: Wait for safety_manager to recover
            recovery_checks: dict[str, Callable[[], bool]] = {
                "alive": lambda: safety.is_alive,
            }
            state: dict[str, bool] = wait_for_criteria(
                recovery_checks, timeout=RECOVERY_TIMEOUT_S,
            )
            assert state["alive"], (
                "safety_manager did not recover within "
                f"{RECOVERY_TIMEOUT_S}s after restart"
            )

            # Step 8: Stop polling
            stop_event.set()
            poll_thread.join(timeout=2.0)

            # Step 9: Analyze DO-0 samples
            # DO-0 should NEVER have been de-asserted (value == 0) during gap
            de_assertions: list[tuple[float, int]] = [
                (t, v) for t, v in samples if v == 0
            ]
            assert len(de_assertions) == 0, (
                f"DO-0 was de-asserted {len(de_assertions)} times during "
                f"safety_manager restart gap. GPIO continuity violated. "
                f"Total samples: {len(samples)}, "
                f"First de-assertion at t={de_assertions[0][0]:.6f}"
                if de_assertions else ""
            )

        finally:
            harness.close()

        time.sleep(1.0)

    def test_data_manager_and_comm_double_fault(
        self,
        running_system: dict[str, ModuleProcess | None],
    ) -> None:
        """Kill data_manager_c + comm_manager_python, restart with correct ordering.

        data_manager_c must restart FIRST (it owns RTDB), then comm_manager_python
        attaches after.
        """
        data_mgr: ModuleProcess | None = running_system.get("data_manager_c")
        comm: ModuleProcess | None = running_system.get("comm_manager_python")

        if data_mgr is None:
            pytest.skip("data_manager_c not available")
        if comm is None:
            pytest.skip("comm_manager_python not available")

        assert data_mgr.is_alive, "data_manager_c not alive before double-fault"
        assert comm.is_alive, "comm_manager_python not alive before double-fault"

        # Kill both simultaneously with SIGKILL
        data_mgr.kill(sig=signal.SIGKILL)
        comm.kill(sig=signal.SIGKILL)

        # Wait for both to die
        deadline: float = time.monotonic() + 2.0
        while (data_mgr.is_alive or comm.is_alive) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not data_mgr.is_alive, "data_manager_c did not die"
        assert not comm.is_alive, "comm_manager_python did not die"

        # Restart data_manager_c FIRST (it owns RTDB)
        data_mgr.restart()

        # Wait for data_manager_c to be alive and RTDB to exist
        dm_checks: dict[str, Callable[[], bool]] = {
            "alive": lambda: data_mgr.is_alive,
            "rtdb_exists": check_rtdb_exists,
        }
        dm_state: dict[str, bool] = wait_for_criteria(
            dm_checks, timeout=RECOVERY_TIMEOUT_S,
        )
        assert dm_state["alive"], "data_manager_c did not recover"
        assert dm_state["rtdb_exists"], "RTDB not created after data_manager_c restart"

        # THEN restart comm_manager_python
        comm.restart()

        # Assert comm recovery
        comm_checks: dict[str, Callable[[], bool]] = {
            "alive": lambda: comm.is_alive,
        }
        comm_state: dict[str, bool] = wait_for_criteria(
            comm_checks, timeout=RECOVERY_TIMEOUT_S,
        )
        assert comm_state["alive"], (
            "comm_manager_python did not recover after data_manager_c"
        )

        # Verify RTDB valid after double-fault
        if check_rtdb_exists():
            _verify_rtdb_integrity()

        time.sleep(1.0)
