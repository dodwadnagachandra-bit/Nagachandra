"""Startup discovery -- probes all configured Modbus devices at startup.

Per-endpoint sequential probing (one device at a time per connection),
cross-endpoint parallel probing (all endpoints probed concurrently via asyncio.gather).

Unreachable devices are marked offline but never block startup.
Mandatory devices (PCS, BMS) log ERROR; optional devices log WARNING.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from ems_comm_manager.modbus_device import ModbusDevice

logger = logging.getLogger(__name__)

# Mandatory devices log ERROR on unreachable; all others log WARNING
MANDATORY_DEVICES: set[str] = {"pcs", "bms"}

# Type alias for either client type
ModbusClient = AsyncModbusSerialClient | AsyncModbusTcpClient


def _endpoint_key(device: ModbusDevice) -> str:
    """Return a unique key for the device's connection endpoint."""
    if device.protocol == "tcp":
        return f"tcp:{device.host}:{device.tcp_port}"
    return f"rtu:{device.port}"


async def startup_discovery(
    devices: list[ModbusDevice],
    clients: dict[str, ModbusClient],
    timeout_s: float = 30.0,
) -> dict[str, bool]:
    """Probe all configured devices and report reachability.

    Groups devices by endpoint. Within each endpoint, devices are probed
    sequentially (only one device can use the bus at a time).
    Different endpoints are probed concurrently via asyncio.gather.

    Args:
        devices: List of ModbusDevice instances to probe.
        clients: Dict of endpoint key to connected Modbus client.
        timeout_s: Overall discovery timeout in seconds.

    Returns:
        Dict mapping device_id to reachability (True=online, False=offline).
        Never raises -- always returns a dict.
    """
    results: dict[str, bool] = {dev.device_id: False for dev in devices}

    # Group devices by endpoint, sorted by priority
    endpoint_devices: dict[str, list[ModbusDevice]] = defaultdict(list)
    for dev in devices:
        key = _endpoint_key(dev)
        endpoint_devices[key].append(dev)
    for key in endpoint_devices:
        endpoint_devices[key].sort(key=lambda d: d.priority)

    async def _probe_endpoint(endpoint: str, devs: list[ModbusDevice]) -> None:
        """Probe all devices on a single endpoint sequentially."""
        client = clients.get(endpoint)
        if client is None:
            logger.error("No client for %s -- skipping discovery", endpoint)
            return

        for dev in devs:
            probe_timeout: float = min(dev.timeout_s, 3.0)
            try:
                raw = await asyncio.wait_for(
                    dev.poll(client),
                    timeout=probe_timeout,
                )
                if raw is not None:
                    results[dev.device_id] = True
                    dev.health.online = True
                    logger.info(
                        "Discovery: %s on %s -- ONLINE", dev.device_id, endpoint
                    )
                else:
                    # Non-fatal exception response
                    results[dev.device_id] = False
                    _log_unreachable(dev, endpoint)
            except asyncio.TimeoutError:
                results[dev.device_id] = False
                _log_unreachable(dev, endpoint)
            except Exception:
                results[dev.device_id] = False
                _log_unreachable(dev, endpoint)

    # Create one task per endpoint, run all in parallel
    tasks = [
        _probe_endpoint(ep, devs) for ep, devs in endpoint_devices.items()
    ]

    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.error("Discovery overall timeout after %.1fs", timeout_s)
        # Results for devices not yet probed remain False

    return results


def _log_unreachable(dev: ModbusDevice, endpoint: str) -> None:
    """Log unreachable device at appropriate severity."""
    if dev.device_type in MANDATORY_DEVICES:
        logger.error(
            "Discovery: %s on %s -- UNREACHABLE (mandatory device)",
            dev.device_id,
            endpoint,
        )
    else:
        logger.warning(
            "Discovery: %s on %s -- UNREACHABLE (optional device)",
            dev.device_id,
            endpoint,
        )
