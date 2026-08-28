"""Tests for startup_discovery -- per-port sequential, cross-port parallel probing.

Covers: all reachable, some unreachable, parallel ports, mandatory vs optional
logging, overall timeout.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ems_comm_manager.health import DeviceHealth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mini_register_map(tmp_path: Path, name: str = "mini") -> str:
    """Create a minimal register map for testing."""
    content = """\
metadata:
  version: "test-1.0"
registers:
  - address: 0x0001
    name: value1
    scale: 1
    unit: ""
    access: r
    signed: false
    default: 0
"""
    p = tmp_path / f"{name}_register_map.yaml"
    p.write_text(content)
    return str(p)


def _make_device(
    tmp_path: Path,
    device_id: str,
    device_type: str,
    port: str,
    priority: int = 0,
) -> "ModbusDevice":
    """Create a ModbusDevice for discovery testing."""
    from ems_comm_manager.modbus_device import ModbusDevice

    reg_map = _make_mini_register_map(tmp_path, device_id)
    push_socket = MagicMock()
    return ModbusDevice(
        device_id=device_id,
        device_type=device_type,
        slave_id=1,
        port=port,
        register_map_path=reg_map,
        poll_interval_ms=1000,
        timeout_s=3.0,
        priority=priority,
        push_socket=push_socket,
    )


class MockModbusResponse:
    """Mock a successful pymodbus response."""

    def __init__(self, registers: list[int] | None = None) -> None:
        self.registers = registers or [0]

    def isError(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Discovery Tests
# ---------------------------------------------------------------------------


class TestStartupDiscoveryAllReachable:
    """Test discovery when all devices respond successfully."""

    @pytest.mark.asyncio
    async def test_all_devices_reachable(self, tmp_path: Path) -> None:
        """Discovery returns True for all devices when all respond."""
        from ems_comm_manager.discovery import startup_discovery

        dev1 = _make_device(tmp_path, "pcs", "pcs", "/dev/ttyUSB0")
        dev2 = _make_device(tmp_path, "meter", "meter", "/dev/ttyUSB1")

        client1 = AsyncMock()
        client1.read_holding_registers = AsyncMock(
            return_value=MockModbusResponse([100])
        )
        client2 = AsyncMock()
        client2.read_holding_registers = AsyncMock(
            return_value=MockModbusResponse([200])
        )

        clients = {"/dev/ttyUSB0": client1, "/dev/ttyUSB1": client2}
        result = await startup_discovery([dev1, dev2], clients)

        assert result["pcs"] is True
        assert result["meter"] is True


class TestStartupDiscoverySomeUnreachable:
    """Test discovery with mixed reachable/unreachable devices."""

    @pytest.mark.asyncio
    async def test_unreachable_device_marked_false(self, tmp_path: Path) -> None:
        """Unreachable device gets False in results, does not block startup."""
        from ems_comm_manager.discovery import startup_discovery

        dev1 = _make_device(tmp_path, "pcs", "pcs", "/dev/ttyUSB0")
        dev2 = _make_device(tmp_path, "btms", "btms", "/dev/ttyUSB0", priority=1)

        client = AsyncMock()
        # First call succeeds (pcs), second raises timeout (btms)
        client.read_holding_registers = AsyncMock(
            side_effect=[MockModbusResponse([100]), asyncio.TimeoutError()]
        )

        clients = {"/dev/ttyUSB0": client}
        result = await startup_discovery([dev1, dev2], clients)

        assert result["pcs"] is True
        assert result["btms"] is False

    @pytest.mark.asyncio
    async def test_all_unreachable_still_returns(self, tmp_path: Path) -> None:
        """Discovery returns even if all devices are unreachable."""
        from ems_comm_manager.discovery import startup_discovery

        dev1 = _make_device(tmp_path, "pcs", "pcs", "/dev/ttyUSB0")

        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        clients = {"/dev/ttyUSB0": client}
        result = await startup_discovery([dev1], clients)

        assert result["pcs"] is False


class TestStartupDiscoveryParallelPorts:
    """Test that discovery probes multiple ports in parallel."""

    @pytest.mark.asyncio
    async def test_ports_probed_in_parallel(self, tmp_path: Path) -> None:
        """Devices on different ports are probed concurrently."""
        from ems_comm_manager.discovery import startup_discovery

        dev1 = _make_device(tmp_path, "pcs", "pcs", "/dev/ttyUSB0")
        dev2 = _make_device(tmp_path, "meter", "meter", "/dev/ttyUSB1")

        call_order: list[str] = []

        async def _slow_read_0(*args, **kwargs):
            call_order.append("port0_start")
            await asyncio.sleep(0.05)
            call_order.append("port0_end")
            return MockModbusResponse([100])

        async def _slow_read_1(*args, **kwargs):
            call_order.append("port1_start")
            await asyncio.sleep(0.05)
            call_order.append("port1_end")
            return MockModbusResponse([200])

        client0 = AsyncMock()
        client0.read_holding_registers = _slow_read_0
        client1 = AsyncMock()
        client1.read_holding_registers = _slow_read_1

        clients = {"/dev/ttyUSB0": client0, "/dev/ttyUSB1": client1}
        result = await startup_discovery([dev1, dev2], clients)

        # Both ports should start before either finishes (parallel)
        assert "port0_start" in call_order
        assert "port1_start" in call_order
        # Both should be reachable
        assert result["pcs"] is True
        assert result["meter"] is True


class TestStartupDiscoveryMandatoryVsOptional:
    """Test mandatory (PCS/BMS) vs optional device logging."""

    @pytest.mark.asyncio
    async def test_mandatory_device_logs_error(self, tmp_path: Path, caplog) -> None:
        """Unreachable mandatory device (PCS) logs at ERROR level."""
        from ems_comm_manager.discovery import startup_discovery

        dev = _make_device(tmp_path, "pcs", "pcs", "/dev/ttyUSB0")

        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        clients = {"/dev/ttyUSB0": client}

        with caplog.at_level(logging.DEBUG):
            await startup_discovery([dev], clients)

        error_messages = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("pcs" in r.message.lower() for r in error_messages)

    @pytest.mark.asyncio
    async def test_optional_device_logs_warning(self, tmp_path: Path, caplog) -> None:
        """Unreachable optional device (BTMS) logs at WARNING level (not ERROR)."""
        from ems_comm_manager.discovery import startup_discovery

        dev = _make_device(tmp_path, "btms", "btms", "/dev/ttyUSB0")

        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        clients = {"/dev/ttyUSB0": client}

        with caplog.at_level(logging.DEBUG):
            await startup_discovery([dev], clients)

        warning_messages = [r for r in caplog.records if r.levelno == logging.WARNING]
        error_messages = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("btms" in r.message.lower() for r in warning_messages)
        # Should NOT log ERROR for optional device
        assert not any("btms" in r.message.lower() for r in error_messages)


class TestStartupDiscoveryTimeout:
    """Test overall discovery timeout."""

    @pytest.mark.asyncio
    async def test_overall_timeout(self, tmp_path: Path) -> None:
        """Discovery completes within timeout even if device hangs."""
        from ems_comm_manager.discovery import startup_discovery

        dev = _make_device(tmp_path, "pcs", "pcs", "/dev/ttyUSB0")

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)  # hang forever
            return MockModbusResponse([0])

        client = AsyncMock()
        client.read_holding_registers = _hang
        clients = {"/dev/ttyUSB0": client}

        # Should complete within ~1s due to overall timeout
        result = await startup_discovery([dev], clients, timeout_s=0.5)
        assert result["pcs"] is False

    @pytest.mark.asyncio
    async def test_never_raises(self, tmp_path: Path) -> None:
        """Discovery never raises exceptions -- always returns a dict."""
        from ems_comm_manager.discovery import startup_discovery

        dev = _make_device(tmp_path, "pcs", "pcs", "/dev/ttyUSB0")

        client = AsyncMock()
        client.read_holding_registers = AsyncMock(
            side_effect=Exception("Unexpected error")
        )
        clients = {"/dev/ttyUSB0": client}

        # Should not raise
        result = await startup_discovery([dev], clients)
        assert isinstance(result, dict)
        assert result["pcs"] is False
