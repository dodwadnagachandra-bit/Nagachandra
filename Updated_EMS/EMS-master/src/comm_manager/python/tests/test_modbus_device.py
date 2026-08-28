"""Tests for ModbusDevice base class and PcsDevice subclass.

Covers: poll success/failure, exception code handling (COMM-07),
on_success/on_failure state transitions, PcsDevice RTDB write.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from ems_comm_manager.modbus_device import ModbusDevice, ModbusDeviceError
from ems_comm_manager.pcs_device import PcsDevice, DEVICE_PCS
from ems_common.rtdb import EmsPcs, EmsSeqlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PCS_REGISTER_MAP: str = str(
    Path(__file__).resolve().parents[4] / "config" / "pcs_register_map.yaml"
)

# Minimal register map YAML for testing (3 contiguous registers)
MINI_REGISTER_MAP = str(
    Path(__file__).resolve().parent / "fixtures" / "mini_register_map.yaml"
)


def _make_mini_register_map(tmp_path: Path) -> str:
    """Create a tiny register map YAML for unit testing."""
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
    p = tmp_path / "mini_register_map.yaml"
    p.write_text(content)
    return str(p)


class MockModbusResponse:
    """Mock a successful pymodbus read_holding_registers response."""

    def __init__(self, registers: list[int]) -> None:
        self.registers: list[int] = registers

    def isError(self) -> bool:
        return False


class MockModbusErrorResponse:
    """Mock an error response with exception code."""

    def __init__(self, exception_code: int) -> None:
        self.exception_code: int = exception_code

    def isError(self) -> bool:
        return True


class MockRtdb:
    """In-process mock RTDB with a real EmsPcs struct (no shm)."""

    def __init__(self) -> None:
        self.pcs = EmsPcs()


# ---------------------------------------------------------------------------
# ModbusDevice Tests
# ---------------------------------------------------------------------------


class TestModbusDevicePoll:
    """Test ModbusDevice.poll() with various responses."""

    @pytest.fixture
    def device(self, tmp_path: Path) -> ModbusDevice:
        reg_map_path = _make_mini_register_map(tmp_path)
        push_socket = MagicMock()
        return ModbusDevice(
            device_id="test_device",
            device_type="test",
            slave_id=1,
            port="/dev/ttyUSB0",
            register_map_path=reg_map_path,
            poll_interval_ms=500,
            timeout_s=3.0,
            priority=0,
            push_socket=push_socket,
        )

    @pytest.mark.asyncio
    async def test_poll_success_returns_raw_values(self, device: ModbusDevice) -> None:
        """Successful read_holding_registers returns assembled raw values."""
        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            return_value=MockModbusResponse([2300, 150, 500])
        )
        raw = await device.poll(client)
        assert raw == [2300, 150, 500]

    @pytest.mark.asyncio
    async def test_poll_exception_code_01_returns_none(self, device: ModbusDevice) -> None:
        """Exception code 01 (Illegal Function) publishes comm_exception, returns None."""
        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            return_value=MockModbusErrorResponse(1)
        )
        raw = await device.poll(client)
        assert raw is None
        device.push_socket.send.assert_called()  # comm_exception published

    @pytest.mark.asyncio
    async def test_poll_exception_code_02_returns_none(self, device: ModbusDevice) -> None:
        """Exception code 02 (Illegal Data Address) publishes comm_exception, returns None."""
        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            return_value=MockModbusErrorResponse(2)
        )
        raw = await device.poll(client)
        assert raw is None

    @pytest.mark.asyncio
    async def test_poll_exception_code_03_returns_none(self, device: ModbusDevice) -> None:
        """Exception code 03 (Illegal Data Value) publishes comm_exception, returns None."""
        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            return_value=MockModbusErrorResponse(3)
        )
        raw = await device.poll(client)
        assert raw is None

    @pytest.mark.asyncio
    async def test_poll_exception_code_04_raises(self, device: ModbusDevice) -> None:
        """Exception code 04 (Device Failure) publishes comm_exception AND raises."""
        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            return_value=MockModbusErrorResponse(4)
        )
        with pytest.raises(ModbusDeviceError):
            await device.poll(client)
        device.push_socket.send.assert_called()


class TestModbusDeviceOnSuccess:
    """Test on_success() state transitions and event publishing."""

    @pytest.fixture
    def device(self, tmp_path: Path) -> ModbusDevice:
        reg_map_path = _make_mini_register_map(tmp_path)
        push_socket = MagicMock()
        return ModbusDevice(
            device_id="test_device",
            device_type="test",
            slave_id=1,
            port="/dev/ttyUSB0",
            register_map_path=reg_map_path,
            poll_interval_ms=500,
            timeout_s=3.0,
            priority=0,
            push_socket=push_socket,
        )

    def test_on_success_first_call_publishes_recovery(self, device: ModbusDevice) -> None:
        """First success transitions offline->online and publishes recovery."""
        device.on_success([2300, 150, 500], now_ms=1000)
        # Device starts offline, first success = recovery
        device.push_socket.send.assert_called()  # recovery event

    def test_on_success_already_online_no_event(self, device: ModbusDevice) -> None:
        """Subsequent successes when online do not publish events."""
        device.on_success([2300, 150, 500], now_ms=1000)
        device.push_socket.reset_mock()
        device.on_success([2300, 150, 500], now_ms=1500)
        device.push_socket.send.assert_not_called()


class TestModbusDeviceOnFailure:
    """Test on_failure() state transitions and event publishing."""

    @pytest.fixture
    def device(self, tmp_path: Path) -> ModbusDevice:
        reg_map_path = _make_mini_register_map(tmp_path)
        push_socket = MagicMock()
        return ModbusDevice(
            device_id="test_device",
            device_type="test",
            slave_id=1,
            port="/dev/ttyUSB0",
            register_map_path=reg_map_path,
            poll_interval_ms=500,
            timeout_s=3.0,
            priority=0,
            push_socket=push_socket,
        )

    def test_on_failure_threshold_publishes_fault(self, device: ModbusDevice) -> None:
        """Reaching offline_threshold consecutive failures publishes comm_fault."""
        # First bring online
        device.on_success([2300, 150, 500], now_ms=1000)
        device.push_socket.reset_mock()

        # 3 consecutive failures (default threshold)
        device.on_failure(now_ms=2000, fault_type="timeout")
        device.on_failure(now_ms=2500, fault_type="timeout")
        device.on_failure(now_ms=3000, fault_type="timeout")

        # Should have published comm_fault on 3rd failure
        device.push_socket.send.assert_called()

    def test_on_failure_below_threshold_no_event(self, device: ModbusDevice) -> None:
        """Failures below threshold do not publish fault events."""
        device.on_success([2300, 150, 500], now_ms=1000)
        device.push_socket.reset_mock()

        device.on_failure(now_ms=2000, fault_type="timeout")
        device.on_failure(now_ms=2500, fault_type="timeout")
        device.push_socket.send.assert_not_called()


# ---------------------------------------------------------------------------
# PcsDevice Tests
# ---------------------------------------------------------------------------


class TestPcsDeviceRtdbWrite:
    """Test PcsDevice._write_to_rtdb maps registers to EmsPcs fields."""

    @pytest.fixture
    def pcs_device(self, tmp_path: Path) -> tuple[PcsDevice, MockRtdb]:
        reg_map_path = _make_mini_register_map(tmp_path)
        push_socket = MagicMock()
        rtdb = MockRtdb()
        dev = PcsDevice(
            device_id="pcs",
            slave_id=1,
            port="/dev/ttyUSB0",
            register_map_path=reg_map_path,
            poll_interval_ms=500,
            timeout_s=3.0,
            push_socket=push_socket,
            rtdb=rtdb,
        )
        return dev, rtdb

    def test_write_to_rtdb_maps_values(self, pcs_device: tuple[PcsDevice, MockRtdb]) -> None:
        """PcsDevice writes scaled register values to correct EmsPcs fields."""
        dev, rtdb = pcs_device
        # raw values: ac_voltage=2300 (scale 10 -> 230.0), ac_current=150 (scale 100 -> 1.5),
        # active_power=500 (scale 10, signed -> 50.0)
        dev._write_to_rtdb([2300, 150, 500], now_ms=5000)

        assert abs(rtdb.pcs.ac_voltage - 230.0) < 0.01
        assert abs(rtdb.pcs.ac_current - 1.5) < 0.01
        assert abs(rtdb.pcs.active_power - 50.0) < 0.01
        assert rtdb.pcs.last_update_ms == 5000

    def test_write_to_rtdb_signed_negative(self, pcs_device: tuple[PcsDevice, MockRtdb]) -> None:
        """Signed register with value > 32767 is treated as negative (two's complement)."""
        dev, rtdb = pcs_device
        # active_power raw = 65036 -> signed: 65036 - 65536 = -500 -> / 10 = -50.0
        dev._write_to_rtdb([2300, 150, 65036], now_ms=6000)

        assert abs(rtdb.pcs.active_power - (-50.0)) < 0.01

    def test_pcs_device_type_and_priority(self, pcs_device: tuple[PcsDevice, MockRtdb]) -> None:
        """PcsDevice has correct type constant and priority."""
        dev, _ = pcs_device
        assert dev.device_type == DEVICE_PCS
        assert dev.priority == 0  # highest priority

    def test_on_success_writes_to_rtdb(self, pcs_device: tuple[PcsDevice, MockRtdb]) -> None:
        """PcsDevice.on_success() calls _write_to_rtdb and updates health."""
        dev, rtdb = pcs_device
        dev.on_success([2300, 150, 500], now_ms=7000)
        assert abs(rtdb.pcs.ac_voltage - 230.0) < 0.01
        assert rtdb.pcs.last_update_ms == 7000
