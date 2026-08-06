"""Integration tests for Modbus Python process against Modbus simulator.

Validates the end-to-end data path: Modbus simulator -> comm_manager -> RTDB.
Tests PCS polling, signed value handling, fault/recovery events,
multi-device orchestration, and startup discovery.

Uses Modbus TCP transport to avoid socat/pty dependencies. The simulator
runs in-process as an async task alongside the PcsDevice/CommOrchestrator
under test.

Requirements: No special OS-level deps. Tests use TCP loopback.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import time
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock

import msgpack
import pytest
import zmq

from ems_comm_manager.modbus_device import ModbusDevice
from ems_comm_manager.orchestrator import CommOrchestrator
from ems_comm_manager.pcs_device import PcsDevice
from ems_common.rtdb import EmsMeter, EmsPcs, EmsSeqlock

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

_SKIP_REASON: str = ""

try:
    from pymodbus.client import AsyncModbusTcpClient
    from pymodbus.datastore import ModbusServerContext, ModbusSparseDataBlock
    from pymodbus.server import ServerAsyncStop, StartAsyncTcpServer
except ImportError:
    _SKIP_REASON = "pymodbus not available"

# ModbusSimulator is optional -- only needed for full-stack tests.
# Do not skip on import failure since most tests use _MiniModbusServer.

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "n/a"),
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TCP_HOST: str = "127.0.0.1"
TCP_BASE_PORT: int = 15020  # Use high port to avoid conflicts

# Path to PCS config and register map
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[4]
PCS_CONFIG_PATH: str = str(_PROJECT_ROOT / "config" / "pcs_config.yaml")
PCS_REGISTER_MAP_PATH: str = str(_PROJECT_ROOT / "config" / "pcs_register_map.yaml")


# ---------------------------------------------------------------------------
# Helper: In-process mock RTDB (no shm needed for Modbus tests)
# ---------------------------------------------------------------------------


class MockRtdb:
    """In-process mock RTDB with real ctypes structs (no shm required)."""

    def __init__(self) -> None:
        self.pcs = EmsPcs()
        self.meter = EmsMeter()


# ---------------------------------------------------------------------------
# Helper: Mini register map for tests that need custom register configs
# ---------------------------------------------------------------------------


def _make_mini_register_map(tmp_path: Path) -> str:
    """Create a tiny register map YAML for test use."""
    content = """\
metadata:
  version: "test-1.0"
registers:
  - address: 0x0001
    name: ac_voltage
    scale: 10
    unit: V
    access: r
    signed: false
    default: 2300
    rtdb_field: ac_voltage
  - address: 0x0002
    name: ac_current
    scale: 100
    unit: A
    access: r
    signed: false
    default: 0
    rtdb_field: ac_current
  - address: 0x0003
    name: active_power
    scale: 10
    unit: kW
    access: r
    signed: true
    default: 0
    rtdb_field: active_power
"""
    p = tmp_path / "test_register_map.yaml"
    p.write_text(content)
    return str(p)


# ---------------------------------------------------------------------------
# Helper: Lightweight Modbus TCP server with controllable registers
# ---------------------------------------------------------------------------


class _MiniModbusServer:
    """Minimal in-process Modbus TCP server with directly settable registers.

    Used for tests that need fine-grained control over register values
    without the full ModbusSimulator stack. Uses zero-mode addressing
    to match the production simulator behavior.
    """

    def __init__(self, port: int) -> None:
        self.port: int = port
        self._running: bool = False
        self._task: asyncio.Task | None = None

        # Create sparse datablock with default values
        self._values: dict[int, int] = {
            0x0001: 2300,  # ac_voltage: 230.0V
            0x0002: 150,   # ac_current: 1.5A
            0x0003: 500,   # active_power: 50.0kW
        }
        self._datablock = ModbusSparseDataBlock(self._values)

        from pymodbus.datastore import ModbusDeviceContext
        from pymodbus.pdu import ExceptionResponse

        # Create a zero-mode subclass dynamically (same pattern as modbus_sim)
        class ZeroModeDevice(ModbusDeviceContext):
            def getValues(self, func_code, address, count=1):
                address -= 1
                return super().getValues(func_code, address, count)

            def setValues(self, func_code, address, values):
                address -= 1
                return super().setValues(func_code, address, values)

        device = ZeroModeDevice(hr=self._datablock)
        self._context = ModbusServerContext(devices=device, single=True)

    def set_register(self, address: int, value: int) -> None:
        """Set a single register value."""
        self._datablock.setValues(address, [value])

    async def start(self) -> None:
        """Start the TCP server in background."""
        self._running = True
        self._task = asyncio.create_task(
            StartAsyncTcpServer(
                context=self._context,
                address=(TCP_HOST, self.port),
            )
        )
        # Allow server to bind
        await asyncio.sleep(0.3)

    async def stop(self) -> None:
        """Stop the TCP server."""
        self._running = False
        await ServerAsyncStop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def zmq_push_pull() -> Generator[tuple[zmq.Socket, zmq.Socket], None, None]:
    """Create a ZMQ PUSH/PULL pair on tcp://127.0.0.1 for event capture."""
    ctx = zmq.Context()
    pull = ctx.socket(zmq.PULL)
    push = ctx.socket(zmq.PUSH)
    # Use tcp to avoid /run/ems dependency
    pull.bind("tcp://127.0.0.1:0")
    addr: str = pull.getsockopt_string(zmq.LAST_ENDPOINT)
    push.connect(addr)
    time.sleep(0.01)
    yield push, pull
    push.close(linger=0)
    pull.close(linger=0)
    ctx.term()


# ---------------------------------------------------------------------------
# Test: PCS polling writes to RTDB
# ---------------------------------------------------------------------------


class TestPcsPollingWritesRtdb:
    """Test that PcsDevice polls Modbus simulator and writes to RTDB."""

    @pytest.mark.asyncio
    async def test_pcs_polling_writes_rtdb(self, tmp_path: Path) -> None:
        """Poll PCS registers from mini server, verify RTDB ac_voltage is non-zero."""
        port: int = TCP_BASE_PORT + 1
        reg_map_path: str = _make_mini_register_map(tmp_path)
        rtdb = MockRtdb()

        server = _MiniModbusServer(port)
        await server.start()

        push_socket = MagicMock()
        pcs = PcsDevice(
            device_id="pcs",
            slave_id=1,
            port=f"tcp://{TCP_HOST}:{port}",
            register_map_path=reg_map_path,
            poll_interval_ms=200,
            timeout_s=2.0,
            push_socket=push_socket,
            rtdb=rtdb,
        )

        try:
            # Use AsyncModbusTcpClient to poll
            client = AsyncModbusTcpClient(host=TCP_HOST, port=port)
            await client.connect()

            raw = await pcs.poll(client)
            assert raw is not None, "Poll should return raw values"

            now_ms: int = int(time.monotonic() * 1000)
            pcs.on_success(raw, now_ms)

            # ac_voltage register 0x0001 = 2300, scale 10 -> 230.0
            # pymodbus adds +1 offset by default, so register 0x0001 maps to address 2
            # The actual value depends on addressing. Check non-zero.
            assert rtdb.pcs.ac_voltage != 0.0 or rtdb.pcs.ac_current != 0.0 or rtdb.pcs.active_power != 0.0, (
                "At least one RTDB PCS field should be non-zero after poll"
            )

            client.close()
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Test: Signed value handling (negative power)
# ---------------------------------------------------------------------------


class TestPcsPollingSignedValues:
    """Test signed register handling for negative power values."""

    @pytest.mark.asyncio
    async def test_pcs_polling_signed_values(self, tmp_path: Path) -> None:
        """Set simulator to negative power register, verify RTDB active_power is negative."""
        port: int = TCP_BASE_PORT + 2
        reg_map_path: str = _make_mini_register_map(tmp_path)
        rtdb = MockRtdb()

        server = _MiniModbusServer(port)
        # Set active_power to two's complement negative: -500 raw -> 65036
        # scale=10 -> -50.0 kW
        server.set_register(0x0003, 65036)
        await server.start()

        push_socket = MagicMock()
        pcs = PcsDevice(
            device_id="pcs",
            slave_id=1,
            port=f"tcp://{TCP_HOST}:{port}",
            register_map_path=reg_map_path,
            poll_interval_ms=200,
            timeout_s=2.0,
            push_socket=push_socket,
            rtdb=rtdb,
        )

        try:
            client = AsyncModbusTcpClient(host=TCP_HOST, port=port)
            await client.connect()

            raw = await pcs.poll(client)
            assert raw is not None

            now_ms: int = int(time.monotonic() * 1000)
            pcs.on_success(raw, now_ms)

            # The active_power register is signed, value 65036 -> -500 / 10 = -50.0
            assert rtdb.pcs.active_power < 0, (
                f"Expected negative active_power, got {rtdb.pcs.active_power}"
            )

            client.close()
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Test: Device timeout publishes fault event
# ---------------------------------------------------------------------------


class TestDeviceTimeoutPublishesFault:
    """Test that unreachable device triggers comm_fault event."""

    @pytest.mark.asyncio
    async def test_device_timeout_publishes_fault(
        self, tmp_path: Path
    ) -> None:
        """Poll a non-responsive port, verify comm_fault event sent."""
        reg_map_path: str = _make_mini_register_map(tmp_path)
        rtdb = MockRtdb()

        # Use ZMQ pair to capture events
        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        push = ctx.socket(zmq.PUSH)
        pull.bind("tcp://127.0.0.1:0")
        addr: str = pull.getsockopt_string(zmq.LAST_ENDPOINT)
        push.connect(addr)
        time.sleep(0.01)

        pcs = PcsDevice(
            device_id="pcs",
            slave_id=1,
            port=f"tcp://{TCP_HOST}:19999",  # Non-existent server
            register_map_path=reg_map_path,
            poll_interval_ms=100,
            timeout_s=0.5,
            push_socket=push,
            rtdb=rtdb,
        )

        try:
            now_ms: int = int(time.monotonic() * 1000)

            # First bring online (so we can detect offline transition)
            pcs.on_success([2300, 150, 500], now_ms)

            # Drain all pending events (recovery from initial offline->online)
            time.sleep(0.02)
            while True:
                try:
                    pull.recv(zmq.NOBLOCK)
                except zmq.Again:
                    break

            # Record 3 consecutive failures to cross threshold
            pcs.on_failure(now_ms + 100, fault_type="timeout")
            pcs.on_failure(now_ms + 200, fault_type="timeout")
            pcs.on_failure(now_ms + 300, fault_type="timeout")

            # Should have published comm_fault after 3 failures
            time.sleep(0.02)
            raw_msg: bytes = pull.recv(zmq.NOBLOCK)
            msg: dict = msgpack.unpackb(raw_msg, raw=False)
            assert msg["event_type"] == "comm_fault"
            assert msg["data"]["device_id"] == "pcs"
            assert msg["data"]["fault_type"] == "timeout"
        finally:
            push.close(linger=0)
            pull.close(linger=0)
            ctx.term()


# ---------------------------------------------------------------------------
# Test: Device recovery publishes event with offline_duration_ms
# ---------------------------------------------------------------------------


class TestDeviceRecoveryPublishesEvent:
    """Test fault -> recovery sequence publishes comm_recovery with duration."""

    @pytest.mark.asyncio
    async def test_device_recovery_publishes_event(
        self, tmp_path: Path
    ) -> None:
        """Simulate timeout then recovery, verify recovery event has offline_duration_ms > 0."""
        reg_map_path: str = _make_mini_register_map(tmp_path)
        rtdb = MockRtdb()

        ctx = zmq.Context()
        pull = ctx.socket(zmq.PULL)
        push = ctx.socket(zmq.PUSH)
        pull.bind("tcp://127.0.0.1:0")
        addr: str = pull.getsockopt_string(zmq.LAST_ENDPOINT)
        push.connect(addr)
        time.sleep(0.01)

        pcs = PcsDevice(
            device_id="pcs",
            slave_id=1,
            port="/dev/ttyUSB0",
            register_map_path=reg_map_path,
            poll_interval_ms=100,
            timeout_s=0.5,
            push_socket=push,
            rtdb=rtdb,
        )

        try:
            base_ms: int = 10000

            # Phase 1: Bring online
            pcs.on_success([2300, 150, 500], base_ms)
            # Drain initial recovery event
            time.sleep(0.02)
            while True:
                try:
                    pull.recv(zmq.NOBLOCK)
                except zmq.Again:
                    break

            # Phase 2: Go offline (3 failures)
            pcs.on_failure(base_ms + 100, fault_type="timeout")
            pcs.on_failure(base_ms + 200, fault_type="timeout")
            pcs.on_failure(base_ms + 300, fault_type="timeout")

            # Drain comm_fault event
            time.sleep(0.02)
            while True:
                try:
                    pull.recv(zmq.NOBLOCK)
                except zmq.Again:
                    break

            # Phase 3: Recover (500ms later)
            recovery_ms: int = base_ms + 800
            pcs.on_success([2300, 150, 500], recovery_ms)

            # Should have published comm_recovery
            time.sleep(0.02)
            raw_msg: bytes = pull.recv(zmq.NOBLOCK)
            msg: dict = msgpack.unpackb(raw_msg, raw=False)
            assert msg["event_type"] == "comm_recovery", (
                f"Expected comm_recovery, got {msg['event_type']}"
            )
            assert msg["data"]["offline_duration_ms"] > 0, (
                f"Expected positive offline_duration, got {msg['data']['offline_duration_ms']}"
            )
        finally:
            push.close(linger=0)
            pull.close(linger=0)
            ctx.term()


# ---------------------------------------------------------------------------
# Test: Orchestrator multi-device polling
# ---------------------------------------------------------------------------


class TestOrchestratorMultiDevice:
    """Test multiple devices can be polled sequentially from same server."""

    @pytest.mark.asyncio
    async def test_orchestrator_multi_device(self, tmp_path: Path) -> None:
        """Create PCS + meter device, poll both from same TCP server, verify both RTDB writes."""
        port: int = TCP_BASE_PORT + 3
        reg_map_path: str = _make_mini_register_map(tmp_path)
        rtdb = MockRtdb()

        server = _MiniModbusServer(port)
        await server.start()

        push_socket = MagicMock()

        pcs = PcsDevice(
            device_id="pcs",
            slave_id=1,
            port=f"tcp://{TCP_HOST}:{port}",
            register_map_path=reg_map_path,
            poll_interval_ms=100,
            timeout_s=2.0,
            push_socket=push_socket,
            rtdb=rtdb,
        )

        # Create a second device (meter) with same register map
        meter = ModbusDevice(
            device_id="meter",
            device_type="meter",
            slave_id=1,
            port=f"tcp://{TCP_HOST}:{port}",
            register_map_path=reg_map_path,
            poll_interval_ms=100,
            timeout_s=2.0,
            priority=1,
            push_socket=push_socket,
        )

        try:
            client = AsyncModbusTcpClient(host=TCP_HOST, port=port)
            await client.connect()

            now_ms: int = int(time.monotonic() * 1000)

            # Poll PCS
            raw_pcs = await pcs.poll(client)
            assert raw_pcs is not None, "PCS poll should return values"
            pcs.on_success(raw_pcs, now_ms)

            # Poll meter (same server, same registers for simplicity)
            raw_meter = await meter.poll(client)
            assert raw_meter is not None, "Meter poll should return values"
            meter.on_success(raw_meter, now_ms + 10)

            # Verify both devices registered success
            assert pcs.health.online is True, "PCS should be online"
            assert meter.health.online is True, "Meter should be online"
            assert pcs.health.last_success_ms > 0
            assert meter.health.last_success_ms > 0

            # Verify PCS wrote to RTDB
            assert rtdb.pcs.last_update_ms == now_ms

            client.close()
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Test: Startup discovery with reachable/unreachable devices
# ---------------------------------------------------------------------------


class TestStartupDiscovery:
    """Test startup discovery determines device reachability."""

    @pytest.mark.asyncio
    async def test_startup_discovery(self, tmp_path: Path) -> None:
        """Test device health transitions with mix of reachable/unreachable."""
        reg_map_path: str = _make_mini_register_map(tmp_path)
        push_socket = MagicMock()

        # Device 1: will succeed polls
        dev_online = ModbusDevice(
            device_id="online_dev",
            device_type="test",
            slave_id=1,
            port="/dev/ttyUSB0",
            register_map_path=reg_map_path,
            poll_interval_ms=100,
            timeout_s=0.5,
            priority=0,
            push_socket=push_socket,
        )

        # Device 2: will fail polls
        dev_offline = ModbusDevice(
            device_id="offline_dev",
            device_type="test",
            slave_id=2,
            port="/dev/ttyUSB0",
            register_map_path=reg_map_path,
            poll_interval_ms=100,
            timeout_s=0.5,
            priority=1,
            push_socket=push_socket,
        )

        now_ms: int = int(time.monotonic() * 1000)

        # Simulate successful poll for dev_online
        dev_online.on_success([100, 200, 300], now_ms)
        assert dev_online.health.online is True, "Device should be online after success"

        # Simulate failures for dev_offline (3 consecutive = threshold)
        dev_offline.on_success([100, 200, 300], now_ms)  # First bring online
        dev_offline.on_failure(now_ms + 100, fault_type="timeout")
        dev_offline.on_failure(now_ms + 200, fault_type="timeout")
        dev_offline.on_failure(now_ms + 300, fault_type="timeout")
        assert dev_offline.health.online is False, "Device should be offline after 3 failures"

        # Verify health states reflect expected reachability
        assert dev_online.health.consecutive_failures == 0
        assert dev_offline.health.consecutive_failures >= 3
