"""EMS Control Manager entry point.

Starts the 1Hz control loop, wires signal handling, and runs until SIGTERM/SIGINT.

Usage:
    python -m ems_control_manager [--config PATH]
    uv run python -m ems_control_manager --config config/control_config.yaml

Environment variables (override ZMQ defaults for integration testing):
    EMS_CONTROL_CMD_ENDPOINT  — REP socket bind address (default: ipc:///run/ems/control_cmd.sock)
    EMS_CONTROL_PUB_ENDPOINT  — PUB socket bind address (default: ipc:///run/ems/control_pub.sock)
    EMS_CONTROL_PUSH_ENDPOINT — PUSH socket connect address (default: ipc:///run/ems/logger.sock)
    EMS_ALARM_SUB_ENDPOINT    — alarm SUB connect address (default: ipc:///run/ems/alarm_pub.sock)
    EMS_CONFIG_SUB_ENDPOINT   — config reload SUB connect address (default: ipc:///run/ems/config_pub.sock)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from ems_control_manager.config import load_control_config
from ems_control_manager.loop import ControlLoop

logger: logging.Logger = logging.getLogger("ems_control_manager")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (None = sys.argv).

    Returns:
        Parsed namespace with .config and .log_level.
    """
    parser = argparse.ArgumentParser(
        prog="ems_control_manager",
        description="EMS Control Manager — 1Hz state machine, RTDB I/O, ZMQ command API",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/control_config.yaml"),
        help="Path to control_config.yaml (default: config/control_config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    """Async entry point: load config, create loop, run until signal.

    Args:
        args: Parsed command-line arguments.
    """
    logger.info("Loading config from %s", args.config)
    config = load_control_config(args.config)

    # Read ZMQ endpoint overrides from env vars (for integration test isolation).
    # When not set, ControlLoop falls back to its default ipc:// endpoints.
    rep_endpoint: str | None = os.environ.get("EMS_CONTROL_CMD_ENDPOINT")
    pub_endpoint: str | None = os.environ.get("EMS_CONTROL_PUB_ENDPOINT")
    push_endpoint: str | None = os.environ.get("EMS_CONTROL_PUSH_ENDPOINT")
    alarm_sub_endpoint: str | None = os.environ.get("EMS_ALARM_SUB_ENDPOINT")
    config_sub_endpoint: str | None = os.environ.get("EMS_CONFIG_SUB_ENDPOINT")

    loop_obj = ControlLoop(
        config,
        rep_endpoint=rep_endpoint,
        pub_endpoint=pub_endpoint,
        push_endpoint=push_endpoint,
        alarm_sub_endpoint=alarm_sub_endpoint,
        config_sub_endpoint=config_sub_endpoint,
        config_path=args.config,
    )

    asyncio_loop = asyncio.get_running_loop()
    stop_event = loop_obj.stop_event

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, _signal_handler)

    logger.info("Control manager starting")
    try:
        await loop_obj.run()
    finally:
        loop_obj.cleanup()
        logger.info("Control manager stopped")


def main(argv: list[str] | None = None) -> int:
    """Entry point for command-line invocation.

    Args:
        argv: Argument list (None = sys.argv).

    Returns:
        Exit code (0 = clean exit).
    """
    args = _parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")

    return 0


if __name__ == "__main__":
    sys.exit(main())
