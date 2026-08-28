"""Tests for CommOrchestrator -- per-port polling loops with priority ordering.

Covers: device grouping by port, priority sorting, polling loop behavior,
timeout isolation, graceful shutdown.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ems_comm_manager.modbus_device import ModbusDevice
from ems_comm_manager.orchestrator import CommOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_register_map(tmp_path: Path, name: str = "reg_map.yaml") -> str:
    """Create a minimal register map YAML."""
    content = """\
metadata:
  version: "test-1.0"
registers:
  - address: 0x0001
    name: test_reg
    scale: 1
    unit: ""
    access: r
    signed: false
    default: 0
    rtdb_field: null
"""
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _make_device(
    tmp_path: Path,
    device_id: str,
    port: str,
    priority: int,
) -> ModbusDevice:
    """Create a ModbusDevice with a test register map."""
    reg_map = _make_register_map(tmp_path, f"reg_{device_id}.yaml")
    return ModbusDevice(
        device_id=device_id,
        device_type="test",
        slave_id=1,
        port=port,
        register_map_path=reg_map,
        poll_interval_ms=500,
        timeout_s=1.0,
        priority=priority,
        push_socket=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeviceGrouping:
    """Test that CommOrchestrator groups devices by port correctly."""

    def test_single_port_single_device(self, tmp_path: Path) -> None:
        dev = _make_device(tmp_path, "pcs", "/dev/ttyUSB0", 0)
        orch = CommOrchestrator([dev], MagicMock())
        assert len(orch.port_devices) == 1
        assert "/dev/ttyUSB0" in orch.port_devices
        assert len(orch.port_devices["/dev/ttyUSB0"]) == 1

    def test_multiple_ports(self, tmp_path: Path) -> None:
        dev1 = _make_device(tmp_path, "pcs", "/dev/ttyUSB0", 0)
        dev2 = _make_device(tmp_path, "meter", "/dev/ttyUSB1", 1)
        orch = CommOrchestrator([dev1, dev2], MagicMock())
        assert len(orch.port_devices) == 2
        assert "/dev/ttyUSB0" in orch.port_devices
        assert "/dev/ttyUSB1" in orch.port_devices

    def test_same_port_multiple_devices(self, tmp_path: Path) -> None:
        dev1 = _make_device(tmp_path, "pcs", "/dev/ttyUSB0", 0)
        dev2 = _make_device(tmp_path, "btms", "/dev/ttyUSB0", 2)
        dev3 = _make_device(tmp_path, "meter", "/dev/ttyUSB0", 1)
        orch = CommOrchestrator([dev1, dev2, dev3], MagicMock())
        assert len(orch.port_devices) == 1
        devices = orch.port_devices["/dev/ttyUSB0"]
        assert len(devices) == 3


class TestPrioritySorting:
    """Test that devices within a port are sorted by priority."""

    def test_sorted_by_priority(self, tmp_path: Path) -> None:
        dev_high = _make_device(tmp_path, "pcs", "/dev/ttyUSB0", 0)
        dev_mid = _make_device(tmp_path, "meter", "/dev/ttyUSB0", 1)
        dev_low = _make_device(tmp_path, "btms", "/dev/ttyUSB0", 2)
        # Insert in reverse order
        orch = CommOrchestrator([dev_low, dev_mid, dev_high], MagicMock())
        devices = orch.port_devices["/dev/ttyUSB0"]
        assert devices[0].device_id == "pcs"
        assert devices[1].device_id == "meter"
        assert devices[2].device_id == "btms"


class TestPollingLoop:
    """Test polling loop behavior with mock client."""

    @pytest.mark.asyncio
    async def test_devices_polled_in_priority_order(self, tmp_path: Path) -> None:
        """Devices are polled in priority order within a port."""
        poll_order: list[str] = []

        dev1 = _make_device(tmp_path, "pcs", "/dev/ttyUSB0", 0)
        dev2 = _make_device(tmp_path, "meter", "/dev/ttyUSB0", 1)

        # Track poll order
        original_poll1 = dev1.poll
        original_poll2 = dev2.poll

        async def mock_poll1(client: object) -> list[int]:
            poll_order.append("pcs")
            return [100]

        async def mock_poll2(client: object) -> list[int]:
            poll_order.append("meter")
            return [200]

        dev1.poll = mock_poll1
        dev2.poll = mock_poll2

        orch = CommOrchestrator([dev2, dev1], MagicMock())

        # Run for a very short time then cancel
        with patch(
            "ems_comm_manager.orchestrator.AsyncModbusSerialClient"
        ) as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.connect = AsyncMock()
            mock_client.close = MagicMock()

            run_task = asyncio.create_task(orch.run())
            await asyncio.sleep(0.15)  # Let at least one loop iteration complete
            await orch.shutdown()

            try:
                await run_task
            except asyncio.CancelledError:
                pass

        # PCS (priority 0) should be polled before meter (priority 1)
        assert len(poll_order) >= 2
        # Find first pair
        pcs_idx = poll_order.index("pcs")
        meter_idx = poll_order.index("meter")
        assert pcs_idx < meter_idx

    @pytest.mark.asyncio
    async def test_timeout_on_one_device_does_not_block_others(
        self, tmp_path: Path
    ) -> None:
        """Timeout on one device should not prevent polling other devices."""
        polled_devices: list[str] = []

        dev_slow = _make_device(tmp_path, "slow_dev", "/dev/ttyUSB0", 0)
        dev_fast = _make_device(tmp_path, "fast_dev", "/dev/ttyUSB0", 1)

        async def slow_poll(client: object) -> list[int]:
            polled_devices.append("slow_dev")
            await asyncio.sleep(10)  # Will timeout
            return [100]

        async def fast_poll(client: object) -> list[int]:
            polled_devices.append("fast_dev")
            return [200]

        dev_slow.poll = slow_poll
        dev_slow.timeout_s = 0.05  # 50ms timeout
        dev_fast.poll = fast_poll

        orch = CommOrchestrator([dev_slow, dev_fast], MagicMock())

        with patch(
            "ems_comm_manager.orchestrator.AsyncModbusSerialClient"
        ) as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.connect = AsyncMock()
            mock_client.close = MagicMock()

            run_task = asyncio.create_task(orch.run())
            await asyncio.sleep(0.3)  # Allow enough for timeout + second poll
            await orch.shutdown()

            try:
                await run_task
            except asyncio.CancelledError:
                pass

        # Both devices should have been polled
        assert "slow_dev" in polled_devices
        assert "fast_dev" in polled_devices

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, tmp_path: Path) -> None:
        """Shutdown cancels all tasks and cleans up."""
        dev = _make_device(tmp_path, "pcs", "/dev/ttyUSB0", 0)

        async def mock_poll(client: object) -> list[int]:
            return [100]

        dev.poll = mock_poll

        orch = CommOrchestrator([dev], MagicMock())

        with patch(
            "ems_comm_manager.orchestrator.AsyncModbusSerialClient"
        ) as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.connect = AsyncMock()
            mock_client.close = MagicMock()

            run_task = asyncio.create_task(orch.run())
            await asyncio.sleep(0.1)
            await orch.shutdown()

            try:
                await run_task
            except asyncio.CancelledError:
                pass

        assert len(orch._tasks) == 0
        assert len(orch._clients) == 0
