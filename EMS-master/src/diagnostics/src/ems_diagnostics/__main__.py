"""ems_diagnostics entry point — EMS Diagnostics Service.

Usage:
    python -m ems_diagnostics [--config PATH] [--log-level LEVEL]
    uv run python -m ems_diagnostics --config config/diagnostics_config.yaml

Starts the DiagnosticsLoop which runs 5 concurrent async tasks:
  - Telemetry collector (SUB from data_manager at 1Hz)
  - Hourly trend updater (REQ to logger for historical queries)
  - Diagnostics publisher (PUB current state at publish_s interval)
  - Report server (REP for get_current/get_report/get_predictions queries)
  - Predictive alert checker (PUSH events to logger on SOH threshold alerts)

Signal handling: SIGTERM and SIGINT trigger graceful shutdown.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from ems_diagnostics.config import load_diagnostics_config, DiagnosticsConfig
from ems_diagnostics.loop import DiagnosticsLoop

logger: logging.Logger = logging.getLogger("ems_diagnostics")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed namespace with .config and .log_level.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="ems_diagnostics",
        description="EMS Diagnostics Service — SOH, PCS efficiency, thermal, and comm health",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/diagnostics_config.yaml"),
        help="Path to diagnostics_config.yaml (default: config/diagnostics_config.yaml)",
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
        args: Parsed command-line arguments from parse_args().
    """
    config_path: str = str(args.config)
    logger.info("Loading diagnostics config from %s", config_path)

    config: DiagnosticsConfig = load_diagnostics_config(config_path)
    logger.info(
        "Config loaded: publish_s=%d trend_update_s=%d",
        config.intervals.publish_s,
        config.intervals.trend_update_s,
    )

    loop_obj: DiagnosticsLoop = DiagnosticsLoop(config)

    # Install signal handlers for graceful shutdown
    asyncio_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received — stopping DiagnosticsLoop")
        loop_obj.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, _signal_handler)

    logger.info("DiagnosticsLoop starting")
    try:
        await loop_obj.run()
    finally:
        loop_obj.cleanup()
        logger.info("DiagnosticsLoop stopped")


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
