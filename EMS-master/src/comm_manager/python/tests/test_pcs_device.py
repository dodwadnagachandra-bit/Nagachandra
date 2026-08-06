"""Tests for PcsDevice write_setpoint() and process_command() methods.

Covers the M2 command path: control_manager -> RTDB -> comm_manager -> PCS.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ems_comm_manager.pcs_device import (
    PCS_CMD_FAULT_RESET,
    PCS_CMD_OFF,
    PCS_CMD_ON,
    PCS_CMD_NONE,
    PcsDevice,
    _REG_PCS_FAULT_RESET,
    _REG_PCS_ON_OFF,
    _REG_PCS_POWER_SETPOINT,
)
from ems_common.rtdb import EmsSeqlock, EmsSystem


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_mini_register_map(tmp_path: Path) -> str:
    """Create a tiny register map YAML for PcsDevice construction."""
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
"""
    p = tmp_path / "mini_register_map.yaml"
    p.write_text(content)
    return str(p)


class MockSystemSection:
    """Mock RTDB system section with EmsSystem ctypes struct and seqlock."""

    def __init__(self) -> None:
        self._system: EmsSystem = EmsSystem()
        self._system.lock.sequence = 0

    @property
    def lock(self) -> EmsSeqlock:
        """Expose seqlock for seqlock_read_section."""
        return self._system.lock

    @property
    def active_setpoint_kw(self) -> float:
        """Active setpoint from ctypes struct."""
        return self._system.active_setpoint_kw

    @active_setpoint_kw.setter
    def active_setpoint_kw(self, val: float) -> None:
        self._system.active_setpoint_kw = val

    @property
    def pcs_command(self) -> int:
        return self._system.pcs_command

    @pcs_command.setter
    def pcs_command(self, val: int) -> None:
        self._system.pcs_command = val

    @property
    def pcs_command_seq(self) -> int:
        return self._system.pcs_command_seq

    @pcs_command_seq.setter
    def pcs_command_seq(self, val: int) -> None:
        self._system.pcs_command_seq = val


class MockRtdbWithSystem:
    """Mock RTDB that provides a system section with EmsSystem fields."""

    def __init__(self) -> None:
        self.system: EmsSystem = EmsSystem()
        # Lock must start at 0 (even) so seqlock reads succeed immediately
        self.system.lock.sequence = 0


def _make_pcs_device(tmp_path: Path, rtdb: object = None) -> PcsDevice:
    """Create a PcsDevice with a minimal register map for testing."""
    reg_map_path = _make_mini_register_map(tmp_path)
    return PcsDevice(
        device_id="pcs",
        slave_id=1,
        port="/dev/ttyUSB0",
        register_map_path=reg_map_path,
        poll_interval_ms=500,
        timeout_s=3.0,
        push_socket=MagicMock(),
        rtdb=rtdb,
    )


# ---------------------------------------------------------------------------
# write_setpoint() tests
# ---------------------------------------------------------------------------


class TestWriteSetpoint:
    """Test PcsDevice.write_setpoint() power register writes."""

    @pytest.mark.asyncio
    async def test_positive_setpoint_writes_correct_raw(self, tmp_path: Path) -> None:
        """25.0 kW -> raw = 250, written to register 0x500E."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.active_setpoint_kw = 25.0

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.write_setpoint(client)

        client.write_register.assert_called_once_with(
            _REG_PCS_POWER_SETPOINT, 250, device_id=1
        )

    @pytest.mark.asyncio
    async def test_negative_setpoint_two_complement(self, tmp_path: Path) -> None:
        """−10.0 kW -> raw = int(-100+65536) = 65436 (two's complement uint16)."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.active_setpoint_kw = -10.0

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.write_setpoint(client)

        client.write_register.assert_called_once_with(
            _REG_PCS_POWER_SETPOINT, 65436, device_id=1
        )

    @pytest.mark.asyncio
    async def test_zero_setpoint_writes_zero(self, tmp_path: Path) -> None:
        """0.0 kW -> raw = 0."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.active_setpoint_kw = 0.0

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.write_setpoint(client)

        client.write_register.assert_called_once_with(
            _REG_PCS_POWER_SETPOINT, 0, device_id=1
        )

    @pytest.mark.asyncio
    async def test_fractional_setpoint_rounds_correctly(self, tmp_path: Path) -> None:
        """12.35 kW rounds to 12.4 kW -> raw 124."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.active_setpoint_kw = 12.35

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.write_setpoint(client)

        # round(12.35 * 10) = round(123.5) = 124
        client.write_register.assert_called_once_with(
            _REG_PCS_POWER_SETPOINT, 124, device_id=1
        )

    @pytest.mark.asyncio
    async def test_no_rtdb_is_noop(self, tmp_path: Path) -> None:
        """write_setpoint with no RTDB does nothing."""
        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb=None)
        client: AsyncMock = AsyncMock()

        await dev.write_setpoint(client)

        client.write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_modbus_error_logs_warning_no_raise(self, tmp_path: Path) -> None:
        """ModbusIOException during write logs warning but does not raise."""
        from pymodbus.exceptions import ModbusIOException

        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.active_setpoint_kw = 10.0

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()
        client.write_register.side_effect = ModbusIOException("connection lost")

        # Should not raise
        await dev.write_setpoint(client)


# ---------------------------------------------------------------------------
# process_command() tests
# ---------------------------------------------------------------------------


class TestProcessCommand:
    """Test PcsDevice.process_command() command dispatch and deduplication."""

    @pytest.mark.asyncio
    async def test_on_command_writes_on_register(self, tmp_path: Path) -> None:
        """PCS_CMD_ON with incremented seq writes 1 to register 0x0291."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.pcs_command = PCS_CMD_ON
        rtdb.system.pcs_command_seq = 1  # increment from 0

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.process_command(client)

        client.write_register.assert_called_once_with(_REG_PCS_ON_OFF, 1, device_id=1)
        assert dev._last_cmd_seq == 1

    @pytest.mark.asyncio
    async def test_off_command_writes_zero_to_on_off_register(self, tmp_path: Path) -> None:
        """PCS_CMD_OFF with incremented seq writes 0 to register 0x0291."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.pcs_command = PCS_CMD_OFF
        rtdb.system.pcs_command_seq = 1

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.process_command(client)

        client.write_register.assert_called_once_with(_REG_PCS_ON_OFF, 0, device_id=1)

    @pytest.mark.asyncio
    async def test_fault_reset_writes_fault_reset_register(self, tmp_path: Path) -> None:
        """PCS_CMD_FAULT_RESET with incremented seq writes 1 to register 0x5064."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.pcs_command = PCS_CMD_FAULT_RESET
        rtdb.system.pcs_command_seq = 1

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.process_command(client)

        client.write_register.assert_called_once_with(_REG_PCS_FAULT_RESET, 1, device_id=1)

    @pytest.mark.asyncio
    async def test_dedup_same_seq_does_nothing(self, tmp_path: Path) -> None:
        """Same seq on second call does nothing (deduplication)."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.pcs_command = PCS_CMD_ON
        rtdb.system.pcs_command_seq = 1

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        # First call: new seq, should execute
        await dev.process_command(client)
        assert client.write_register.call_count == 1

        # Second call: same seq, should be no-op
        client.write_register.reset_mock()
        await dev.process_command(client)
        client.write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_seq_zero_no_command_on_init(self, tmp_path: Path) -> None:
        """seq=0 with _last_cmd_seq=0 does nothing on initial state (no-op)."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.pcs_command = PCS_CMD_ON
        rtdb.system.pcs_command_seq = 0  # same as initial _last_cmd_seq

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.process_command(client)

        client.write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_seq_rollover_uint32_handled(self, tmp_path: Path) -> None:
        """Seq wrapping around uint32 max is handled correctly."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.pcs_command = PCS_CMD_OFF
        rtdb.system.pcs_command_seq = 0  # wrapped from 0xFFFFFFFF + 1

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        # Simulate last_cmd_seq at max uint32 value (0xFFFFFFFF)
        dev._last_cmd_seq = 0xFFFFFFFF
        client: AsyncMock = AsyncMock()

        # (0 - 0xFFFFFFFF) & 0xFFFFFFFF = 1, which != 0 → new command
        await dev.process_command(client)

        client.write_register.assert_called_once_with(_REG_PCS_ON_OFF, 0, device_id=1)

    @pytest.mark.asyncio
    async def test_no_rtdb_is_noop(self, tmp_path: Path) -> None:
        """process_command with no RTDB does nothing."""
        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb=None)
        client: AsyncMock = AsyncMock()

        await dev.process_command(client)

        client.write_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_command_does_not_write(self, tmp_path: Path) -> None:
        """Unknown command value logs warning but does not write any register."""
        rtdb: MockRtdbWithSystem = MockRtdbWithSystem()
        rtdb.system.pcs_command = 99  # unknown value
        rtdb.system.pcs_command_seq = 1

        dev: PcsDevice = _make_pcs_device(tmp_path, rtdb)
        client: AsyncMock = AsyncMock()

        await dev.process_command(client)

        client.write_register.assert_not_called()
        assert dev._last_cmd_seq == 1  # seq still updated
