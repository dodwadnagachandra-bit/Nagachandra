"""ModbusDevice base class for Modbus RTU polling.

Provides the core poll/on_success/on_failure lifecycle for each Modbus device.
Subclasses implement _write_to_rtdb() to map decoded register values to RTDB.

Uses DeviceHealth for state tracking and exponential backoff.
Uses RegisterDef and scale_value for register processing.
"""

from __future__ import annotations

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from ems_comm_manager.events import (
    publish_comm_exception,
    publish_comm_fault,
    publish_comm_recovery,
)
from ems_comm_manager.health import DeviceHealth
from ems_comm_manager.register_map import (
    RegisterDef,
    build_read_ranges,
    load_register_map,
    scale_value,
)


class ModbusDeviceError(Exception):
    """Raised when a Modbus device encounters a fatal exception (e.g., code 04)."""


class ModbusDevice:
    """Base class for polling a single Modbus RTU device.

    Manages register reads, health tracking, and event publishing.
    Subclasses must implement ``_write_to_rtdb()`` to write decoded values.

    Attributes:
        device_id: Unique identifier for this device (e.g., "pcs").
        device_type: Device type string (e.g., "pcs", "btms").
        slave_id: Modbus slave/unit address.
        port: Serial port path (e.g., "/dev/ttyUSB0").
        timeout_s: Per-request timeout in seconds.
        priority: Polling priority within a port (lower = higher priority).
        push_socket: ZMQ PUSH socket for event publishing.
    """

    def __init__(
        self,
        device_id: str,
        device_type: str,
        slave_id: int,
        port: str,
        register_map_path: str,
        poll_interval_ms: int,
        timeout_s: float,
        priority: int,
        push_socket: object,
        protocol: str = "rtu",
        host: str = "127.0.0.1",
        tcp_port: int = 502,
    ) -> None:
        self.device_id: str = device_id
        self.device_type: str = device_type
        self.slave_id: int = slave_id
        self.port: str = port
        self.protocol: str = protocol
        self.host: str = host
        self.tcp_port: int = tcp_port
        self.timeout_s: float = timeout_s
        self.priority: int = priority
        self.push_socket = push_socket

        # Load register map and build read ranges
        self.register_map: list[RegisterDef] = load_register_map(register_map_path)
        self.read_ranges: list[tuple[int, int]] = build_read_ranges(self.register_map)

        # Health tracking
        self.health: DeviceHealth = DeviceHealth(
            device_id=device_id,
            poll_interval_ms=poll_interval_ms,
        )

    async def poll(self, client: AsyncModbusSerialClient | AsyncModbusTcpClient) -> list[int] | None:
        """Read holding registers from the device.

        For each read range, issues a read_holding_registers call.
        Handles Modbus exception codes per COMM-07:
        - Codes 01-03: publish comm_exception, return None (no offline transition).
        - Code 04: publish comm_exception AND raise ModbusDeviceError.

        Args:
            client: Connected pymodbus async serial client.

        Returns:
            List of raw register values, or None if non-fatal exception.

        Raises:
            ModbusDeviceError: On exception code 04 (Device Failure).
        """
        raw_values: list[int] = []

        for start, count in self.read_ranges:
            result = await client.read_holding_registers(
                address=start,
                count=count,
                device_id=self.slave_id,
            )

            if result.isError():
                exc_code: int = getattr(result, "exception_code", 0)
                exc_names: dict[int, str] = {
                    1: "Illegal Function",
                    2: "Illegal Data Address",
                    3: "Illegal Data Value",
                    4: "Device Failure",
                }
                exc_msg: str = exc_names.get(exc_code, f"Unknown ({exc_code})")

                publish_comm_exception(
                    push_socket=self.push_socket,
                    device_id=self.device_id,
                    device_address=self.slave_id,
                    port=self.port,
                    exception_code=exc_code,
                    message=exc_msg,
                )

                if exc_code >= 4:
                    raise ModbusDeviceError(
                        f"Device {self.device_id} exception {exc_code}: {exc_msg}"
                    )
                return None

            raw_values.extend(result.registers)

        return raw_values

    def on_success(self, raw_values: list[int], now_ms: int) -> None:
        """Handle a successful poll result.

        Records health success, publishes recovery if transitioning
        offline->online, writes decoded values to RTDB via subclass.

        Args:
            raw_values: Raw register values from poll().
            now_ms: Current monotonic timestamp in milliseconds.
        """
        recovery: dict | None = self.health.record_success(now_ms)
        if recovery is not None:
            publish_comm_recovery(
                push_socket=self.push_socket,
                device_id=self.device_id,
                device_address=self.slave_id,
                port=self.port,
                offline_duration_ms=recovery["offline_duration_ms"],
            )
        self._write_to_rtdb(raw_values, now_ms)
        self.health.record_poll(now_ms)

    def on_failure(self, now_ms: int, fault_type: str = "timeout") -> None:
        """Handle a failed poll attempt.

        Records health failure, publishes comm_fault if crossing
        offline threshold.

        Args:
            now_ms: Current monotonic timestamp in milliseconds.
            fault_type: Type of fault (e.g., "timeout", "exception_04").
        """
        fault: dict | None = self.health.record_failure(now_ms)
        if fault is not None:
            publish_comm_fault(
                push_socket=self.push_socket,
                device_id=self.device_id,
                device_address=self.slave_id,
                port=self.port,
                fault_type=fault_type,
                last_seen_ms=self.health.last_success_ms,
                consecutive_failures=fault["consecutive_failures"],
            )
        self.health.record_poll(now_ms)

    def _write_to_rtdb(self, raw_values: list[int], now_ms: int) -> None:
        """Write decoded register values to RTDB. Subclass must implement.

        Args:
            raw_values: Raw register values from poll().
            now_ms: Current monotonic timestamp in milliseconds.
        """
        pass  # Base class no-op; subclasses override

    @property
    def should_poll(self) -> bool:
        """Check if this device is ready for its next poll cycle.

        Delegates to DeviceHealth.should_poll() but requires a now_ms arg
        in practice. The orchestrator passes now_ms via health directly.
        """
        return True  # Orchestrator checks health.should_poll(now_ms) directly
