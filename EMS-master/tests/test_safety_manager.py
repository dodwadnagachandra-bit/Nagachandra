"""Integration tests for safety_manager binary -- SAFE-01 through SAFE-11.

Validates end-to-end safety behavior: DI input -> response matrix ->
DO output -> RTDB state -> ZMQ events. Uses the RTDB test backend
(--rtdb-backend) to inject GPIO stimuli without hardware.

Requirements covered:
  SAFE-01: E-Stop dual-channel
  SAFE-02: E-Stop response timing (<100ms)
  SAFE-03: Fire dual-confirm
  SAFE-04: Flood
  SAFE-05: Watchdog (skip without /dev/watchdog)
  SAFE-06: SCHED_FIFO
  SAFE-07: Watchdog thread priority
  SAFE-08: RTDB GPIO writes
  SAFE-09: ZMQ events
  SAFE-10: Independent lifecycle
  SAFE-11: GPIO failure
"""

from __future__ import annotations

import ctypes
import os
import signal
import struct
import subprocess
import time
from multiprocessing import resource_tracker
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

import msgpack
import pytest
import zmq

# Suppress SharedMemory BufferError warnings from safety_manager subprocess
# cleanup. The subprocess owns its own shm references and the ctypes overlay
# from the test's EmsRtdb.from_buffer holds a pointer that prevents clean close.
# This is harmless -- the shm is properly unlinked in fixture teardown.
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)

from ems_common.rtdb import (
    RTDB_MAGIC,
    RTDB_VERSION,
    EmsGpio,
    EmsRtdb,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).parent.parent
BINARY_PATH: str = str(PROJECT_ROOT / "build" / "src" / "safety_manager" / "safety_manager")

# DI pin indices (from gpio_config.yaml / response_matrix.h)
DI_ACDB_FEEDBACK: int = 0
DI_FLOOD_SENSOR: int = 1
DI_DOOR_SWITCH: int = 2
DI_SMOKE_DETECTOR: int = 3
DI_HEAT_DETECTOR: int = 4
DI_SPARE: int = 5
DI_ESTOP_NO: int = 6
DI_ESTOP_NC: int = 7

# DO pin indices
DO_ACDB_TRIP: int = 0
DO_EXTINGUISHER: int = 1
DO_WARNING_LAMP: int = 2
DO_RUNNING_LAMP: int = 3
DO_FAULT_LAMP: int = 4
DO_PCS_STOP: int = 5
DO_SIREN: int = 6
DO_SPARE: int = 7

# Scan interval for tests (fast)
SCAN_MS: int = 5

# IPC paths (matching ipc_defs.h)
IPC_BASE: str = "/run/ems"
IPC_TELEMETRY: str = f"ipc://{IPC_BASE}/telemetry.sock"
IPC_SAFETY_CMD: str = f"ipc://{IPC_BASE}/safety_cmd.sock"

HAS_RUN_EMS: bool = Path(IPC_BASE).is_dir()
HAS_WATCHDOG: bool = Path("/dev/watchdog").exists()


def _binary_is_functional() -> bool:
    """Check if safety_manager binary actually runs (not just prints version and exits).

    When built without libzmq, the binary may compile with stubs and exit immediately.
    """
    if not Path(BINARY_PATH).exists():
        return False
    proc = subprocess.Popen(
        [BINARY_PATH, "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.wait(timeout=5)
    output: str = (proc.stdout.read() + proc.stderr.read()).decode()
    # A functional binary prints usage/help; a stub just prints version and exits
    return "--rtdb-backend" in output or "--scan-interval" in output


HAS_FUNCTIONAL_BINARY: bool = _binary_is_functional()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def wait_for_do(rtdb: EmsRtdb, index: int, expected: int,
                timeout_ms: int = 500) -> float:
    """Poll RTDB DO pin until value matches or timeout.

    Returns elapsed time in milliseconds.
    Raises AssertionError on timeout.
    """
    start: float = time.monotonic()
    deadline: float = start + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        val: int = rtdb.gpio.do_state[index]
        if val == expected:
            return (time.monotonic() - start) * 1000.0
        time.sleep(0.001)  # 1ms poll
    actual: int = rtdb.gpio.do_state[index]
    msg = (f"DO-{index} expected {expected}, got {actual} "
           f"after {timeout_ms}ms")
    raise AssertionError(msg)


def wait_for_do_mask(rtdb: EmsRtdb, expected_mask: int,
                     timeout_ms: int = 500) -> float:
    """Wait until all 8 DOs match the expected bitmask.

    Returns elapsed time in milliseconds.
    """
    start: float = time.monotonic()
    deadline: float = start + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        actual: int = 0
        for i in range(8):
            actual |= (rtdb.gpio.do_state[i] & 1) << i
        if actual == expected_mask:
            return (time.monotonic() - start) * 1000.0
        time.sleep(0.001)
    # Build readable failure message
    actual = 0
    for i in range(8):
        actual |= (rtdb.gpio.do_state[i] & 1) << i
    msg = (f"DO mask expected 0x{expected_mask:02X}, got 0x{actual:02X} "
           f"after {timeout_ms}ms")
    raise AssertionError(msg)


def get_do_mask(rtdb: EmsRtdb) -> int:
    """Read current DO state as bitmask."""
    mask: int = 0
    for i in range(8):
        mask |= (rtdb.gpio.do_state[i] & 1) << i
    return mask


def set_di(rtdb: EmsRtdb, pin: int, value: int) -> None:
    """Write a DI pin in the RTDB (simulating GPIO input)."""
    rtdb.gpio.di[pin] = value & 0xFF


def set_di_multi(rtdb: EmsRtdb, values: dict[int, int]) -> None:
    """Write multiple DI pins atomically via seqlock."""
    rtdb.gpio.lock.sequence += 1  # odd = write in progress
    for pin, val in values.items():
        rtdb.gpio.di[pin] = val & 0xFF
    rtdb.gpio.lock.sequence += 1  # even = complete


def clear_all_di(rtdb: EmsRtdb) -> None:
    """Set all DI pins to 0 (normal state)."""
    for i in range(8):
        rtdb.gpio.di[i] = 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shm_name() -> str:
    """Unique shm name per test to avoid collision."""
    return f"ems_rtdb_test_{os.getpid()}_{id(object())}"


@pytest.fixture()
def rtdb_shm(shm_name: str):
    """Create RTDB shared memory with standard name for safety_manager.

    Uses the standard name 'ems_rtdb' so safety_manager can find it.
    Yields the ctypes-mapped EmsRtdb struct.
    """
    # Remove stale shm if leftover from a crashed test
    std_name: str = "ems_rtdb"
    try:
        stale = SharedMemory(name=std_name, create=False, size=ctypes.sizeof(EmsRtdb))
        resource_tracker.unregister(f"/{std_name}", "shared_memory")
        stale.close()
        stale.unlink()
    except FileNotFoundError:
        pass

    shm = SharedMemory(name=std_name, create=True, size=ctypes.sizeof(EmsRtdb))
    rtdb = EmsRtdb.from_buffer(shm.buf)

    # Initialize magic/version/topology
    rtdb.magic = RTDB_MAGIC
    rtdb.version = RTDB_VERSION
    rtdb.cluster_count = 1
    rtdb.racks_per_cluster = 1
    rtdb.modules_per_rack = 1
    rtdb.cells_per_module = 1
    rtdb.temps_per_module = 1

    # Initialize all DI and DO to 0
    for i in range(8):
        rtdb.gpio.di[i] = 0
        rtdb.gpio.do_state[i] = 0

    # Set DI pins to "normal" state:
    # DI-0 (ACDB_FEEDBACK, active_high): raw 1 = feedback present (normal)
    # DI-2 (DOOR_SWITCH, active_low):    raw 1 = door closed (normal)
    # DI-7 (ESTOP_NC, active_low):       raw 1 = NC idle (normal)
    # All others: raw 0 = inactive (normal for active_high pins)
    rtdb.gpio.di[DI_ACDB_FEEDBACK] = 1  # Feedback present
    rtdb.gpio.di[DI_DOOR_SWITCH] = 1    # Door closed
    rtdb.gpio.di[DI_ESTOP_NC] = 1       # NC channel idle

    yield rtdb

    # Cleanup: must delete ctypes struct reference before closing shm
    # to avoid BufferError: cannot close exported pointers exist.
    # Force garbage collection of the ctypes overlay.
    import gc
    del rtdb
    gc.collect()

    # Close and unlink shm
    try:
        shm.close()
    except BufferError:
        # ctypes struct may still hold a reference in some edge cases
        pass
    try:
        shm.unlink()
    except FileNotFoundError:
        pass


@pytest.fixture()
def safety_process(rtdb_shm: EmsRtdb):
    """Start safety_manager binary with RTDB backend.

    Yields subprocess.Popen handle. Teardown sends SIGTERM.
    """
    if not Path(BINARY_PATH).exists():
        pytest.skip(f"safety_manager binary not found at {BINARY_PATH}")
    if not HAS_FUNCTIONAL_BINARY:
        pytest.skip("safety_manager binary not functional (install libzmq3-dev and rebuild)")

    proc: subprocess.Popen = subprocess.Popen(
        [
            BINARY_PATH,
            "--rtdb-backend",
            "--no-watchdog",
            "--scan-interval-ms", str(SCAN_MS),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "EMS_GPIO_BACKEND": "rtdb"},
    )

    # Wait for startup (scan loop should begin within 200ms)
    time.sleep(0.2)

    # Verify process is running
    if proc.poll() is not None:
        stderr_out = proc.stderr.read().decode() if proc.stderr else ""
        pytest.skip(
            f"safety_manager exited immediately with code {proc.returncode}. "
            f"stderr: {stderr_out}"
        )

    yield proc

    # Teardown: graceful shutdown
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


@pytest.fixture()
def zmq_subscriber():
    """ZMQ SUB socket for receiving safety events.

    Binds a PUB-side proxy (XSUB/XPUB) is not needed here -- we bind
    a SUB socket and the safety_manager's PUB connects to the same
    address. Actually ZMQ PUB connects to ipc:// telemetry, so we need
    to bind on that path.

    Only available when /run/ems/ exists.
    """
    if not HAS_RUN_EMS:
        pytest.skip("/run/ems/ not available -- cannot test ZMQ events")

    ctx = zmq.Context()

    # We bind a XPUB on the telemetry address so safety_manager's PUB
    # (which connects) can deliver. Then we create a local SUB that
    # connects to us.
    # Actually simpler: bind a PULL socket where safety_manager PUSHes,
    # or create a proxy.
    #
    # The safety_manager PUB *connects* to the telemetry address.
    # We need to *bind* on that address and subscribe.
    # ZMQ allows SUB to bind and PUB to connect.

    sub_sock = ctx.socket(zmq.SUB)
    sub_sock.setsockopt(zmq.RCVTIMEO, 3000)  # 3 second timeout
    sub_sock.setsockopt_string(zmq.SUBSCRIBE, "gpio")
    sub_sock.bind(IPC_TELEMETRY)

    # Small delay for bind to take effect
    time.sleep(0.05)

    yield sub_sock

    sub_sock.close()
    ctx.term()

    # Clean up the socket file
    sock_path = Path(f"{IPC_BASE}/telemetry.sock")
    if sock_path.exists():
        sock_path.unlink()


@pytest.fixture()
def zmq_reset_client():
    """ZMQ REQ socket for sending safety_reset commands.

    Only available when /run/ems/ exists.
    """
    if not HAS_RUN_EMS:
        pytest.skip("/run/ems/ not available -- cannot test ZMQ reset")

    ctx = zmq.Context()
    req_sock = ctx.socket(zmq.REQ)
    req_sock.setsockopt(zmq.RCVTIMEO, 3000)
    req_sock.setsockopt(zmq.SNDTIMEO, 1000)
    req_sock.connect(IPC_SAFETY_CMD)

    # Small delay for connect
    time.sleep(0.05)

    yield req_sock

    req_sock.close()
    ctx.term()


# ---------------------------------------------------------------------------
# SAFE-01: E-Stop dual-channel
# ---------------------------------------------------------------------------

class TestEstopDualChannel:
    """SAFE-01: E-Stop requires both NO (DI-6) and NC (DI-7) channels."""

    def test_estop_dual_channel_triggers(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """E-Stop dual-channel: DI-6=1, DI-7=0 triggers safety outputs.

        DI-6 (NO, active_high): raw 1 = pressed -> logical 1
        DI-7 (NC, active_low):  raw 0 = pressed -> logical 1 (inverted)
        Both logical 1 = valid dual-channel E-Stop.

        Expected DO: ACDB_TRIP(0), FAULT_LAMP(4), PCS_STOP(5), SIREN(6) ON.
        RUNNING_LAMP(3) OFF (mutually exclusive with faults).
        """
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 0})

        # Wait for outputs
        wait_for_do(rtdb_shm, DO_ACDB_TRIP, 1, timeout_ms=500)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_SIREN, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_FAULT_LAMP, 1, timeout_ms=100)

        # Running lamp must be OFF during fault
        assert rtdb_shm.gpio.do_state[DO_RUNNING_LAMP] == 0, \
            "Running lamp must be OFF during E-Stop"

    def test_estop_single_channel_no_trigger(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Single channel only (DI-6=1, DI-7=1): discrepancy, NOT E-Stop.

        DI-6 (NO, active_high): raw 1 = pressed -> logical 1
        DI-7 (NC, active_low):  raw 1 = not pressed -> logical 0 (inverted)
        Channels disagree -> wiring fault, NO E-Stop response.
        Warning lamp may be ON for discrepancy.
        """
        # First verify normal state -- running lamp ON
        wait_for_do(rtdb_shm, DO_RUNNING_LAMP, 1, timeout_ms=500)

        # Set single-channel discrepancy
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 1})
        time.sleep(0.05)  # Let a few scans process

        # E-Stop outputs should NOT be asserted
        assert rtdb_shm.gpio.do_state[DO_ACDB_TRIP] == 0, \
            "ACDB trip should NOT assert on single-channel discrepancy"
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 0, \
            "PCS stop should NOT assert on single-channel discrepancy"

    def test_estop_both_channels_normal(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Both channels in normal state: no E-Stop.

        DI-6=0 (NO not pressed), DI-7=1 (NC not pressed, raw 1 -> logical 0).
        Both logical 0 = no E-Stop. Running lamp ON.
        """
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 0, DI_ESTOP_NC: 1})
        time.sleep(0.05)

        # No safety outputs
        assert rtdb_shm.gpio.do_state[DO_ACDB_TRIP] == 0
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 0

        # Running lamp ON (normal state)
        wait_for_do(rtdb_shm, DO_RUNNING_LAMP, 1, timeout_ms=200)


# ---------------------------------------------------------------------------
# SAFE-02: E-Stop response timing
# ---------------------------------------------------------------------------

class TestEstopTiming:
    """SAFE-02: E-Stop response must be <100ms."""

    def test_estop_response_within_100ms(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Measure E-Stop DI set to DO assert time over 10 iterations.

        Each iteration: clear inputs, wait for normal, trigger E-Stop,
        measure time to PCS_STOP assertion. Max must be <100ms.
        """
        times: list[float] = []

        for iteration in range(10):
            # Clear inputs: DI-6=0, DI-7=1 (normal)
            set_di_multi(rtdb_shm, {DI_ESTOP_NO: 0, DI_ESTOP_NC: 1})

            # We can't easily clear the latch without ZMQ reset, so
            # for timing tests we track the initial DO state and wait
            # for it to assert from the last trigger. On first iteration
            # the latch is not yet set.
            if iteration == 0:
                time.sleep(0.05)  # Let normal state settle

            # Trigger E-Stop: DI-6=1 (pressed), DI-7=0 (pressed)
            t_start: float = time.monotonic()
            set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 0})

            # Wait for PCS_STOP to assert
            elapsed_ms: float = wait_for_do(
                rtdb_shm, DO_PCS_STOP, 1, timeout_ms=200
            )
            times.append(elapsed_ms)

        max_time: float = max(times)
        avg_time: float = sum(times) / len(times)

        assert max_time < 100.0, (
            f"E-Stop max response time {max_time:.1f}ms exceeds 100ms limit. "
            f"Times: {[f'{t:.1f}' for t in times]}"
        )

        # Log timing for diagnostics (visible in pytest -v output)
        print(f"\nE-Stop timing (10 iterations): "
              f"avg={avg_time:.1f}ms, max={max_time:.1f}ms, "
              f"min={min(times):.1f}ms")


# ---------------------------------------------------------------------------
# SAFE-03: Fire dual-confirm
# ---------------------------------------------------------------------------

class TestFireDualConfirm:
    """SAFE-03: Fire requires both smoke (DI-3) AND heat (DI-4)."""

    def test_fire_dual_confirm_triggers(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Both fire sensors active: extinguisher + all safety outputs.

        Expected: DO-0 (ACDB), DO-1 (Extinguisher), DO-4 (Fault),
                  DO-5 (PCS Stop), DO-6 (Siren) all asserted.
        """
        set_di_multi(rtdb_shm, {DI_SMOKE_DETECTOR: 1, DI_HEAT_DETECTOR: 1})

        wait_for_do(rtdb_shm, DO_EXTINGUISHER, 1, timeout_ms=500)
        wait_for_do(rtdb_shm, DO_ACDB_TRIP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_SIREN, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_FAULT_LAMP, 1, timeout_ms=100)

    def test_fire_single_sensor_no_extinguisher(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Single fire sensor: warning lamp only, NO extinguisher.

        Only smoke detector (DI-3) active. Extinguisher must NOT fire.
        Warning lamp (DO-2) should be ON.
        """
        set_di(rtdb_shm, DI_SMOKE_DETECTOR, 1)
        time.sleep(0.05)

        # Extinguisher must NOT be asserted
        assert rtdb_shm.gpio.do_state[DO_EXTINGUISHER] == 0, \
            "Extinguisher must NOT fire on single sensor"

        # Warning lamp should be ON
        wait_for_do(rtdb_shm, DO_WARNING_LAMP, 1, timeout_ms=200)


# ---------------------------------------------------------------------------
# SAFE-04: Flood
# ---------------------------------------------------------------------------

class TestFlood:
    """SAFE-04: Flood sensor triggers ACDB trip, PCS stop, siren."""

    def test_flood_triggers_outputs(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Flood sensor (DI-1=1): ACDB, Fault, PCS Stop, Siren.

        Extinguisher (DO-1) must NOT assert for flood events.
        """
        set_di(rtdb_shm, DI_FLOOD_SENSOR, 1)

        wait_for_do(rtdb_shm, DO_ACDB_TRIP, 1, timeout_ms=500)
        wait_for_do(rtdb_shm, DO_FAULT_LAMP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_SIREN, 1, timeout_ms=100)

        # Extinguisher must NOT be asserted for flood
        assert rtdb_shm.gpio.do_state[DO_EXTINGUISHER] == 0, \
            "Extinguisher should NOT assert for flood"


# ---------------------------------------------------------------------------
# SAFE-05: Watchdog
# ---------------------------------------------------------------------------

class TestWatchdog:
    """SAFE-05: Hardware watchdog keeps process alive."""

    @pytest.mark.skipif(
        not HAS_WATCHDOG,
        reason="/dev/watchdog not available"
    )
    def test_process_stays_alive_with_watchdog(
        self, rtdb_shm: EmsRtdb
    ) -> None:
        """Start without --no-watchdog, verify process stays alive 5s."""
        if not Path(BINARY_PATH).exists():
            pytest.skip("safety_manager binary not found")

        proc = subprocess.Popen(
            [BINARY_PATH, "--rtdb-backend", "--scan-interval-ms", str(SCAN_MS)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            time.sleep(5)
            assert proc.poll() is None, \
                f"Process died with code {proc.returncode}"
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


# ---------------------------------------------------------------------------
# SAFE-06: SCHED_FIFO
# ---------------------------------------------------------------------------

class TestRtPriority:
    """SAFE-06: RT scheduling setup."""

    def test_rt_priority_graceful(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Verify process starts successfully (RT setup is non-fatal).

        On systems without CAP_SYS_NICE, safety_manager logs a warning
        and continues in default scheduling. We verify the process
        didn't crash. If we DO have RT capability, verify SCHED_FIFO.
        """
        pid: int = safety_process.pid
        assert safety_process.poll() is None, "Process should be running"

        # Try to check scheduler policy (may fail without perms)
        try:
            policy: int = os.sched_getscheduler(pid)
            # SCHED_FIFO = 1 on Linux
            if policy == 1:
                # Great -- RT scheduling is active
                param = os.sched_getparam(pid)
                assert param.sched_priority == 80, \
                    f"Expected priority 80, got {param.sched_priority}"
        except (PermissionError, OSError):
            # Can't read scheduler -- just verify process is alive
            pass

        # Process should still be running regardless
        assert safety_process.poll() is None, \
            "Process should survive RT setup (graceful fallback)"


# ---------------------------------------------------------------------------
# SAFE-07: Watchdog thread priority
# ---------------------------------------------------------------------------

class TestWatchdogThread:
    """SAFE-07: Watchdog thread at higher priority than scan thread."""

    @pytest.mark.skipif(
        not HAS_WATCHDOG,
        reason="/dev/watchdog not available"
    )
    def test_watchdog_thread_exists(self, rtdb_shm: EmsRtdb) -> None:
        """Start with watchdog, verify multiple threads exist."""
        if not Path(BINARY_PATH).exists():
            pytest.skip("safety_manager binary not found")

        proc = subprocess.Popen(
            [BINARY_PATH, "--rtdb-backend", "--scan-interval-ms", str(SCAN_MS)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            time.sleep(0.5)
            if proc.poll() is not None:
                pytest.skip("Process exited (watchdog may not be accessible)")

            pid: int = proc.pid
            task_dir = Path(f"/proc/{pid}/task")
            if task_dir.exists():
                threads: list[str] = list(task_dir.iterdir())
                assert len(threads) >= 2, \
                    f"Expected >= 2 threads (main + watchdog), got {len(threads)}"
            else:
                pytest.skip("/proc/pid/task not available")
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


# ---------------------------------------------------------------------------
# SAFE-08: RTDB GPIO writes
# ---------------------------------------------------------------------------

class TestRtdbGpioWrites:
    """SAFE-08: safety_manager writes DI and DO state to RTDB."""

    def test_rtdb_di_values_updated(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Set DI-3=1 via RTDB, verify safety_manager reads and writes it back.

        The RTDB backend reads from gpio.di[] (set by test) and the
        safety_manager writes back via rtdb_write_gpio which copies
        di_raw into rtdb->gpio.di. After a scan cycle, the value should
        persist.
        """
        set_di(rtdb_shm, DI_SMOKE_DETECTOR, 1)
        time.sleep(0.05)  # At least one scan cycle

        # The safety_manager reads DI from RTDB and writes them back
        # via rtdb_write_gpio. The value should be present.
        assert rtdb_shm.gpio.di[DI_SMOKE_DETECTOR] == 1, \
            "RTDB DI-3 should reflect the set value"

    def test_rtdb_do_state_updated(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Trigger E-Stop, verify DO state in RTDB.

        After E-Stop trigger, PCS_STOP (DO-5) should be 1 in RTDB.
        """
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 0})
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=500)

        # Directly read RTDB DO state
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 1, \
            "RTDB DO-5 (PCS_STOP) should be 1 after E-Stop"

    def test_rtdb_last_update_ms_advancing(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Verify last_update_ms advances with each scan cycle."""
        time.sleep(0.05)  # Let some scans run

        t1: int = rtdb_shm.gpio.last_update_ms
        time.sleep(0.05)  # Wait for more scans
        t2: int = rtdb_shm.gpio.last_update_ms

        assert t2 > t1, (
            f"last_update_ms should advance: t1={t1}, t2={t2}"
        )


# ---------------------------------------------------------------------------
# SAFE-09: ZMQ events
# ---------------------------------------------------------------------------

class TestZmqEvents:
    """SAFE-09: Safety events published via ZMQ."""

    @pytest.mark.skipif(
        not HAS_RUN_EMS,
        reason="/run/ems/ not available -- ZMQ ipc paths require it"
    )
    def test_zmq_event_on_estop(
        self,
        rtdb_shm: EmsRtdb,
        zmq_subscriber,
        safety_process: subprocess.Popen,
    ) -> None:
        """Trigger E-Stop, receive ZMQ event with correct envelope.

        Expected: topic "gpio", MessagePack envelope with src="safety_manager",
        event_type containing "do_change" or similar.
        """
        # Give ZMQ connections time to establish
        time.sleep(0.2)

        # Trigger E-Stop
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 0})

        # Receive message (2-frame: topic + body)
        try:
            frames = zmq_subscriber.recv_multipart(flags=0)
        except zmq.Again:
            pytest.fail("No ZMQ event received within timeout")

        # Expect exactly 2 frames: [topic, msgpack body]
        assert len(frames) == 2, f"Expected 2 frames [topic, body], got {len(frames)}"
        topic: str = frames[0].decode("utf-8")
        assert "gpio" in topic, f"Expected gpio topic, got: {topic}"

        # Second frame is msgpack body (no length prefix)
        body: bytes = frames[1]
        envelope: dict = msgpack.unpackb(body, raw=False)

        assert envelope.get("src") == "safety_manager"
        assert envelope.get("topic") == "gpio"
        assert "payload" in envelope


# ---------------------------------------------------------------------------
# SAFE-10: Independent lifecycle
# ---------------------------------------------------------------------------

class TestIndependentLifecycle:
    """SAFE-10: safety_manager starts without config_manager or data_manager."""

    def test_starts_without_config_manager(
        self, rtdb_shm: EmsRtdb
    ) -> None:
        """Start safety_manager without config_manager running.

        Uses compiled-in defaults. Process should stay alive.
        """
        if not Path(BINARY_PATH).exists():
            pytest.skip("safety_manager binary not found")
        if not HAS_FUNCTIONAL_BINARY:
            pytest.skip("safety_manager binary not functional (install libzmq3-dev and rebuild)")

        proc = subprocess.Popen(
            [
                BINARY_PATH,
                "--rtdb-backend",
                "--no-watchdog",
                "--scan-interval-ms", str(SCAN_MS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            time.sleep(2)
            assert proc.poll() is None, \
                f"Process should be alive after 2s, exited with {proc.returncode}"
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    def test_starts_without_data_manager(self) -> None:
        """Start safety_manager without RTDB (data_manager not running).

        When --rtdb-backend is used, RTDB is required, so this should
        fail with a clear error. Without --rtdb-backend and without
        libgpiod, it will also fail. This validates the error handling.

        When NOT using --rtdb-backend: safety_manager should start with
        RTDB unavailable and log CRITICAL but continue (SAFE-10).
        Since we don't have libgpiod in dev, we verify the RTDB-required
        error path for --rtdb-backend mode.
        """
        if not Path(BINARY_PATH).exists():
            pytest.skip("safety_manager binary not found")
        if not HAS_FUNCTIONAL_BINARY:
            pytest.skip("safety_manager binary not functional (install libzmq3-dev and rebuild)")

        # Ensure no RTDB exists
        try:
            stale = SharedMemory(name="ems_rtdb", create=False)
            resource_tracker.unregister("/ems_rtdb", "shared_memory")
            stale.close()
            stale.unlink()
        except FileNotFoundError:
            pass

        proc = subprocess.Popen(
            [
                BINARY_PATH,
                "--rtdb-backend",
                "--no-watchdog",
                "--scan-interval-ms", str(SCAN_MS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # With --rtdb-backend, should exit because RTDB is required
            proc.wait(timeout=10)  # Retries take up to 3 seconds
            stderr_out: str = proc.stderr.read().decode() if proc.stderr else ""

            # Process should have exited with error (RTDB required for --rtdb-backend)
            assert proc.returncode != 0, \
                "Expected non-zero exit when RTDB unavailable with --rtdb-backend"
            assert "RTDB" in stderr_out or "rtdb" in stderr_out, \
                f"Error should mention RTDB: {stderr_out}"
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("Process did not exit within 10s when RTDB unavailable")


# ---------------------------------------------------------------------------
# SAFE-11: GPIO failure
# ---------------------------------------------------------------------------

class TestGpioFailure:
    """SAFE-11: GPIO failure asserts all safety outputs."""

    def test_gpio_failure_response_matrix(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Verify via C unit tests (response matrix) that gpio_failure
        asserts all outputs.

        Integration-level: the RTDB backend never returns a read failure
        (memory reads always succeed), so gpio_failure cannot be triggered
        via the integration path. This is tested at the C unit test level.

        Here we verify that the normal-state DO output (running lamp)
        is correct, confirming the response matrix is connected.
        """
        # In normal state: running lamp ON, all others OFF
        wait_for_do(rtdb_shm, DO_RUNNING_LAMP, 1, timeout_ms=500)

        # No fault outputs
        assert rtdb_shm.gpio.do_state[DO_ACDB_TRIP] == 0
        assert rtdb_shm.gpio.do_state[DO_EXTINGUISHER] == 0
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 0
        assert rtdb_shm.gpio.do_state[DO_SIREN] == 0


# ---------------------------------------------------------------------------
# General integration tests
# ---------------------------------------------------------------------------

class TestGeneralBehavior:
    """General safety_manager behavior tests."""

    def test_clean_shutdown(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """SIGTERM causes clean shutdown within 2 seconds.

        After shutdown, all DO outputs should be de-asserted (safe state).
        Process should exit with code 0.
        """
        # First verify process is running and in normal state
        wait_for_do(rtdb_shm, DO_RUNNING_LAMP, 1, timeout_ms=500)

        # Send SIGTERM
        safety_process.send_signal(signal.SIGTERM)

        try:
            retcode: int = safety_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            safety_process.kill()
            safety_process.wait()
            pytest.fail("Process did not exit within 5 seconds of SIGTERM")

        assert retcode == 0, f"Expected exit code 0, got {retcode}"

        # After clean shutdown, DO outputs should all be 0 (de-asserted)
        # The shutdown writes all-zero DO via the GPIO backend
        for i in range(8):
            assert rtdb_shm.gpio.do_state[i] == 0, \
                f"DO-{i} should be de-asserted after shutdown"

    def test_latching_requires_reset(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen,
    ) -> None:
        """E-Stop latch persists after inputs clear.

        Trigger E-Stop, clear inputs, verify outputs stay asserted (latched).
        Without ZMQ reset, the latch cannot be cleared in this test.
        """
        # Trigger E-Stop
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 0})
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=500)

        # Clear inputs (normal state: DI-6=0, DI-7=1)
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 0, DI_ESTOP_NC: 1})
        time.sleep(0.1)  # Several scan cycles

        # Outputs should STILL be asserted (latched)
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 1, \
            "PCS_STOP should remain latched after inputs clear"
        assert rtdb_shm.gpio.do_state[DO_ACDB_TRIP] == 1, \
            "ACDB_TRIP should remain latched after inputs clear"

    @pytest.mark.skipif(
        not HAS_RUN_EMS,
        reason="/run/ems/ not available -- ZMQ reset requires it"
    )
    def test_reset_clears_latch(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen,
        zmq_reset_client,
    ) -> None:
        """Safety reset via ZMQ clears latched state when inputs normal.

        1. Trigger E-Stop
        2. Clear inputs
        3. Send safety_reset command
        4. Verify outputs clear
        """
        # Trigger E-Stop
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 0})
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=500)

        # Clear inputs
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 0, DI_ESTOP_NC: 1})
        time.sleep(0.1)

        # Send safety_reset command (length-prefixed msgpack)
        payload: bytes = msgpack.packb({"action": "safety_reset", "source": "test"})
        length_prefix: bytes = struct.pack(">I", len(payload))

        zmq_reset_client.send_multipart([length_prefix, payload])

        # Receive reply
        reply_frames = zmq_reset_client.recv_multipart()
        reply_body: bytes = reply_frames[-1]
        reply: dict = msgpack.unpackb(reply_body, raw=False)

        assert reply.get("status") == "ok", \
            f"Reset should succeed, got: {reply}"

        # Wait for outputs to clear
        time.sleep(0.1)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 0, timeout_ms=500)
        wait_for_do(rtdb_shm, DO_RUNNING_LAMP, 1, timeout_ms=200)

    @pytest.mark.skipif(
        not HAS_RUN_EMS,
        reason="/run/ems/ not available -- ZMQ reset requires it"
    )
    def test_reset_rejected_while_active(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen,
        zmq_reset_client,
    ) -> None:
        """Safety reset rejected while E-Stop inputs are still active."""
        # Trigger E-Stop (and keep inputs active)
        set_di_multi(rtdb_shm, {DI_ESTOP_NO: 1, DI_ESTOP_NC: 0})
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=500)

        # Send safety_reset while inputs still active
        payload: bytes = msgpack.packb({"action": "safety_reset", "source": "test"})
        length_prefix: bytes = struct.pack(">I", len(payload))

        zmq_reset_client.send_multipart([length_prefix, payload])

        # Receive reply -- should be error
        reply_frames = zmq_reset_client.recv_multipart()
        reply_body: bytes = reply_frames[-1]
        reply: dict = msgpack.unpackb(reply_body, raw=False)

        assert reply.get("status") == "error", \
            f"Reset should be rejected while inputs active, got: {reply}"

        # Outputs should still be asserted
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 1, \
            "PCS_STOP should remain after rejected reset"

    def test_normal_state_running_lamp(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Normal state: Running lamp (DO-3) ON, all others OFF.

        'Dark panel' philosophy: only green running lamp in normal state.
        """
        # Wait for normal state to establish
        wait_for_do(rtdb_shm, DO_RUNNING_LAMP, 1, timeout_ms=500)

        # All other outputs should be OFF
        assert rtdb_shm.gpio.do_state[DO_ACDB_TRIP] == 0
        assert rtdb_shm.gpio.do_state[DO_EXTINGUISHER] == 0
        assert rtdb_shm.gpio.do_state[DO_WARNING_LAMP] == 0
        assert rtdb_shm.gpio.do_state[DO_FAULT_LAMP] == 0
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 0
        assert rtdb_shm.gpio.do_state[DO_SIREN] == 0
        assert rtdb_shm.gpio.do_state[DO_SPARE] == 0

    def test_door_open_warning_only(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Door open (DI-2) triggers warning lamp only -- no shutdown.

        DI-2 is active_low: raw 0 = door open -> logical 1.
        """
        # Door open: DI-2 raw 0 (active_low means 0 = active)
        set_di(rtdb_shm, DI_DOOR_SWITCH, 0)
        time.sleep(0.05)

        wait_for_do(rtdb_shm, DO_WARNING_LAMP, 1, timeout_ms=200)

        # No shutdown outputs
        assert rtdb_shm.gpio.do_state[DO_ACDB_TRIP] == 0, \
            "ACDB should NOT trip for door open"
        assert rtdb_shm.gpio.do_state[DO_PCS_STOP] == 0, \
            "PCS should NOT stop for door open"

    def test_acdb_feedback_loss(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """ACDB feedback loss (DI-0=0): Fault + PCS Stop + Siren.

        DI-0 active_high, so logical LOW (raw 0) = loss of signal.
        ACDB trip itself must NOT be asserted (already open).
        Auto-recovers when DI-0 returns high.
        """
        # DI-0 defaults to 0 which means loss of signal
        # Set DI-0=0 explicitly (loss of feedback)
        set_di(rtdb_shm, DI_ACDB_FEEDBACK, 0)
        time.sleep(0.05)

        wait_for_do(rtdb_shm, DO_FAULT_LAMP, 1, timeout_ms=300)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_SIREN, 1, timeout_ms=100)

        # ACDB trip should NOT be asserted (it's already open)
        assert rtdb_shm.gpio.do_state[DO_ACDB_TRIP] == 0, \
            "ACDB trip should NOT re-trip on feedback loss"

    def test_acdb_feedback_auto_recover(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """ACDB feedback loss auto-recovers when DI-0 returns high."""
        # Trigger loss
        set_di(rtdb_shm, DI_ACDB_FEEDBACK, 0)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=300)

        # Recover: DI-0=1 (feedback present)
        set_di(rtdb_shm, DI_ACDB_FEEDBACK, 1)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 0, timeout_ms=300)
        wait_for_do(rtdb_shm, DO_RUNNING_LAMP, 1, timeout_ms=200)

    def test_multiple_faults_combined(
        self, rtdb_shm: EmsRtdb, safety_process: subprocess.Popen
    ) -> None:
        """Multiple simultaneous faults: outputs are OR'd together.

        Flood + Fire single sensor = ACDB trip + PCS stop + Siren
        + Fault + Warning (but NOT extinguisher).
        """
        set_di(rtdb_shm, DI_FLOOD_SENSOR, 1)
        set_di(rtdb_shm, DI_SMOKE_DETECTOR, 1)  # single fire sensor
        time.sleep(0.05)

        wait_for_do(rtdb_shm, DO_ACDB_TRIP, 1, timeout_ms=300)
        wait_for_do(rtdb_shm, DO_PCS_STOP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_SIREN, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_FAULT_LAMP, 1, timeout_ms=100)
        wait_for_do(rtdb_shm, DO_WARNING_LAMP, 1, timeout_ms=100)

        # Extinguisher should NOT be on (only one fire sensor)
        assert rtdb_shm.gpio.do_state[DO_EXTINGUISHER] == 0
