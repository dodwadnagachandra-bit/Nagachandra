"""alarm_manager entry point — 1Hz IEC 62682 alarm evaluation engine.

Usage:
    python -m ems_alarm_manager [--config PATH]
    uv run python -m ems_alarm_manager --config config/alarms_config.yaml

Environment variables (override ZMQ defaults for integration testing):
    EMS_ALARM_CMD_ENDPOINT    — REP socket bind address (default: ipc:///run/ems/alarm_cmd.sock)
    EMS_ALARM_PUSH_ENDPOINT   — PUSH socket connect address (default: ipc:///run/ems/logger.sock)
    EMS_ALARM_PUB_ENDPOINT    — PUB socket bind address (default: ipc:///run/ems/alarm_pub.sock)
    EMS_CONFIG_SUB_ENDPOINT   — config reload SUB connect address (default: ipc:///run/ems/config_pub.sock)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from ems_alarm_manager.config import load_alarm_config
from ems_alarm_manager.loop import AlarmLoop

logger: logging.Logger = logging.getLogger("ems_alarm_manager")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed namespace with .config and .log_level.
    """
    parser = argparse.ArgumentParser(
        prog="ems_alarm_manager",
        description="EMS Alarm Manager — 1Hz IEC 62682 alarm evaluation engine",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/alarms_config.yaml"),
        help="Path to alarms_config.yaml (default: config/alarms_config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    """Async entry point: load config, create loop, run until signal.

    Args:
        args: Parsed command-line arguments.
    """
    logger.info("Loading alarm config from %s", args.config)
    config = load_alarm_config(args.config)

    # Read ZMQ endpoint overrides from env vars (for integration test isolation).
    # When not set, AlarmLoop falls back to its default ipc:// endpoints.
    rep_endpoint: str | None = os.environ.get("EMS_ALARM_CMD_ENDPOINT")
    push_endpoint: str | None = os.environ.get("EMS_ALARM_PUSH_ENDPOINT")
    pub_endpoint: str | None = os.environ.get("EMS_ALARM_PUB_ENDPOINT")
    config_sub_endpoint: str | None = os.environ.get("EMS_CONFIG_SUB_ENDPOINT")

    loop_obj = AlarmLoop(
        config,
        rep_endpoint=rep_endpoint,
        push_endpoint=push_endpoint,
        pub_endpoint=pub_endpoint,
        config_sub_endpoint=config_sub_endpoint,
        config_path=args.config,
    )
    asyncio_loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        loop_obj.stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, _signal_handler)

    logger.info("Alarm manager starting")
    try:
        await loop_obj.run()
    finally:
        loop_obj.cleanup()
        logger.info("Alarm manager stopped")


def main() -> None:
    """Entry point for command-line invocation."""
    args = parse_args()

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
