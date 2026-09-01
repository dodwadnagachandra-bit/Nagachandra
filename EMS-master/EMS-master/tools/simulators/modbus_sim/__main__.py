"""CLI entry point: uv run python -m tools.simulators.modbus_sim"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from tools.simulators.modbus_sim.simulator import ModbusSimulator


def parse_args() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Modbus PCS Simulator -- generates realistic PCS telemetry via Modbus TCP/RTU",
    )
    parser.add_argument(
        "--transport",
        choices=["tcp", "rtu"],
        default="rtu",
        help="Modbus transport mode (default: rtu)",
    )
    parser.add_argument(
        "--config",
        default="config/pcs_config.yaml",
        help="Path to pcs_config.yaml",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=5020,
        help="TCP port for Modbus TCP mode (default: 5020)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    return parser.parse_args()


def main() -> None:
    args: argparse.Namespace = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    sim: ModbusSimulator = ModbusSimulator(
        config_path=args.config,
        transport=args.transport,
        tcp_port=args.tcp_port,
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
        print("Modbus PCS Simulator stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
