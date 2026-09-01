"""CLI entry point: uv run python -m tools.simulators.can_sim"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from tools.simulators.can_sim.simulator import CANSimulator


def parse_args() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="CAN Bus BMU Simulator — generates realistic BMU Layer 2 traffic on vcan",
    )
    parser.add_argument("--interface", default="vcan0", help="CAN interface name (default: vcan0)")
    parser.add_argument(
        "--config",
        default="config/profiles/residential/bms_config.yaml",
        help="Path to bms_config.yaml",
    )
    parser.add_argument(
        "--system-config",
        default=None,
        help="Path to system_config.yaml for topology (optional)",
    )
    parser.add_argument("--racks", type=int, default=None, help="Override rack count")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    return parser.parse_args()


def main() -> None:
    args: argparse.Namespace = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    sim: CANSimulator = CANSimulator(
        interface=args.interface,
        config_path=args.config,
        system_config_path=args.system_config,
        num_racks=args.racks,
        verbose=args.verbose,
    )

    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, sim.stop)

    try:
        loop.run_until_complete(sim.run())
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
        print("CAN Simulator stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
