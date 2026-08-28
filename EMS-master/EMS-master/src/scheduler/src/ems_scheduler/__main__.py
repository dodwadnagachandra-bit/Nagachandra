"""ems_scheduler entry point -- 1Hz schedule evaluation and command dispatch.

Usage:
    python -m ems_scheduler [--config PATH]
    uv run python -m ems_scheduler --config config/schedule_config.yaml

Environment variables (override ZMQ defaults for integration testing):
    EMS_CONTROL_CMD_ENDPOINT   -- REQ socket connect address
    EMS_CONFIG_SUB_ENDPOINT    -- config reload SUB connect address
    EMS_SCHEDULER_PUB_ENDPOINT -- PUB socket bind address
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from ems_scheduler.config import load_schedule_config
from ems_scheduler.loop import SchedulerLoop

logger: logging.Logger = logging.getLogger("ems_scheduler")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed namespace with .config and .log_level.
    """
    parser = argparse.ArgumentParser(
        prog="ems_scheduler",
        description="EMS Scheduler -- 1Hz schedule evaluation and command dispatch",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/schedule_config.yaml"),
        help="Path to schedule_config.yaml (default: config/schedule_config.yaml)",
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
    logger.info("Loading schedule config from %s", args.config)
    config: dict = load_schedule_config(args.config)

    # Read ZMQ endpoint overrides from env vars (for integration test isolation).
    req_endpoint: str | None = os.environ.get("EMS_CONTROL_CMD_ENDPOINT")
    config_sub_endpoint: str | None = os.environ.get("EMS_CONFIG_SUB_ENDPOINT")
    pub_endpoint: str | None = os.environ.get("EMS_SCHEDULER_PUB_ENDPOINT")

    loop_obj = SchedulerLoop(
        config,
        config_path=args.config,
        req_endpoint=req_endpoint,
        config_sub_endpoint=config_sub_endpoint,
        pub_endpoint=pub_endpoint,
    )
    asyncio_loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        loop_obj.stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, _signal_handler)

    logger.info("Scheduler starting")
    try:
        await loop_obj.run()
    finally:
        loop_obj.cleanup()
        logger.info("Scheduler stopped")


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
