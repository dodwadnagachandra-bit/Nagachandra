"""config_manager service entry point.

Runs the ConfigManager as an asyncio service:
  - Loads all configs at startup (fail-fast on error)
  - Starts inotify watcher for hot-reloadable configs
  - Starts ZMQ REQ/REP server for config queries
  - Handles SIGTERM/SIGINT for graceful shutdown

Usage:
    python -m ems_config_manager [--config-dir PATH] [--schema-dir PATH] [--profile NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import zmq.asyncio

from ems_config_manager.manager import ConfigManager
from ems_config_manager.watcher import ConfigWatcher

logger: logging.Logger = logging.getLogger("ems_config_manager")


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argument namespace.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="config_manager",
        description="EMS Config Manager service",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config/"),
        help="Path to config directory (default: config/)",
    )
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=Path("config/schemas/"),
        help="Path to schema directory (default: config/schemas/)",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=os.environ.get("EMS_PROFILE"),
        help="Deployment profile (or set EMS_PROFILE env var)",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    """Run the config_manager asyncio service.

    Args:
        args: Parsed command-line arguments.
    """
    # Create and load ConfigManager (fail-fast on error)
    cm: ConfigManager = ConfigManager(
        config_dir=args.config_dir,
        schema_dir=args.schema_dir,
        profile=args.profile,
    )
    cm.load_all()
    logger.info("All configs loaded successfully")

    # Create ZMQ context
    ctx: zmq.asyncio.Context = zmq.asyncio.Context()

    # Initialize push socket for events and pub socket for config_reload broadcast
    cm._init_push_socket(ctx)
    cm._init_pub_socket(ctx)

    # Create watcher with on_change callback
    watcher: ConfigWatcher = ConfigWatcher(
        config_dir=args.config_dir,
        on_change=cm.handle_reload,
    )

    # Set up graceful shutdown
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    stop_event: asyncio.Event = asyncio.Event()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    # Run services concurrently
    tasks: list[asyncio.Task] = [
        asyncio.create_task(cm.serve_queries(ctx), name="zmq-server"),
        asyncio.create_task(watcher.watch_loop(), name="inotify-watcher"),
    ]

    logger.info("config_manager service started")

    # Wait for shutdown signal
    await stop_event.wait()

    # Cancel all tasks
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    # Cleanup
    cm._close_push_socket()
    cm._close_pub_socket()
    ctx.term()
    logger.info("config_manager service stopped")


def main() -> None:
    """Entry point for the config_manager service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args: argparse.Namespace = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
