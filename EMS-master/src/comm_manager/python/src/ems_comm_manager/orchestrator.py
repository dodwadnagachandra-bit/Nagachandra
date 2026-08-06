"""Comm orchestrator -- per-port async polling loops with priority ordering.

Creates one pymodbus client per unique connection endpoint (serial port or
TCP host:port) and spawns one asyncio task per endpoint. Devices on the same
endpoint are polled sequentially in priority order (lower = higher priority).

Supports both Modbus RTU (serial) and Modbus TCP transports, selected per
device via the protocol field in config.

50ms minimum loop period prevents busy-spin per RESEARCH.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusIOException
from pymodbus.framer import FramerType

from ems_comm_manager.discovery import startup_discovery
from ems_comm_manager.modbus_device import ModbusDevice, ModbusDeviceError
from ems_comm_manager.pcs_device import PcsDevice

logger = logging.getLogger(__name__)

# Minimum loop period to prevent busy-spin (50ms)
MIN_LOOP_PERIOD_S: float = 0.050

# Type alias for either client type
ModbusClient = AsyncModbusSerialClient | AsyncModbusTcpClient


def _monotonic_ms() -> int:
    """Return monotonic clock in milliseconds."""
    return int(time.monotonic() * 1000)


def _endpoint_key(device: ModbusDevice) -> str:
    """Return a unique key for the device's connection endpoint."""
    if device.protocol == "tcp":
        return f"tcp:{device.host}:{device.tcp_port}"
    return f"rtu:{device.port}"


class CommOrchestrator:
    """Manages per-endpoint Modbus polling loops for multiple devices.

    Groups devices by connection endpoint (serial port or TCP address) and
    polls each endpoint's devices sequentially by priority in an async task.

    Args:
        devices: List of ModbusDevice instances to poll.
        push_socket: ZMQ PUSH socket for event publishing.
        baudrate: Serial baud rate for RTU connections (default 9600).
    """

    def __init__(
        self,
        devices: list[ModbusDevice],
        push_socket: object,
        baudrate: int = 9600,
    ) -> None:
        self.push_socket = push_socket
        self.baudrate: int = baudrate
        self._tasks: list[asyncio.Task] = []
        self._clients: dict[str, ModbusClient] = {}
        self._running: bool = False

        # Group devices by endpoint, sort by priority within each group
        self._endpoint_devices: dict[str, list[ModbusDevice]] = defaultdict(list)
        for dev in devices:
            key = _endpoint_key(dev)
            self._endpoint_devices[key].append(dev)
        for key in self._endpoint_devices:
            self._endpoint_devices[key].sort(key=lambda d: d.priority)

    @property
    def port_devices(self) -> dict[str, list[ModbusDevice]]:
        """Return the endpoint-to-devices mapping (for testing)."""
        return dict(self._endpoint_devices)

    def _create_clients(self) -> None:
        """Create one Modbus client per unique endpoint."""
        for key, devices in self._endpoint_devices.items():
            if key in self._clients:
                continue
            # Use the first device's config to determine transport
            dev = devices[0]
            if dev.protocol == "tcp":
                client = AsyncModbusTcpClient(
                    host=dev.host,
                    port=dev.tcp_port,
                    framer=FramerType.SOCKET,
                    reconnect_delay=0,
                )
                logger.info("Created TCP client for %s:%d", dev.host, dev.tcp_port)
            else:
                client = AsyncModbusSerialClient(
                    port=dev.port,
                    framer=FramerType.RTU,
                    baudrate=self.baudrate,
                    reconnect_delay=0,
                )
                logger.info("Created RTU client for %s", dev.port)
            self._clients[key] = client

    async def run(self) -> None:
        """Start per-endpoint polling loops and wait for completion."""
        self._running = True

        try:
            self._create_clients()

            for key, devices in self._endpoint_devices.items():
                client = self._clients[key]
                task = asyncio.create_task(
                    self._port_polling_loop(key, client, devices),
                    name=f"modbus-poll-{key}",
                )
                self._tasks.append(task)

            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            self._running = False

    async def run_with_discovery(self, discovery_timeout_s: float = 30.0) -> None:
        """Run startup discovery then enter polling loops.

        Creates clients, runs startup_discovery to probe all devices,
        logs a summary, then starts normal polling.

        Args:
            discovery_timeout_s: Overall discovery timeout in seconds.
        """
        self._running = True

        try:
            self._create_clients()

            # Run discovery with existing clients
            all_devices: list[ModbusDevice] = []
            for devs in self._endpoint_devices.values():
                all_devices.extend(devs)

            results: dict[str, bool] = await startup_discovery(
                all_devices, self._clients, timeout_s=discovery_timeout_s
            )

            reachable: int = sum(1 for v in results.values() if v)
            total: int = len(results)
            logger.info(
                "Discovery complete: %d/%d devices reachable", reachable, total
            )

            # Start polling loops
            for key, devices in self._endpoint_devices.items():
                client = self._clients[key]
                task = asyncio.create_task(
                    self._port_polling_loop(key, client, devices),
                    name=f"modbus-poll-{key}",
                )
                self._tasks.append(task)

            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            self._running = False

    async def _port_polling_loop(
        self,
        endpoint: str,
        client: ModbusClient,
        devices: list[ModbusDevice],
    ) -> None:
        """Poll all devices on a single endpoint in priority order.

        Runs until cancelled. Each iteration:
        1. Gets current timestamp
        2. Polls each device that is ready (health.should_poll)
        3. Handles timeouts and exceptions per device
        4. Sleeps 50ms minimum loop period

        Args:
            endpoint: Endpoint identifier string.
            client: Connected pymodbus client for this endpoint.
            devices: Devices on this endpoint, sorted by priority.
        """
        try:
            await client.connect()
        except Exception:
            logger.error("Failed to connect to %s", endpoint)
            return

        try:
            while True:
                now_ms: int = _monotonic_ms()

                for device in devices:
                    if not device.health.should_poll(now_ms):
                        continue

                    try:
                        raw = await asyncio.wait_for(
                            device.poll(client),
                            timeout=device.timeout_s,
                        )
                        if raw is not None:
                            device.on_success(raw, now_ms)
                        else:
                            # Non-fatal exception (codes 01-03): record poll time only
                            device.health.record_poll(now_ms)
                    except asyncio.TimeoutError:
                        device.on_failure(now_ms, fault_type="timeout")
                    except (ModbusIOException, ModbusDeviceError):
                        device.on_failure(now_ms, fault_type="modbus_exception")
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception(
                            "Unexpected error polling %s on %s",
                            device.device_id,
                            endpoint,
                        )
                        device.on_failure(now_ms, fault_type="unknown")

                # After poll cycle: execute PCS write methods (setpoint + commands)
                for device in devices:
                    if not isinstance(device, PcsDevice):
                        continue
                    try:
                        await device.write_setpoint(client)
                        await device.process_command(client)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception(
                            "Unexpected error in PCS write methods for %s on %s",
                            device.device_id,
                            endpoint,
                        )

                await asyncio.sleep(MIN_LOOP_PERIOD_S)

        except asyncio.CancelledError:
            logger.info("Polling loop for %s cancelled", endpoint)
        finally:
            client.close()

    async def shutdown(self) -> None:
        """Cancel all polling tasks and close clients."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for client in self._clients.values():
            client.close()
        self._tasks.clear()
        self._clients.clear()
