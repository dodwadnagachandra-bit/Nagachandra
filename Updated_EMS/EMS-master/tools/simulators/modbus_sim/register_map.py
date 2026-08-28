"""Register map loader and callback datablock for Modbus PCS simulator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pymodbus.datastore import ModbusSparseDataBlock

if TYPE_CHECKING:
    from tools.simulators.modbus_sim.state_machine import PCSStateMachine

log: logging.Logger = logging.getLogger(__name__)


def load_register_map(path: str | Path) -> dict[str, Any]:
    """Load register map YAML and return the parsed dict.

    Args:
        path: Path to the register map YAML file.

    Returns:
        Dict with 'metadata', 'simulator', and 'registers' keys.
    """
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f)
    log.info("Loaded register map: %s (%d registers)", path, len(data["registers"]))
    return data


def build_datablock(register_map: dict[str, Any]) -> ModbusSparseDataBlock:
    """Build a ModbusSparseDataBlock from register map with default values.

    Args:
        register_map: Parsed register map dict.

    Returns:
        ModbusSparseDataBlock populated with default values.
    """
    values: dict[int, int] = {}
    for reg in register_map["registers"]:
        address: int = reg["address"]
        default: int = reg.get("default", 0)
        values[address] = default
    return ModbusSparseDataBlock(values)


class CallbackDataBlock(ModbusSparseDataBlock):
    """Sparse datablock that dispatches control register writes to the state machine.

    Intercepts FC06 (write single register) for control registers:
    - 0x0291 (on_off): 1 = start, 0 = stop
    - 0x0292 (fault_reset): 1 = reset fault
    - 0x500E (active_power_setpoint): set power target
    """

    # Control register addresses
    ADDR_ON_OFF: int = 0x0291
    ADDR_FAULT_RESET: int = 0x0292
    ADDR_ACTIVE_POWER_SETPOINT: int = 0x500E

    def __init__(
        self,
        register_map: dict[str, Any],
        state_machine: PCSStateMachine,
        fault_cfg: dict[str, Any] | None = None,
    ) -> None:
        values: dict[int, int] = {}
        for reg in register_map["registers"]:
            values[reg["address"]] = reg.get("default", 0)
        super().__init__(values)
        self._state_machine: PCSStateMachine = state_machine
        self._register_map: dict[str, Any] = register_map

        # Fault injection: exception registers
        fc: dict[str, Any] = fault_cfg or {}
        self._exception_registers: set[int] = set(fc.get("exception_registers", []))
        self._exception_code: int = fc.get("exception_code", 0x02)

    def getValues(self, address: int, count: int = 1) -> list[int]:  # noqa: N802
        """Override getValues to inject Modbus exceptions on configured registers."""
        if self._exception_registers:
            # Check if any requested register falls in the exception set
            for offset in range(count):
                if (address + offset) in self._exception_registers:
                    log.debug(
                        "FAULT: exception code %d for register 0x%04X",
                        self._exception_code, address + offset,
                    )
                    # Return ExceptionResponse-compatible error via pymodbus convention:
                    # returning a ModbusExceptions value triggers the server to send
                    # an exception response. We use the ExceptionResponse class.
                    from pymodbus.pdu import ExceptionResponse

                    return ExceptionResponse(self._exception_code)  # type: ignore[return-value]
        return super().getValues(address, count)

    def setValues(self, address: int, values: list[int], use_as_default: bool = False) -> None:  # noqa: N802
        """Override setValues to intercept control register writes."""
        super().setValues(address, values, use_as_default=use_as_default)

        if not values:
            return

        value: int = values[0]

        if address == self.ADDR_ON_OFF:
            if value == 1:
                self._state_machine.command_start()
            elif value == 0:
                self._state_machine.command_stop()
            log.debug("Write on_off register: %d", value)

        elif address == self.ADDR_FAULT_RESET:
            if value == 1:
                self._state_machine.command_fault_reset()
            log.debug("Write fault_reset register: %d", value)

        elif address == self.ADDR_ACTIVE_POWER_SETPOINT:
            self._state_machine.set_power_setpoint(value)
            log.debug("Write active_power_setpoint register: %d", value)
