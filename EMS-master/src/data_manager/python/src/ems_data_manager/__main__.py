"""EMS Data Manager Python service entry point.

Runs the telemetry publisher, health monitor, and snapshot manager
concurrently via asyncio.gather. Attaches to the C-owned RTDB shared
memory and publishes telemetry at 1Hz on ZMQ PUB.

Usage:
    python -m ems_data_manager [--snapshot-dir DIR] [--snapshot-interval S] [--stale-threshold MS]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import zmq.asyncio

from ems_common.rtdb import attach_rtdb, detach_rtdb

from .health import HealthMonitor
from .publisher import TelemetryPublisher
from .snapshot import SnapshotManager

logger = logging.getLogger("ems_data_manager")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="ems_data_manager",
        description="EMS Data Manager Python service -- telemetry publisher, health monitor, snapshots",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/snapshots"),
        help="Directory for RTDB snapshot files (default: data/snapshots/)",
    )
    parser.add_argument(
        "--snapshot-interval",
        type=int,
        default=60,
        help="Seconds between periodic snapshots (default: 60)",
    )
    parser.add_argument(
        "--stale-threshold",
        type=int,
        default=5000,
        help="Milliseconds before a section is considered stale (default: 5000)",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    """Main async entry point: attach RTDB, create services, run loops."""
    logger.info("Attaching to RTDB shared memory...")
    shm, rtdb = attach_rtdb()
    logger.info(
        "RTDB attached: clusters=%d, racks_per_cluster=%d",
        rtdb.cluster_count,
        rtdb.racks_per_cluster,
    )

    ctx = zmq.asyncio.Context()

    publisher = TelemetryPublisher(rtdb, ctx)
    health = HealthMonitor(rtdb, publisher.pub_socket, stale_threshold_ms=args.stale_threshold)
    snapshot = SnapshotManager(rtdb, args.snapshot_dir, interval_s=args.snapshot_interval)

    # Handle SIGTERM/SIGINT for graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    async def _run_until_stop(coro: asyncio.Future) -> None:  # type: ignore[type-arg]
        """Run a coroutine until stop_event is set."""
        task = asyncio.create_task(coro)
        stop_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            {task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    logger.info("Starting telemetry publisher, health monitor, and snapshot manager")
    await asyncio.gather(
        _run_until_stop(publisher.publish_loop()),
        _run_until_stop(health.monitor_loop()),
        _run_until_stop(snapshot.snapshot_loop()),
    )

    # Cleanup
    logger.info("Shutting down...")
    publisher.close()
    health.close()
    del publisher
    del health
    del snapshot
    del rtdb
    detach_rtdb(shm)
    ctx.term()
    logger.info("Data manager stopped cleanly")


def main() -> None:
    """Entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
