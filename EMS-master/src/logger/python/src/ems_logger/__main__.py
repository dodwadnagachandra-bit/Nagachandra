"""EMS Logger Python service entry point.

Runs the telemetry writer, event consumer, query server, and retention
cleanup manager concurrently via asyncio. Subscribes to ZMQ telemetry,
writes Parquet + JSONL, serves DuckDB queries, and manages disk retention.

Usage:
    python -m ems_logger [--config PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml
import zmq.asyncio

from ems_logger.cleanup import RetentionManager
from ems_logger.config import LoggerConfig, load_logger_config
from ems_logger.event_writer import EventConsumer
from ems_logger.query_handler import QueryServer
from ems_logger.telemetry_writer import TelemetryWriter

logger: logging.Logger = logging.getLogger("ems_logger")

# Default topology values (residential profile)
_DEFAULT_TOPOLOGY: dict[str, int] = {
    "cluster_count": 1,
    "racks_per_cluster": 1,
    "modules_per_rack": 8,
    "cells_per_module": 16,
    "temps_per_module": 4,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed namespace with config path.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="ems_logger",
        description="EMS Logger service -- Parquet telemetry, JSONL events, DuckDB queries",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/logger_config.yaml"),
        help="Path to logger_config.yaml (default: config/logger_config.yaml)",
    )
    return parser.parse_args(argv)


def _load_topology(config_dir: Path) -> dict[str, int]:
    """Load topology from system_config.yaml, falling back to defaults.

    Args:
        config_dir: Directory containing system_config.yaml.

    Returns:
        Dict with cluster_count, racks_per_cluster, modules_per_rack,
        cells_per_module, temps_per_module.
    """
    system_config_path: Path = config_dir / "system_config.yaml"

    if not system_config_path.exists():
        logger.warning(
            "system_config.yaml not found at %s, using default topology",
            system_config_path,
        )
        return dict(_DEFAULT_TOPOLOGY)

    try:
        with open(system_config_path) as f:
            raw: dict = yaml.safe_load(f) or {}

        topology_raw: dict = raw.get("topology", {})
        topology: dict[str, int] = {
            "cluster_count": int(topology_raw.get("cluster_count", _DEFAULT_TOPOLOGY["cluster_count"])),
            "racks_per_cluster": int(topology_raw.get("racks_per_cluster", _DEFAULT_TOPOLOGY["racks_per_cluster"])),
            "modules_per_rack": int(topology_raw.get("modules_per_rack", _DEFAULT_TOPOLOGY["modules_per_rack"])),
            "cells_per_module": int(topology_raw.get("cells_per_module", _DEFAULT_TOPOLOGY["cells_per_module"])),
            "temps_per_module": int(topology_raw.get("temps_per_module", _DEFAULT_TOPOLOGY["temps_per_module"])),
        }
        logger.info("Loaded topology from %s: %s", system_config_path, topology)
        return topology

    except Exception:
        logger.warning(
            "Failed to parse system_config.yaml, using default topology",
            exc_info=True,
        )
        return dict(_DEFAULT_TOPOLOGY)


async def run(args: argparse.Namespace) -> None:
    """Main async entry point: create components, run concurrently, handle shutdown.

    Args:
        args: Parsed CLI arguments.
    """
    # Load configuration
    config_path: Path = args.config
    try:
        config: LoggerConfig = load_logger_config(config_path)
        logger.info("Loaded logger config from %s", config_path)
    except FileNotFoundError:
        logger.warning(
            "Config file %s not found, using defaults", config_path
        )
        config = LoggerConfig()

    # Load topology for Parquet schema generation
    config_dir: Path = config_path.parent
    topology: dict[str, int] = _load_topology(config_dir)

    # Create shared ZMQ context
    zmq_ctx: zmq.asyncio.Context = zmq.asyncio.Context()

    # Run crash recovery before starting writers
    retention: RetentionManager = RetentionManager(config)
    retention.startup_recovery()
    logger.info("Startup recovery complete")

    # Create components
    telemetry_writer: TelemetryWriter = TelemetryWriter(
        config=config,
        zmq_ctx=zmq_ctx,
        topology=topology,
    )
    event_consumer: EventConsumer = EventConsumer(
        config=config,
        zmq_ctx=zmq_ctx,
    )
    query_server: QueryServer = QueryServer(
        config=config,
        zmq_ctx=zmq_ctx,
    )

    # Register signal handlers for graceful shutdown
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    stop_event: asyncio.Event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    # Create async tasks
    tasks: list[asyncio.Task] = [
        asyncio.create_task(telemetry_writer.run(), name="telemetry_writer"),
        asyncio.create_task(event_consumer.run(), name="event_consumer"),
        asyncio.create_task(query_server.run(), name="query_server"),
        asyncio.create_task(retention.run_periodic(), name="retention_cleanup"),
    ]

    logger.info(
        "Logger started: %d tasks (telemetry_writer, event_consumer, query_server, retention_cleanup)",
        len(tasks),
    )

    # Wait for shutdown signal
    await stop_event.wait()
    logger.info("Shutting down %d tasks...", len(tasks))

    # Cancel all tasks
    for task in tasks:
        task.cancel()

    # Wait for tasks to finish
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            logger.error("Task %s failed: %s", task.get_name(), result)

    # Close components (flush files, close sockets)
    telemetry_writer.close()
    event_consumer.close()
    query_server.close()

    # Terminate ZMQ context
    zmq_ctx.term()
    logger.info("Logger stopped cleanly")


def main() -> None:
    """Entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    args: argparse.Namespace = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
