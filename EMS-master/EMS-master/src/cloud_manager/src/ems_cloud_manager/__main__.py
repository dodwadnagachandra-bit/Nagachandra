"""cloud_manager entry point -- MQTT/TLS cloud bridge for EMS.

Usage:
    python -m ems_cloud_manager [--config PATH] [--log-level LEVEL]
    uv run python -m ems_cloud_manager --config config/cloud_config.yaml

Environment variables (override ZMQ defaults for integration testing):
    EMS_TELEMETRY_SUB_ENDPOINT   -- SUB connect address (default: ipc:///run/ems/telemetry.sock)
    EMS_ALARM_SUB_ENDPOINT       -- SUB connect address (default: ipc:///run/ems/alarm_pub.sock)
    EMS_LOGGER_PUSH_ENDPOINT     -- PUSH connect address (default: ipc:///run/ems/logger.sock)
    EMS_CONTROL_CMD_ENDPOINT     -- REQ connect address (default: ipc:///run/ems/control_cmd.sock)
    EMS_ALARM_CMD_ENDPOINT       -- REQ connect address (default: ipc:///run/ems/alarm_cmd.sock)
    EMS_CLOUD_PUB_ENDPOINT       -- ZMQ PUB bind address (default: ipc:///run/ems/cloud_pub.sock)
    EMS_CLOUD_BUFFER_DIR         -- Root dir for offline JSONL buffer files
                                    (default: data/cloud_buffer; only used when
                                    offline_buffer.enabled is true in config)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any

from ems_cloud_manager.buffer import BufferManager
from ems_cloud_manager.buffered_loop import BufferedCloudLoop
from ems_cloud_manager.config import load_cloud_config
from ems_cloud_manager.dispatcher import CommandDispatcher
from ems_cloud_manager.loop import CloudLoop
from ems_cloud_manager.publisher import MqttPublisher

logger: logging.Logger = logging.getLogger("ems_cloud_manager")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed namespace with .config and .log_level.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="ems_cloud_manager",
        description="EMS Cloud Manager -- MQTT/TLS cloud bridge for BESS data and commands",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/cloud_config.yaml"),
        help="Path to cloud_config.yaml (default: config/cloud_config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    """Async entry point: load config, wire components, run until signal.

    Args:
        args: Parsed command-line arguments from parse_args().
    """
    logger.info("Loading cloud config from %s", args.config)
    config: dict = load_cloud_config(args.config)

    # Read ZMQ endpoint overrides from env vars (for test isolation and
    # production deployments that need to override defaults).
    telemetry_sub: str | None = os.environ.get("EMS_TELEMETRY_SUB_ENDPOINT")
    alarm_sub: str | None = os.environ.get("EMS_ALARM_SUB_ENDPOINT")
    logger_push: str | None = os.environ.get("EMS_LOGGER_PUSH_ENDPOINT")
    control_cmd: str | None = os.environ.get("EMS_CONTROL_CMD_ENDPOINT")
    alarm_cmd: str | None = os.environ.get("EMS_ALARM_CMD_ENDPOINT")
    cloud_pub: str | None = os.environ.get("EMS_CLOUD_PUB_ENDPOINT")

    # Build components
    publisher: MqttPublisher = MqttPublisher(config, cloud_pub_endpoint=cloud_pub)
    dispatcher: CommandDispatcher = CommandDispatcher(
        publisher,
        control_cmd_endpoint=control_cmd,
        alarm_cmd_endpoint=alarm_cmd,
    )

    # Conditionally wire offline buffer (CLOUD-04/CLOUD-05)
    buffer_cfg: dict[str, Any] = config.get("offline_buffer", {})
    loop_obj: CloudLoop
    if buffer_cfg.get("enabled", False):
        buffer_dir: Path = Path(
            os.environ.get("EMS_CLOUD_BUFFER_DIR", "data/cloud_buffer")
        )
        buffer_mgr: BufferManager = BufferManager(
            buffer_dir=buffer_dir,
            max_hours=buffer_cfg["max_hours"],
            max_mb=buffer_cfg["max_mb"],
        )
        loop_obj = BufferedCloudLoop(
            config,
            publisher,
            buffer_mgr,
            dispatcher=dispatcher,
            telemetry_sub_endpoint=telemetry_sub,
            alarm_sub_endpoint=alarm_sub,
            logger_push_endpoint=logger_push,
        )
        logger.info(
            "Offline buffer enabled: dir=%s max_hours=%d max_mb=%d",
            buffer_dir,
            buffer_cfg["max_hours"],
            buffer_cfg["max_mb"],
        )
    else:
        loop_obj = CloudLoop(
            config,
            publisher,
            dispatcher=dispatcher,
            telemetry_sub_endpoint=telemetry_sub,
            alarm_sub_endpoint=alarm_sub,
            logger_push_endpoint=logger_push,
        )

    # Install signal handlers for graceful shutdown
    asyncio_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        loop_obj.stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, _signal_handler)

    logger.info("Cloud manager starting")
    try:
        await loop_obj.run()
    finally:
        dispatcher.cleanup()
        loop_obj.cleanup()
        logger.info("Cloud manager stopped")


def main() -> None:
    """Entry point for command-line invocation."""
    args: argparse.Namespace = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
