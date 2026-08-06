"""ModbusSimulator -- top-level orchestrator for PCS Modbus simulation."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import yaml
from pymodbus.datastore import ModbusDeviceContext, ModbusServerContext
from pymodbus.pdu import ExceptionResponse
from pymodbus.server import StartAsyncSerialServer, StartAsyncTcpServer
from pymodbus.transport import NULLMODEM_HOST

from tools.simulators.modbus_sim.pty_pair import PTYPair
from tools.simulators.modbus_sim.register_map import CallbackDataBlock, load_register_map
from tools.simulators.modbus_sim.signals import PCSSignalGenerator
from tools.simulators.modbus_sim.state_machine import PCSState, PCSStateMachine

log: logging.Logger = logging.getLogger(__name__)


class _ZeroModeDeviceContext(ModbusDeviceContext):
    """ModbusDeviceContext with zero-mode addressing (no +1 offset).

    pymodbus 3.12+ removed zero_mode parameter. This subclass restores
    the zero-mode behavior where Modbus address N maps directly to
    datablock key N, without the default +1 offset.
    """

    def getValues(  # noqa: N802
        self, func_code: int, address: int, count: int = 1
    ) -> list[int] | list[bool] | ExceptionResponse:
        """Get values without the default +1 address offset."""
        address -= 1  # Counteract the +1 added by parent
        return super().getValues(func_code, address, count)

    def setValues(  # noqa: N802
        self, func_code: int, address: int, values: list[int]
    ) -> None | ExceptionResponse:
        """Set values without the default +1 address offset."""
        address -= 1  # Counteract the +1 added by parent
        return super().setValues(func_code, address, values)


def _to_uint16(value: float, scale: int, signed: bool) -> int:
    """Convert an engineering value to a uint16 register value.

    Args:
        value: Engineering value (e.g., 230.5 V).
        scale: Scaling factor (raw = value * scale).
        signed: If True, apply two's complement for negative values.

    Returns:
        Clamped uint16 value [0, 65535].
    """
    raw: int = int(round(value * scale))
    if signed and raw < 0:
        raw = raw + 65536
    return max(0, min(65535, raw))


class ModbusSimulator:
    """Orchestrates Modbus PCS simulation with state machine and telemetry.

    Supports TCP and RTU transport modes. In RTU mode, automatically creates
    a socat PTY pair for virtual serial communication.
    """

    def __init__(
        self,
        config_path: str = "config/pcs_config.yaml",
        transport: str = "tcp",
        tcp_port: int = 5020,
        verbose: bool = False,
    ) -> None:
        with open(config_path) as f:
            self._pcs_config: dict[str, Any] = yaml.safe_load(f)

        # Load register map
        reg_map_path: str = self._pcs_config.get(
            "register_map_path", "config/pcs_register_map.yaml"
        )
        self._register_map: dict[str, Any] = load_register_map(reg_map_path)

        meta: dict[str, Any] = self._register_map.get("metadata", {})
        sim_cfg: dict[str, Any] = self._register_map.get("simulator", {})

        self._phase_count: int = meta.get("phase_count", 3)
        self._rated_power_kw: float = meta.get("rated_power_kw", 50.0)
        self._nominal_voltage_v: float = meta.get("nominal_voltage_v", 230.0)
        self._nominal_frequency_hz: float = meta.get("nominal_frequency_hz", 50.0)

        # State machine
        self.state_machine: PCSStateMachine = PCSStateMachine(
            rated_power_kw=self._rated_power_kw,
            startup_delay_s=sim_cfg.get("startup_delay_s", 2.0),
            shutdown_delay_s=sim_cfg.get("shutdown_delay_s", 1.5),
            ramp_rate_pct_per_s=sim_cfg.get("ramp_rate_pct_per_s", 10.0),
        )

        # Signal generator
        self.signal_gen: PCSSignalGenerator = PCSSignalGenerator(
            nominal_voltage_v=self._nominal_voltage_v,
            nominal_frequency_hz=self._nominal_frequency_hz,
            phase_count=self._phase_count,
        )

        # Fault injection config
        self._fault_cfg: dict[str, Any] = self._pcs_config.get("fault_injection", {})
        self._signal_tuning: dict[str, Any] = self._pcs_config.get("signal_tuning", {})

        if self._fault_cfg:
            log.info(
                "FAULT: %s",
                ", ".join(f"{k}={v}" for k, v in self._fault_cfg.items()),
            )
        if self._signal_tuning:
            log.info(
                "TUNING: %s",
                ", ".join(f"{k}={v}" for k, v in self._signal_tuning.items()),
            )

        # Callback datablock
        self.datablock: CallbackDataBlock = CallbackDataBlock(
            register_map=self._register_map,
            state_machine=self.state_machine,
            fault_cfg=self._fault_cfg if self._fault_cfg else None,
        )

        # Server context: single device with zero-mode addressing
        device: _ZeroModeDeviceContext = _ZeroModeDeviceContext(
            hr=self.datablock,
        )
        self._context: ModbusServerContext = ModbusServerContext(
            devices=device,
            single=True,
        )

        self._transport: str = transport
        self._tcp_port: int = tcp_port
        self._verbose: bool = verbose
        self._pty_pair: PTYPair | None = None
        self._running: bool = False
        self._telemetry_task: asyncio.Task[None] | None = None
        self._server_task: asyncio.Task[None] | None = None

        # Build register lookup by name for fast telemetry updates
        self._reg_by_name: dict[str, dict[str, Any]] = {}
        for reg in self._register_map["registers"]:
            self._reg_by_name[reg["name"]] = reg

    async def run(self) -> None:
        """Start the Modbus server and telemetry loop."""
        self._running = True

        # Fault: response timeout -- delay server startup
        if self._fault_cfg.get("response_timeout", False):
            delay: float = self._fault_cfg.get("timeout_duration_s", 10.0)
            log.info("FAULT: response_timeout active, delaying server start by %.1fs", delay)
            await asyncio.sleep(delay)

        if self._transport == "rtu":
            self._pty_pair = PTYPair()
            self._pty_pair.open()
            port: str = self._pty_pair.sim_path
            log.info("Starting Modbus RTU server on %s", port)

            from pymodbus.framer import FramerType

            self._server_task = asyncio.create_task(
                StartAsyncSerialServer(
                    context=self._context,
                    port=port,
                    framer=FramerType.RTU,
                    baudrate=self._pcs_config.get("connection", {}).get("baud_rate", 9600),
                    parity=self._pcs_config.get("connection", {}).get("parity", "N"),
                    stopbits=self._pcs_config.get("connection", {}).get("stopbits", 1),
                )
            )
        else:
            log.info("Starting Modbus TCP server on port %d", self._tcp_port)
            self._server_task = asyncio.create_task(
                StartAsyncTcpServer(
                    context=self._context,
                    address=(NULLMODEM_HOST, self._tcp_port),
                )
            )

        self._telemetry_task = asyncio.create_task(self._telemetry_loop())

        try:
            await asyncio.gather(self._server_task, self._telemetry_task)
        except asyncio.CancelledError:
            pass

    async def _telemetry_loop(self) -> None:
        """Update all telemetry registers at 1 Hz based on state machine."""
        try:
            while self._running:
                self.state_machine.tick(1.0)
                self._update_registers()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    def _update_registers(self) -> None:
        """Write current telemetry values to all registers."""
        sm: PCSStateMachine = self.state_machine
        sg: PCSSignalGenerator = self.signal_gen
        is_running: bool = sm.state == PCSState.RUNNING

        # AC voltage -- visible in ALL states (with optional noise)
        voltage_noise: float = self._signal_tuning.get("voltage_noise", 0.0)
        for phase_idx, name in enumerate(["ac_voltage_l1", "ac_voltage_l2", "ac_voltage_l3"], 1):
            v: float = sg.ac_voltage(phase_idx)
            if voltage_noise > 0:
                v += random.gauss(0, voltage_noise)
            self._set_reg(name, v)

        # AC frequency -- visible in ALL states
        self._set_reg("ac_frequency", sg.ac_frequency())

        # AC current and power -- zero unless RUNNING
        power_kw: float = sm.current_power_kw if is_running else 0.0
        voltage_v: float = sg.ac_voltage(1)  # Use L1 for calculation

        if is_running and voltage_v > 0 and self._phase_count > 0:
            current_a: float = abs(power_kw * 1000.0) / (voltage_v * self._phase_count)
        else:
            current_a = 0.0

        for phase_idx, name in enumerate(["ac_current_l1", "ac_current_l2", "ac_current_l3"], 1):
            if phase_idx <= self._phase_count:
                self._set_reg(name, current_a if is_running else 0.0)
            else:
                self._set_reg(name, 0.0)

        self._set_reg("ac_active_power", power_kw)
        self._set_reg("ac_reactive_power", 0.0)
        apparent_kva: float = abs(power_kw)  # PF ~1.0
        self._set_reg("ac_apparent_power", apparent_kva)
        pf: float = 1.0 if apparent_kva > 0 else 1.0
        self._set_reg("ac_power_factor", pf)

        # DC side
        self._set_reg("dc_voltage", sg.dc_voltage())
        dc_power_kw: float = power_kw * 1.02 if is_running else 0.0  # 2% efficiency loss
        self._set_reg("dc_power", dc_power_kw)
        dc_v: float = sg.dc_voltage()
        dc_current: float = (dc_power_kw * 1000.0 / dc_v) if (is_running and dc_v > 0) else 0.0
        self._set_reg("dc_current", dc_current)
        self._set_reg("dc_bus_voltage", sg.dc_bus_voltage())

        # Thermal
        load_factor: float = sm.load_factor
        self._set_reg("temp_heatsink", sg.temperature(30.0, load_factor))
        self._set_reg("temp_ambient", sg.temperature(25.0, 0.0))
        self._set_reg("temp_internal", sg.temperature(35.0, load_factor * 0.5))

        # Status
        self._set_reg("operating_state", float(sm.state))
        self._set_reg("fault_code", float(sm.fault_code))

    def _set_reg(self, name: str, value: float) -> None:
        """Set a register by name to a scaled uint16 value."""
        reg: dict[str, Any] | None = self._reg_by_name.get(name)
        if reg is None:
            return
        address: int = reg["address"]
        scale: int = reg.get("scale", 1)
        signed: bool = reg.get("signed", False)
        raw: int = _to_uint16(value, scale, signed)
        self.datablock.setValues(address, [raw])

    def stop(self) -> None:
        """Stop the simulator, cancel tasks, clean up resources."""
        self._running = False
        if self._telemetry_task is not None:
            self._telemetry_task.cancel()
        if self._server_task is not None:
            self._server_task.cancel()
        if self._pty_pair is not None:
            self._pty_pair.close()
            self._pty_pair = None
        log.info("Modbus simulator stopped")
