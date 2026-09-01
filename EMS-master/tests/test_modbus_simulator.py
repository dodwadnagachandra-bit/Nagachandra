"""Modbus PCS simulator tests -- register map, state machine, signals, transport."""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

import pytest

from tools.simulators.modbus_sim.register_map import (
    CallbackDataBlock,
    build_datablock,
    load_register_map,
)
from tools.simulators.modbus_sim.signals import PCSSignalGenerator
from tools.simulators.modbus_sim.simulator import ModbusSimulator, _to_uint16
from tools.simulators.modbus_sim.state_machine import PCSState, PCSStateMachine

REG_MAP_PATH: str = "config/pcs_register_map.yaml"


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture(scope="session")
def register_map() -> dict[str, Any]:
    return load_register_map(REG_MAP_PATH)


@pytest.fixture()
def state_machine() -> PCSStateMachine:
    return PCSStateMachine(
        rated_power_kw=50.0,
        startup_delay_s=2.0,
        shutdown_delay_s=1.5,
        ramp_rate_pct_per_s=10.0,
    )


@pytest.fixture()
def signal_gen() -> PCSSignalGenerator:
    return PCSSignalGenerator(
        nominal_voltage_v=230.0,
        nominal_frequency_hz=50.0,
        phase_count=3,
    )


@pytest.fixture()
def callback_datablock(
    register_map: dict[str, Any], state_machine: PCSStateMachine
) -> CallbackDataBlock:
    return CallbackDataBlock(register_map=register_map, state_machine=state_machine)


# -- Register map tests ------------------------------------------------------


def test_register_map_loads(register_map: dict[str, Any]) -> None:
    """Register map YAML loads successfully."""
    assert "metadata" in register_map
    assert "simulator" in register_map
    assert "registers" in register_map


def test_register_map_count(register_map: dict[str, Any]) -> None:
    """Register map has at least 30 registers."""
    assert len(register_map["registers"]) >= 30


def test_register_map_no_duplicate_addresses(register_map: dict[str, Any]) -> None:
    """All register addresses are unique."""
    addresses: list[int] = [r["address"] for r in register_map["registers"]]
    assert len(addresses) == len(set(addresses)), "Duplicate addresses found"


def test_register_map_contiguous_ac_group(register_map: dict[str, Any]) -> None:
    """AC side registers (0x0001-0x000C) are contiguous."""
    ac_addrs: list[int] = sorted(
        r["address"] for r in register_map["registers"] if 0x0001 <= r["address"] <= 0x000C
    )
    expected: list[int] = list(range(0x0001, 0x000D))
    assert ac_addrs == expected


def test_register_map_contiguous_dc_group(register_map: dict[str, Any]) -> None:
    """DC side registers (0x0010-0x0013) are contiguous."""
    dc_addrs: list[int] = sorted(
        r["address"] for r in register_map["registers"] if 0x0010 <= r["address"] <= 0x0013
    )
    expected: list[int] = list(range(0x0010, 0x0014))
    assert dc_addrs == expected


def test_register_map_builds_datablock(register_map: dict[str, Any]) -> None:
    """Register map builds a valid ModbusSparseDataBlock."""
    db = build_datablock(register_map)
    # Should be able to read AC voltage L1 (address 0x0001)
    values = db.getValues(0x0001, 1)
    assert values == [2300]  # 230.0V * 10


def test_register_map_default_values(register_map: dict[str, Any]) -> None:
    """Default values are set correctly in datablock."""
    db = build_datablock(register_map)
    # AC frequency default: 5000 (50.0 Hz * 100)
    assert db.getValues(0x0007, 1) == [5000]
    # Operating state default: 0 (STANDBY)
    assert db.getValues(0x0030, 1) == [0]
    # DC voltage default: 4000 (400.0V * 10)
    assert db.getValues(0x0010, 1) == [4000]


def test_register_map_metadata(register_map: dict[str, Any]) -> None:
    """Metadata section has expected fields."""
    meta: dict[str, Any] = register_map["metadata"]
    assert meta["version"] == "V1.24-synthetic"
    assert meta["phase_count"] == 3
    assert meta["rated_power_kw"] == 50.0


# -- Callback datablock tests -----------------------------------------------


def test_datablock_write_start(
    callback_datablock: CallbackDataBlock, state_machine: PCSStateMachine
) -> None:
    """Writing 1 to on_off register triggers start command."""
    assert state_machine.state == PCSState.STANDBY
    callback_datablock.setValues(0x0291, [1])
    assert state_machine.state == PCSState.STARTING


def test_datablock_write_stop(
    callback_datablock: CallbackDataBlock, state_machine: PCSStateMachine
) -> None:
    """Writing 0 to on_off register triggers stop command."""
    # Get to RUNNING state first
    state_machine.command_start()
    for _ in range(3):
        state_machine.tick(1.0)
    assert state_machine.state == PCSState.RUNNING
    callback_datablock.setValues(0x0291, [0])
    assert state_machine.state == PCSState.STOPPING


def test_datablock_write_power_setpoint(
    callback_datablock: CallbackDataBlock, state_machine: PCSStateMachine
) -> None:
    """Writing to power setpoint register sets target power."""
    # Get to RUNNING state
    state_machine.command_start()
    for _ in range(3):
        state_machine.tick(1.0)
    assert state_machine.state == PCSState.RUNNING
    # Write 250 raw = 25.0 kW
    callback_datablock.setValues(0x500E, [250])
    assert state_machine.power_setpoint_kw == 25.0


def test_datablock_write_fault_reset(
    callback_datablock: CallbackDataBlock, state_machine: PCSStateMachine
) -> None:
    """Writing 1 to fault_reset register clears fault."""
    state_machine.set_fault(code=42)
    assert state_machine.state == PCSState.FAULT
    callback_datablock.setValues(0x0292, [1])
    assert state_machine.state == PCSState.STANDBY
    assert state_machine.fault_code == 0


# -- State machine tests -----------------------------------------------------


def test_state_machine_initial_state(state_machine: PCSStateMachine) -> None:
    """State machine starts in STANDBY."""
    assert state_machine.state == PCSState.STANDBY
    assert state_machine.current_power_kw == 0.0


def test_state_machine_start_sequence(state_machine: PCSStateMachine) -> None:
    """STANDBY -> STARTING -> RUNNING after startup delay."""
    state_machine.command_start()
    assert state_machine.state == PCSState.STARTING
    # Tick less than startup_delay_s (2.0s)
    state_machine.tick(1.0)
    assert state_machine.state == PCSState.STARTING
    # Tick past startup_delay_s
    state_machine.tick(1.5)
    assert state_machine.state == PCSState.RUNNING


def test_state_machine_stop_sequence(state_machine: PCSStateMachine) -> None:
    """RUNNING -> STOPPING -> STANDBY after shutdown delay."""
    state_machine.command_start()
    state_machine.tick(3.0)
    assert state_machine.state == PCSState.RUNNING

    state_machine.command_stop()
    assert state_machine.state == PCSState.STOPPING
    state_machine.tick(0.5)
    assert state_machine.state == PCSState.STOPPING
    state_machine.tick(1.5)
    assert state_machine.state == PCSState.STANDBY


def test_state_machine_fault_reset(state_machine: PCSStateMachine) -> None:
    """FAULT -> STANDBY on fault reset."""
    state_machine.set_fault(code=7)
    assert state_machine.state == PCSState.FAULT
    assert state_machine.fault_code == 7
    state_machine.command_fault_reset()
    assert state_machine.state == PCSState.STANDBY
    assert state_machine.fault_code == 0


def test_state_machine_power_ramp_positive(state_machine: PCSStateMachine) -> None:
    """Power ramps toward positive setpoint at configured rate."""
    state_machine.command_start()
    state_machine.tick(3.0)  # Get to RUNNING
    assert state_machine.state == PCSState.RUNNING

    state_machine.set_power_setpoint(500)  # 50.0 kW (full rated)
    # Ramp rate: 10%/s of 50 kW = 5 kW/s
    state_machine.tick(1.0)
    assert 4.0 <= state_machine.current_power_kw <= 6.0  # ~5 kW after 1s

    state_machine.tick(1.0)
    assert 9.0 <= state_machine.current_power_kw <= 11.0  # ~10 kW after 2s


def test_state_machine_power_ramp_negative(state_machine: PCSStateMachine) -> None:
    """Power ramps toward negative setpoint (discharge)."""
    state_machine.command_start()
    state_machine.tick(3.0)
    assert state_machine.state == PCSState.RUNNING

    # Negative: 65536 - 500 = 65036 (two's complement for -50.0 kW)
    state_machine.set_power_setpoint(65036)
    assert state_machine.power_setpoint_kw == -50.0
    state_machine.tick(1.0)
    assert state_machine.current_power_kw < 0  # ramping negative


def test_state_machine_setpoint_ignored_in_standby(state_machine: PCSStateMachine) -> None:
    """Power setpoint is ignored when not in RUNNING state."""
    assert state_machine.state == PCSState.STANDBY
    state_machine.set_power_setpoint(500)
    assert state_machine.power_setpoint_kw == 0.0


def test_state_machine_load_factor(state_machine: PCSStateMachine) -> None:
    """Load factor reflects current power as fraction of rated."""
    state_machine.command_start()
    state_machine.tick(3.0)
    state_machine.set_power_setpoint(250)  # 25.0 kW = 50% of rated
    for _ in range(20):
        state_machine.tick(1.0)
    # Should have ramped to ~25 kW, load_factor ~0.5
    assert 0.4 <= state_machine.load_factor <= 0.6


# -- Signal generator tests --------------------------------------------------


def test_signal_ac_voltage_range(signal_gen: PCSSignalGenerator) -> None:
    """AC voltage is within expected range around nominal."""
    for _ in range(50):
        v: float = signal_gen.ac_voltage(1)
        assert 200.0 <= v <= 260.0, f"AC voltage out of range: {v}"


def test_signal_ac_frequency_range(signal_gen: PCSSignalGenerator) -> None:
    """AC frequency is within expected range around nominal."""
    for _ in range(50):
        f: float = signal_gen.ac_frequency()
        assert 45.0 <= f <= 55.0, f"AC frequency out of range: {f}"


def test_signal_temperature_load_dependent(signal_gen: PCSSignalGenerator) -> None:
    """Temperature rises under load."""
    no_load_temps: list[float] = [signal_gen.temperature(25.0, 0.0) for _ in range(20)]
    full_load_temps: list[float] = [signal_gen.temperature(25.0, 1.0) for _ in range(20)]
    avg_no_load: float = sum(no_load_temps) / len(no_load_temps)
    avg_full_load: float = sum(full_load_temps) / len(full_load_temps)
    assert avg_full_load > avg_no_load + 10.0, "Full load should be notably hotter"


def test_signal_single_phase_zeros() -> None:
    """Single-phase generator returns 0.0 for phases 2 and 3."""
    gen: PCSSignalGenerator = PCSSignalGenerator(phase_count=1)
    assert gen.ac_voltage(1) > 0.0
    assert gen.ac_voltage(2) == 0.0
    assert gen.ac_voltage(3) == 0.0


# -- Uint16 conversion tests -------------------------------------------------


def test_to_uint16_positive() -> None:
    """Positive value scaled correctly."""
    assert _to_uint16(230.0, 10, signed=False) == 2300


def test_to_uint16_negative_signed() -> None:
    """Negative signed value uses two's complement."""
    result: int = _to_uint16(-25.0, 10, signed=True)
    # -250 + 65536 = 65286
    assert result == 65286


def test_to_uint16_clamped_high() -> None:
    """Values above 65535 are clamped."""
    result: int = _to_uint16(7000.0, 10, signed=False)
    assert result == 65535


def test_to_uint16_clamped_low() -> None:
    """Negative unsigned values are clamped to 0."""
    result: int = _to_uint16(-10.0, 10, signed=False)
    assert result == 0


# -- TCP integration tests ---------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run an async coroutine in a new event loop (no pytest-asyncio needed)."""
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _tcp_roundtrip_read() -> None:
    """Start TCP server, read holding registers, verify response."""
    from pymodbus.client import AsyncModbusTcpClient
    from pymodbus.transport import NULLMODEM_HOST

    sim: ModbusSimulator = ModbusSimulator(
        config_path="config/pcs_config.yaml",
        transport="tcp",
        tcp_port=5021,
    )

    server_task: asyncio.Task[None] = asyncio.create_task(sim.run())
    await asyncio.sleep(0.5)  # Let server start

    try:
        client: AsyncModbusTcpClient = AsyncModbusTcpClient(NULLMODEM_HOST, port=5021)
        await client.connect()
        assert client.connected

        # Read AC voltage L1 (address 0x0001)
        result = await client.read_holding_registers(address=0x0001, count=1,device_id=1)
        assert not result.isError(), f"Read error: {result}"
        # Should be near default 2300 (230V * 10) or telemetry-updated
        assert result.registers[0] > 0

        client.close()
    finally:
        sim.stop()
        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


def test_tcp_roundtrip_read() -> None:
    """TCP client can read holding registers from simulator."""
    _run_async(_tcp_roundtrip_read())


async def _tcp_write_triggers_state() -> None:
    """Write on_off=1 via TCP, verify state transitions to STARTING."""
    from pymodbus.client import AsyncModbusTcpClient
    from pymodbus.transport import NULLMODEM_HOST

    sim: ModbusSimulator = ModbusSimulator(
        config_path="config/pcs_config.yaml",
        transport="tcp",
        tcp_port=5022,
    )

    server_task: asyncio.Task[None] = asyncio.create_task(sim.run())
    await asyncio.sleep(0.5)

    try:
        client: AsyncModbusTcpClient = AsyncModbusTcpClient(NULLMODEM_HOST, port=5022)
        await client.connect()

        # Write on_off = 1 (start PCS)
        result = await client.write_register(address=0x0291, value=1)
        assert not result.isError(), f"Write error: {result}"

        # State should be STARTING or RUNNING
        assert sim.state_machine.state in (PCSState.STARTING, PCSState.RUNNING)

        client.close()
    finally:
        sim.stop()
        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


def test_tcp_write_triggers_state() -> None:
    """TCP write to on_off register triggers state machine transition."""
    _run_async(_tcp_write_triggers_state())


async def _tcp_telemetry_coherence() -> None:
    """After starting PCS and setting power, telemetry values are coherent."""
    from pymodbus.client import AsyncModbusTcpClient
    from pymodbus.transport import NULLMODEM_HOST

    sim: ModbusSimulator = ModbusSimulator(
        config_path="config/pcs_config.yaml",
        transport="tcp",
        tcp_port=5023,
    )

    server_task: asyncio.Task[None] = asyncio.create_task(sim.run())
    await asyncio.sleep(0.5)

    try:
        client: AsyncModbusTcpClient = AsyncModbusTcpClient(NULLMODEM_HOST, port=5023)
        await client.connect()

        # Start PCS
        await client.write_register(address=0x0291, value=1)
        # Wait for RUNNING state + telemetry update
        await asyncio.sleep(3.5)

        # Set power to 25 kW (raw = 250)
        await client.write_register(address=0x500E, value=250)
        # Wait for ramp
        await asyncio.sleep(3.0)

        # Read operating state
        state_result = await client.read_holding_registers(address=0x0030, count=1)
        assert not state_result.isError()
        assert state_result.registers[0] == PCSState.RUNNING

        # Read AC voltage -- should be nonzero (always live)
        v_result = await client.read_holding_registers(address=0x0001, count=1)
        assert not v_result.isError()
        assert v_result.registers[0] > 0, "AC voltage should be live"

        # Read AC active power -- should be nonzero after ramp
        p_result = await client.read_holding_registers(address=0x0008, count=1)
        assert not p_result.isError()
        assert p_result.registers[0] > 0, "Active power should be nonzero in RUNNING"

        client.close()
    finally:
        sim.stop()
        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


def test_tcp_telemetry_coherence() -> None:
    """Telemetry is coherent: power/current nonzero in RUNNING, voltage always live."""
    _run_async(_tcp_telemetry_coherence())


# -- RTU integration tests (require socat) -----------------------------------


HAS_SOCAT: bool = shutil.which("socat") is not None


@pytest.mark.rtu
@pytest.mark.skipif(not HAS_SOCAT, reason="socat not available")
def test_rtu_roundtrip_read() -> None:
    """RTU client can read holding registers over socat PTY pair."""

    async def _rtu_test() -> None:
        from pymodbus.client import AsyncModbusSerialClient
        from pymodbus.framer import FramerType

        sim: ModbusSimulator = ModbusSimulator(
            config_path="config/pcs_config.yaml",
            transport="rtu",
        )

        server_task: asyncio.Task[None] = asyncio.create_task(sim.run())
        await asyncio.sleep(1.0)  # socat + server startup

        try:
            assert sim._pty_pair is not None
            client: AsyncModbusSerialClient = AsyncModbusSerialClient(
                sim._pty_pair.client_path,
                framer=FramerType.RTU,
                baudrate=9600,
                parity="N",
                stopbits=1,
                timeout=3,
            )
            await client.connect()
            assert client.connected

            result = await client.read_holding_registers(address=0x0001, count=1, device_id=1)
            assert not result.isError(), f"RTU read error: {result}"
            assert result.registers[0] > 0

            client.close()
        finally:
            sim.stop()
            try:
                await asyncio.wait_for(server_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    _run_async(_rtu_test())
