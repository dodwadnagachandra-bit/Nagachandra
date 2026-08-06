"""Entry point for the EMS Communication Manager (Modbus polling).

Loads all device configs (PCS, BTMS, Meter, DG, PV), attaches to RTDB,
creates ZMQ PUSH socket, and runs the CommOrchestrator with startup discovery.

Usage:
    uv run python -m ems_comm_manager [--config CONFIG_DIR] [--help]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import yaml
import zmq

from ems_comm_manager.generic_device import (
    GenericDevice,
    PRIORITY_BTMS,
    PRIORITY_DG,
    PRIORITY_METER,
    PRIORITY_PV,
)
from ems_comm_manager.orchestrator import CommOrchestrator
from ems_comm_manager.pcs_device import PcsDevice
from ems_common.ipc import SOCK_LOGGER

logger = logging.getLogger("ems_comm_manager")

# Device config file names and their RTDB section mappings
DEVICE_CONFIGS: list[dict[str, Any]] = [
    {
        "file": "btms_config.yaml",
        "device_id": "btms",
        "device_type": "btms",
        "rtdb_section_name": "btms",
        "priority": PRIORITY_BTMS,
        "default_poll_ms": 2000,
    },
    {
        "file": "meter_config.yaml",
        "device_id": "meter",
        "device_type": "meter",
        "rtdb_section_name": "meter",
        "priority": PRIORITY_METER,
        "default_poll_ms": 1000,
    },
    {
        "file": "dg_config.yaml",
        "device_id": "dg",
        "device_type": "dg",
        "rtdb_section_name": "dg",  # No RTDB struct yet -- log-only
        "priority": PRIORITY_DG,
        "default_poll_ms": 2000,
    },
    {
        "file": "pv_config.yaml",
        "device_id": "pv",
        "device_type": "pv",
        "rtdb_section_name": "pv",  # No RTDB struct yet -- log-only
        "priority": PRIORITY_PV,
        "default_poll_ms": 1000,
    },
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="ems_comm_manager",
        description="EMS Communication Manager -- Modbus RTU polling orchestrator",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config",
        help="Path to config directory (default: config)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


def _load_yaml_config(config_dir: str, filename: str) -> dict | None:
    """Load a YAML config file, returning None if not found.

    Args:
        config_dir: Path to config directory.
        filename: YAML file name.

    Returns:
        Parsed config dict, or None if file not found.
    """
    path = Path(config_dir) / filename
    if not path.exists():
        return None
    with path.open("r") as f:
        return yaml.safe_load(f)


def _create_generic_devices(
    config_dir: str,
    push_socket: object,
    rtdb: object,
) -> list[GenericDevice]:
    """Create GenericDevice instances for all configured non-PCS devices.

    Args:
        config_dir: Path to config directory.
        push_socket: ZMQ PUSH socket for event publishing.
        rtdb: Attached RTDB object (may be None).

    Returns:
        List of GenericDevice instances.
    """
    devices: list[GenericDevice] = []

    for dev_cfg in DEVICE_CONFIGS:
        cfg = _load_yaml_config(config_dir, dev_cfg["file"])
        if cfg is None:
            logger.warning(
                "Config %s not found -- skipping %s device",
                dev_cfg["file"],
                dev_cfg["device_id"],
            )
            continue

        conn = cfg.get("connection", {})
        timing = cfg.get("timing", {})

        # Register map path: config/{device_type}_register_map.yaml
        register_map_path: str = str(
            Path(config_dir) / f"{dev_cfg['device_type']}_register_map.yaml"
        )
        if not Path(register_map_path).exists():
            logger.warning(
                "Register map %s not found -- skipping %s device",
                register_map_path,
                dev_cfg["device_id"],
            )
            continue

        protocol = conn.get("protocol", "rtu")
        device = GenericDevice(
            device_id=dev_cfg["device_id"],
            device_type=dev_cfg["device_type"],
            slave_id=conn.get("slave_id", 1),
            port=conn.get("device", "/dev/ttyUSB0"),
            register_map_path=register_map_path,
            poll_interval_ms=timing.get(
                "poll_interval_ms", dev_cfg["default_poll_ms"]
            ),
            timeout_s=timing.get("timeout_ms", 3000) / 1000.0,
            priority=dev_cfg["priority"],
            push_socket=push_socket,
            rtdb=rtdb,
            rtdb_section_name=dev_cfg["rtdb_section_name"],
            protocol=protocol,
            host=conn.get("host", "127.0.0.1"),
            tcp_port=conn.get("port", 502),
        )
        addr = f"{conn.get('host')}:{conn.get('port')}" if protocol == "tcp" else conn.get("device", "/dev/ttyUSB0")
        logger.info(
            "Created %s device: slave=%d %s=%s poll=%dms",
            dev_cfg["device_id"],
            conn.get("slave_id", 1),
            protocol,
            addr,
            timing.get("poll_interval_ms", dev_cfg["default_poll_ms"]),
        )
        devices.append(device)

    return devices


def main(argv: list[str] | None = None) -> int:
    """Main entry point for comm_manager.

    Args:
        argv: Command-line arguments (None = sys.argv).

    Returns:
        Exit code (0 = success).
    """
    args = _parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # COMM-08: GPIO monitoring is handled by safety_manager (SAFE-08)
    logger.info(
        "GPIO monitoring handled by safety_manager (SAFE-08) -- "
        "no GPIO code in comm_manager"
    )

    # Load PCS config
    pcs_config = _load_yaml_config(args.config, "pcs_config.yaml")
    if pcs_config is None:
        logger.error("PCS config not found in %s", args.config)
        return 1

    conn = pcs_config.get("connection", {})
    timing = pcs_config.get("timing", {})

    # Attach to RTDB (optional -- may not be available in dev/test)
    rtdb = None
    shm = None
    try:
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        shm, rtdb = attach_rtdb()
        logger.info("Attached to RTDB")
    except Exception:
        logger.warning("RTDB not available -- running without RTDB writes")

    # Create ZMQ context and PUSH socket
    zmq_ctx = zmq.Context()
    push_socket = zmq_ctx.socket(zmq.PUSH)
    push_socket.setsockopt(zmq.LINGER, 0)
    try:
        push_socket.connect(SOCK_LOGGER)
    except Exception:
        logger.warning("Could not connect to logger socket -- events will be dropped")

    # Create PCS device
    register_map_path: str = pcs_config.get(
        "register_map_path", "config/pcs_register_map.yaml"
    )
    pcs_protocol = conn.get("protocol", "rtu")
    pcs = PcsDevice(
        device_id="pcs",
        slave_id=conn.get("slave_id", 1),
        port=conn.get("device", "/dev/ttyUSB0"),
        register_map_path=register_map_path,
        poll_interval_ms=timing.get("poll_interval_ms", 500),
        timeout_s=timing.get("timeout_ms", 3000) / 1000.0,
        push_socket=push_socket,
        rtdb=rtdb,
        protocol=pcs_protocol,
        host=conn.get("host", "127.0.0.1"),
        tcp_port=conn.get("port", 502),
    )

    # Create generic devices (BTMS, Meter, DG, PV)
    generic_devices: list[GenericDevice] = _create_generic_devices(
        args.config, push_socket, rtdb
    )

    # All devices for the orchestrator
    all_devices = [pcs, *generic_devices]

    # Create orchestrator
    orchestrator = CommOrchestrator(
        devices=all_devices,
        push_socket=push_socket,
        baudrate=conn.get("baud_rate", 9600),
    )

    # Signal handling
    def _signal_handler(sig: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", sig)
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.ensure_future(orchestrator.shutdown())
        )

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Run orchestrator with discovery
    device_summary: str = ", ".join(d.device_id for d in all_devices)
    logger.info(
        "Starting comm_manager with %d devices: %s",
        len(all_devices),
        device_summary,
    )

    try:
        asyncio.run(orchestrator.run_with_discovery())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        push_socket.close()
        zmq_ctx.term()
        if shm is not None:
            from ems_common.rtdb import detach_rtdb

            detach_rtdb(shm)
            logger.info("Detached from RTDB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
