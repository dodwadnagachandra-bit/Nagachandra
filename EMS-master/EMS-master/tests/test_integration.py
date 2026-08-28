"""Integration smoke tests for EMS simulators.

Tests that all 3 simulators (CAN, Modbus, GPIO) can start, exercise one
operation, and validate fault injection. Designed to run in CI after
the build-and-test job.

Requires:
  - vcan0 interface up (CAN tests skip if unavailable)
  - uv run python -m tools.simulators.can_sim available
  - pymodbus for Modbus TCP in-process tests
  - POSIX shared memory for GPIO RTDB backend

Run with: uv run pytest tests/test_integration.py -v -m integration
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run an async coroutine in a new event loop (no pytest-asyncio needed)."""
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _vcan0_available() -> bool:
    """Check if vcan0 is up."""
    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ["ip", "link", "show", "vcan0"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and "UP" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def vcan0_up() -> None:
    """Ensure vcan0 interface is up. Skip if not available."""
    if not _vcan0_available():
        pytest.skip("vcan0 not available (need: sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set up vcan0)")


@pytest.fixture(scope="module")
def can_sim_process(vcan0_up: None) -> subprocess.Popen[bytes]:
    """Start CAN simulator as a subprocess with residential profile."""
    proc: subprocess.Popen[bytes] = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "tools.simulators.can_sim",
            "--interface", "vcan0",
            "--config", "config/profiles/residential/bms_config.yaml",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for simulator to initialize and start sending frames
    time.sleep(3)
    yield proc  # type: ignore[misc]
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="module")
def gpio_backend() -> Any:
    """Create an RtdbBackend with a unique shm name for integration testing."""
    from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

    shm_name: str = f"ems_rtdb_inttest_{os.getpid()}"
    backend: RtdbBackend = RtdbBackend(shm_name=shm_name)
    yield backend
    backend.close()


# ---------------------------------------------------------------------------
# CAN tests
# ---------------------------------------------------------------------------


def test_can_sends_frames(vcan0_up: None, can_sim_process: subprocess.Popen[bytes]) -> None:
    """CAN simulator sends extended frames on vcan0."""
    import can as python_can

    bus: python_can.Bus = python_can.Bus(interface="socketcan", channel="vcan0")
    try:
        msg: python_can.Message | None = bus.recv(timeout=5.0)
        assert msg is not None, "No CAN frame received within 5s"
        assert msg.is_extended_id is True, "Expected 29-bit extended CAN ID"
    finally:
        bus.shutdown()


# ---------------------------------------------------------------------------
# Modbus tests (in-process using NULLMODEM_HOST for reliable TCP transport)
# ---------------------------------------------------------------------------


def test_modbus_read_write() -> None:
    """Modbus TCP simulator responds to read and write operations."""

    async def _modbus_ops() -> None:
        from pymodbus.client import AsyncModbusTcpClient
        from pymodbus.transport import NULLMODEM_HOST

        from tools.simulators.modbus_sim.simulator import ModbusSimulator

        sim: ModbusSimulator = ModbusSimulator(
            config_path="config/profiles/residential/pcs_config.yaml",
            transport="tcp",
            tcp_port=5030,
        )

        server_task: asyncio.Task[None] = asyncio.create_task(sim.run())
        await asyncio.sleep(0.5)

        try:
            client: AsyncModbusTcpClient = AsyncModbusTcpClient(
                NULLMODEM_HOST, port=5030
            )
            await client.connect()
            assert client.connected

            # Read holding register 0x500E (active power setpoint)
            result = await client.read_holding_registers(address=0x500E, count=1)
            assert not result.isError(), f"Read error: {result}"

            # Write to on_off register 0x0291 (start PCS)
            write_result = await client.write_register(address=0x0291, value=1)
            assert not write_result.isError(), f"Write error: {write_result}"

            # Read back on_off -- state machine should have transitioned
            read_back = await client.read_holding_registers(address=0x0291, count=1)
            assert not read_back.isError(), f"Read-back error: {read_back}"

            client.close()
        finally:
            sim.stop()
            try:
                await asyncio.wait_for(server_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    _run_async(_modbus_ops())


# ---------------------------------------------------------------------------
# GPIO tests
# ---------------------------------------------------------------------------


def test_gpio_set_and_read(gpio_backend: Any) -> None:
    """GPIO RTDB backend supports set/get DI pin operations."""
    # Set DI-0 high
    gpio_backend.set_di(0, 1)
    assert gpio_backend.get_di(0) == 1

    # Clear DI-0
    gpio_backend.set_di(0, 0)
    assert gpio_backend.get_di(0) == 0


# ---------------------------------------------------------------------------
# Fault injection tests
# ---------------------------------------------------------------------------


def test_can_fault_injection(vcan0_up: None) -> None:
    """CAN simulator with 100% frame drop rate sends no frames."""
    # Create a temporary bms_config with fault_injection
    base_config_path: str = "config/profiles/residential/bms_config.yaml"
    with open(base_config_path) as f:
        config: dict[str, Any] = yaml.safe_load(f)

    config["fault_injection"] = {"frame_drop_rate": 1.0}

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, prefix="bms_fault_"
    ) as tmp:
        yaml.dump(config, tmp)
        tmp_path: str = tmp.name

    proc: subprocess.Popen[bytes] = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "tools.simulators.can_sim",
            "--interface", "vcan0",
            "--config", tmp_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for sim to start
        time.sleep(3)

        import can as python_can

        bus: python_can.Bus = python_can.Bus(interface="socketcan", channel="vcan0")
        try:
            # With 100% drop rate, no frames should arrive within timeout
            msg: python_can.Message | None = bus.recv(timeout=3.0)
            assert msg is None, "Expected no frames with 100% drop rate"
        finally:
            bus.shutdown()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        os.unlink(tmp_path)


def test_modbus_fault_injection() -> None:
    """Modbus simulator with exception_registers returns errors for faulted registers."""

    async def _modbus_fault() -> None:
        from pymodbus.client import AsyncModbusTcpClient
        from pymodbus.transport import NULLMODEM_HOST

        from tools.simulators.modbus_sim.simulator import ModbusSimulator

        # Create temp config with fault injection (exception on register 0x0001)
        base_config_path: str = "config/profiles/residential/pcs_config.yaml"
        with open(base_config_path) as f:
            config: dict[str, Any] = yaml.safe_load(f)

        config["fault_injection"] = {
            "exception_code": 4,
            "exception_registers": [1],  # AC voltage L1
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix="pcs_fault_"
        ) as tmp:
            yaml.dump(config, tmp)
            tmp_path: str = tmp.name

        try:
            sim: ModbusSimulator = ModbusSimulator(
                config_path=tmp_path,
                transport="tcp",
                tcp_port=5031,
            )

            server_task: asyncio.Task[None] = asyncio.create_task(sim.run())
            await asyncio.sleep(0.5)

            try:
                client: AsyncModbusTcpClient = AsyncModbusTcpClient(
                    NULLMODEM_HOST, port=5031
                )
                await client.connect()

                # Read faulted register -- should return error
                result = await client.read_holding_registers(address=0x0001, count=1)
                assert result.isError(), "Expected error for faulted register 0x0001"

                # Read non-faulted register -- should succeed
                ok_result = await client.read_holding_registers(address=0x0007, count=1)
                assert not ok_result.isError(), f"Non-faulted register should succeed: {ok_result}"

                client.close()
            finally:
                sim.stop()
                try:
                    await asyncio.wait_for(server_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        finally:
            os.unlink(tmp_path)

    _run_async(_modbus_fault())


def test_gpio_fault_injection() -> None:
    """GPIO RTDB backend with stuck_pins ignores writes to stuck pins."""
    from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

    shm_name: str = f"ems_rtdb_fault_{os.getpid()}"
    backend: RtdbBackend = RtdbBackend(
        shm_name=shm_name,
        fault_cfg={"stuck_pins": [3]},
    )

    try:
        # Stuck pin 3: write should be ignored, value stays at default 0
        backend.set_di(3, 1)
        assert backend.get_di(3) == 0, "Stuck pin DI-3 should ignore writes"

        # Non-stuck pin 0: write should work normally
        backend.set_di(0, 1)
        assert backend.get_di(0) == 1, "Non-stuck pin DI-0 should accept writes"

        # Clean up non-stuck pin
        backend.set_di(0, 0)
    finally:
        backend.close()
