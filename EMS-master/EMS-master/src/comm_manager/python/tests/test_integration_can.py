"""Integration tests for CAN C process against CAN simulator on vcan.

Validates the end-to-end data path: CAN simulator -> comm_manager_c -> RTDB.
Tests heartbeat timeout detection, error frame handling, and online recovery.

Requirements: vcan kernel module, root/CAP_NET_ADMIN, comm_manager_c binary built.
Tests skip cleanly when prerequisites are unavailable.
"""

from __future__ import annotations

import ctypes
import os
import signal
import struct
import subprocess
import time
from multiprocessing import shared_memory
from pathlib import Path
from typing import Generator

import pytest

from ems_common.rtdb import (
    RTDB_MAGIC,
    RTDB_SHM_NAME,
    RTDB_VERSION,
    EmsCanHealth,
    EmsRtdb,
)


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

_IS_ROOT: bool = os.geteuid() == 0

_COMM_MANAGER_C_BIN: str = str(
    Path(__file__).resolve().parents[4] / "build" / "src" / "comm_manager" / "c" / "comm_manager_c"
)

_BIN_EXISTS: bool = Path(_COMM_MANAGER_C_BIN).is_file()


def _vcan_available() -> bool:
    """Check if vcan kernel module can be loaded."""
    if not _IS_ROOT:
        return False
    try:
        result = subprocess.run(
            ["modprobe", "vcan"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


_VCAN_OK: bool = _vcan_available() if _IS_ROOT else False

_RUN_EMS_EXISTS: bool = Path("/run/ems").is_dir()

_SKIP_REASON: str = ""
if not _IS_ROOT:
    _SKIP_REASON = "Root/CAP_NET_ADMIN required for vcan"
elif not _VCAN_OK:
    _SKIP_REASON = "vcan kernel module not available"
elif not _BIN_EXISTS:
    _SKIP_REASON = f"comm_manager_c binary not found at {_COMM_MANAGER_C_BIN}"
elif not _RUN_EMS_EXISTS:
    _SKIP_REASON = "/run/ems/ not available (ipc paths hardcoded in C binary)"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "n/a"),
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VCAN_INTERFACE: str = "vcan0"
BASE_CAN_ID: int = 0x98FF0003
HEARTBEAT_TIMEOUT_MS: int = 900
NUM_CLUSTERS: int = 1
NUM_RACKS: int = 2

# CAN error frame constants (linux/can/error.h)
CAN_ERR_FLAG: int = 0x20000000
CAN_ERR_BUSOFF: int = 0x00000040

# CAN bus states matching can_health.h
CAN_BUS_OK: int = 0
CAN_BUS_OFF: int = 3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vcan_interface() -> Generator[str, None, None]:
    """Create vcan0 interface, tear down after test."""
    # Remove stale interface if exists
    subprocess.run(
        ["ip", "link", "delete", VCAN_INTERFACE],
        capture_output=True,
    )
    # Create vcan interface
    subprocess.run(
        ["ip", "link", "add", "dev", VCAN_INTERFACE, "type", "vcan"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ip", "link", "set", "up", VCAN_INTERFACE],
        check=True,
        capture_output=True,
    )
    yield VCAN_INTERFACE
    # Teardown
    subprocess.run(
        ["ip", "link", "delete", VCAN_INTERFACE],
        capture_output=True,
    )


@pytest.fixture()
def rtdb_shm() -> Generator[tuple[shared_memory.SharedMemory, EmsRtdb], None, None]:
    """Create RTDB shared memory with test topology, tear down after test."""
    # Remove stale shm if exists
    try:
        stale = shared_memory.SharedMemory(name=RTDB_SHM_NAME, create=False)
        stale.close()
        stale.unlink()
    except FileNotFoundError:
        pass

    size: int = ctypes.sizeof(EmsRtdb)
    shm = shared_memory.SharedMemory(name=RTDB_SHM_NAME, create=True, size=size)

    # Initialize RTDB header
    rtdb = EmsRtdb.from_buffer(shm.buf)
    rtdb.magic = RTDB_MAGIC
    rtdb.version = RTDB_VERSION
    rtdb.cluster_count = NUM_CLUSTERS
    rtdb.racks_per_cluster = NUM_RACKS
    rtdb.modules_per_rack = 1
    rtdb.cells_per_module = 16
    rtdb.temps_per_module = 8

    yield shm, rtdb

    shm.close()
    shm.unlink()


@pytest.fixture()
def comm_manager_c_process(
    vcan_interface: str,
    rtdb_shm: tuple[shared_memory.SharedMemory, EmsRtdb],
) -> Generator[subprocess.Popen, None, None]:
    """Start comm_manager_c as subprocess, kill on teardown."""
    proc = subprocess.Popen(
        [
            _COMM_MANAGER_C_BIN,
            "--interface", vcan_interface,
            "--base-id", hex(BASE_CAN_ID),
            "--clusters", str(NUM_CLUSTERS),
            "--racks-per-cluster", str(NUM_RACKS),
            "--heartbeat-timeout-ms", str(HEARTBEAT_TIMEOUT_MS),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Allow startup
    time.sleep(0.3)
    yield proc
    # Teardown
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture()
def can_simulator(
    vcan_interface: str,
) -> Generator[subprocess.Popen, None, None]:
    """Start CAN simulator sending on vcan0 for 2 racks."""
    proc = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "tools.simulators.can_sim",
            "--interface", vcan_interface,
            "--racks", str(NUM_RACKS),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Allow simulator to start sending frames
    time.sleep(0.5)
    yield proc
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _send_can_frame(interface: str, can_id: int, data: bytes) -> None:
    """Send a single CAN frame using python-can."""
    import can

    bus = can.interface.Bus(channel=interface, interface="socketcan")
    msg = can.Message(
        arbitration_id=can_id,
        data=data,
        is_extended_id=True,
    )
    bus.send(msg)
    bus.shutdown()


def _send_error_frame(interface: str, error_id: int, data: bytes) -> None:
    """Send a CAN error frame on the given interface using raw socket."""
    import socket as sock

    # Raw CAN socket
    CAN_RAW: int = 1
    CAN_EFF_FLAG: int = 0x80000000

    s = sock.socket(sock.AF_CAN, sock.SOCK_RAW, CAN_RAW)
    s.bind((interface,))

    # CAN frame format: ID (4 bytes LE) + DLC (1 byte) + padding (3 bytes) + data (8 bytes)
    frame_id: int = error_id | CAN_ERR_FLAG
    dlc: int = min(len(data), 8)
    padded_data: bytes = data.ljust(8, b"\x00")
    frame: bytes = struct.pack("=IBB2s8s", frame_id, dlc, 0, b"\x00\x00", padded_data)
    s.send(frame)
    s.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCanDecodePackSummary:
    """Test CAN decode of pack summary message writes to RTDB."""

    def test_can_decode_pack_summary(
        self,
        comm_manager_c_process: subprocess.Popen,
        can_simulator: subprocess.Popen,
        rtdb_shm: tuple[shared_memory.SharedMemory, EmsRtdb],
    ) -> None:
        """Start simulator + comm_manager_c, verify RTDB rack[0].pack_v is non-zero."""
        _, rtdb = rtdb_shm
        # Wait for simulator to send data and comm_manager to decode
        time.sleep(2.0)
        rack_0 = rtdb.clusters[0].racks[0]
        assert rack_0.pack_v != 0.0, (
            f"Expected non-zero pack_v, got {rack_0.pack_v}"
        )


class TestCanDecodeAllRacks:
    """Test that all configured racks receive CAN data."""

    def test_can_decode_all_racks(
        self,
        comm_manager_c_process: subprocess.Popen,
        can_simulator: subprocess.Popen,
        rtdb_shm: tuple[shared_memory.SharedMemory, EmsRtdb],
    ) -> None:
        """Verify both rack[0] and rack[1] have non-zero last_update_ms."""
        _, rtdb = rtdb_shm
        time.sleep(3.0)
        for i in range(NUM_RACKS):
            rack = rtdb.clusters[0].racks[i]
            assert rack.last_update_ms != 0, (
                f"Rack[{i}] last_update_ms is 0 -- no CAN data received"
            )


class TestCanHeartbeatTimeout:
    """Test heartbeat timeout marks racks offline when simulator stops."""

    def test_can_heartbeat_timeout(
        self,
        vcan_interface: str,
        comm_manager_c_process: subprocess.Popen,
        rtdb_shm: tuple[shared_memory.SharedMemory, EmsRtdb],
    ) -> None:
        """Send frames, stop, wait for timeout, verify rack goes offline."""
        _, rtdb = rtdb_shm

        # Send a few pack summary frames for rack 0
        # PackSummary is message offset 0x03 in DBC: base_id + cluster*0x1000 + rack*0x10 + offset
        pack_summary_id: int = BASE_CAN_ID  # cluster=0, rack=0, offset=0x03
        # Fake pack summary data: pack_v=3500 (350.0V), pack_i=100 (10.0A), SOC=800 (80.0%)
        frame_data: bytes = struct.pack(">HHH2s", 3500, 100, 800, b"\x00\x00")

        for _ in range(3):
            _send_can_frame(vcan_interface, pack_summary_id, frame_data)
            time.sleep(0.1)

        time.sleep(0.5)
        # Rack should be online after receiving frames
        rack_0 = rtdb.clusters[0].racks[0]
        assert rack_0.online == 1, "Rack should be online after receiving frames"

        # Now stop sending -- wait for heartbeat timeout (900ms + check interval 300ms)
        time.sleep(2.0)

        rack_0 = rtdb.clusters[0].racks[0]
        assert rack_0.online == 0, (
            f"Expected rack offline after heartbeat timeout, got online={rack_0.online}"
        )


class TestCanErrorFrame:
    """Test CAN error frame updates RTDB can_health bus_state."""

    def test_can_error_frame(
        self,
        vcan_interface: str,
        comm_manager_c_process: subprocess.Popen,
        rtdb_shm: tuple[shared_memory.SharedMemory, EmsRtdb],
    ) -> None:
        """Send bus-off error frame, verify RTDB can_health[0].bus_state == CAN_BUS_OFF."""
        _, rtdb = rtdb_shm
        time.sleep(0.5)

        # Send a CAN_ERR_BUSOFF error frame
        error_data: bytes = struct.pack("8B", 0, 0, 0, 0, 0, 0, 0, 0)
        _send_error_frame(vcan_interface, CAN_ERR_BUSOFF, error_data)
        time.sleep(0.5)

        can_health_0: EmsCanHealth = rtdb.can_health[0]
        assert can_health_0.bus_state == CAN_BUS_OFF, (
            f"Expected bus_state={CAN_BUS_OFF} (BUS_OFF), got {can_health_0.bus_state}"
        )


class TestCanOnlineRecovery:
    """Test online recovery when simulator restarts after going offline."""

    def test_can_online_recovery(
        self,
        vcan_interface: str,
        comm_manager_c_process: subprocess.Popen,
        rtdb_shm: tuple[shared_memory.SharedMemory, EmsRtdb],
    ) -> None:
        """Start sending, verify online, stop (offline), restart, verify online again."""
        _, rtdb = rtdb_shm

        pack_summary_id: int = BASE_CAN_ID
        frame_data: bytes = struct.pack(">HHH2s", 3500, 100, 800, b"\x00\x00")

        # Phase 1: Send frames -> rack should come online
        for _ in range(5):
            _send_can_frame(vcan_interface, pack_summary_id, frame_data)
            time.sleep(0.1)
        time.sleep(0.5)
        rack_0 = rtdb.clusters[0].racks[0]
        assert rack_0.online == 1, "Rack should be online after receiving frames"

        # Phase 2: Stop sending -> wait for heartbeat timeout
        time.sleep(2.0)
        rack_0 = rtdb.clusters[0].racks[0]
        assert rack_0.online == 0, "Rack should be offline after heartbeat timeout"

        # Phase 3: Resume sending -> rack should come back online
        for _ in range(5):
            _send_can_frame(vcan_interface, pack_summary_id, frame_data)
            time.sleep(0.1)
        time.sleep(0.5)
        rack_0 = rtdb.clusters[0].racks[0]
        assert rack_0.online == 1, "Rack should recover to online after frames resume"
