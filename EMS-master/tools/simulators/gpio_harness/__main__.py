"""CLI entry point: uv run python -m tools.simulators.gpio_harness"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

import yaml

from tools.simulators.gpio_harness.backend import detect_backend, GpioBackend
from tools.simulators.gpio_harness.config import GpioConfig

log: logging.Logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="GPIO Test Harness -- inject DI signals and read DO state for safety_manager testing",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "rtdb", "gpio-sim"],
        default="auto",
        help="Backend mode (default: auto)",
    )
    parser.add_argument(
        "--config",
        default="config/gpio_config.yaml",
        help="Path to gpio_config.yaml",
    )
    parser.add_argument(
        "--shm-name",
        default="ems_rtdb",
        help="POSIX shared memory segment name (default: ems_rtdb)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # --- set subcommand ---
    set_parser = subparsers.add_parser("set", help="Set DI pin value(s)")
    set_parser.add_argument(
        "pins",
        nargs="+",
        help="PIN VALUE (single) or PIN=VALUE PIN=VALUE (multi-pin atomic). "
        "VALUE: high/1 or low/0. PIN: DI-N, pin number, or name (ESTOP_NO).",
    )
    set_parser.add_argument(
        "--logical",
        action="store_true",
        help="Apply active_low polarity from config (logical value, not raw)",
    )

    # --- get subcommand ---
    get_parser = subparsers.add_parser("get", help="Get pin value(s)")
    get_parser.add_argument(
        "pin",
        help="Pin to read: DI-N, DO-N, pin name, or 'all'",
    )
    get_parser.add_argument(
        "--logical",
        action="store_true",
        help="Apply active_low polarity from config",
    )
    get_parser.add_argument(
        "--raw",
        action="store_true",
        help="Print bare value only (no formatting)",
    )

    # --- daemon subcommand ---
    subparsers.add_parser(
        "daemon",
        help="Run gpio-sim daemon (keeps virtual chip alive until SIGINT)",
    )

    return parser.parse_args()


def _parse_value(val_str: str) -> int:
    """Parse a pin value string to int."""
    val_lower: str = val_str.lower()
    if val_lower in ("high", "1"):
        return 1
    if val_lower in ("low", "0"):
        return 0
    raise ValueError(f"Invalid value: {val_str!r} (expected high/1 or low/0)")


def cmd_set(
    args: argparse.Namespace,
    backend: GpioBackend,
    config: GpioConfig,
) -> None:
    """Execute the 'set' subcommand."""
    pins_args: list[str] = args.pins

    # Detect multi-pin format (PIN=VALUE)
    if any("=" in a for a in pins_args):
        values: dict[int, int] = {}
        for arg in pins_args:
            if "=" not in arg:
                print(f"Error: mixed format -- use PIN=VALUE for all args", file=sys.stderr)
                sys.exit(1)
            pin_str, val_str = arg.split("=", 1)
            pin_num: int = config.resolve_di(pin_str)
            raw_val: int = _parse_value(val_str)
            if args.logical:
                raw_val = config.apply_active_low_di(pin_num, logical=bool(raw_val))
            values[pin_num] = raw_val
        backend.set_di_multi(values)
        for p, v in values.items():
            name: str = config.get_di_name(p)
            print(f"DI-{p} ({name}): {v}")
    else:
        # Single pin: PIN VALUE
        if len(pins_args) != 2:
            print(
                "Error: expected PIN VALUE (2 args) or PIN=VALUE format",
                file=sys.stderr,
            )
            sys.exit(1)
        pin_str = pins_args[0]
        val_str = pins_args[1]
        pin_num = config.resolve_di(pin_str)
        raw_val = _parse_value(val_str)
        if args.logical:
            raw_val = config.apply_active_low_di(pin_num, logical=bool(raw_val))
        backend.set_di(pin_num, raw_val)
        name = config.get_di_name(pin_num)
        print(f"DI-{pin_num} ({name}): {raw_val}")


def cmd_get(
    args: argparse.Namespace,
    backend: GpioBackend,
    config: GpioConfig,
) -> None:
    """Execute the 'get' subcommand."""
    pin_arg: str = args.pin

    if pin_arg.lower() == "all":
        # Print all DI and DO pins
        for i in range(8):
            val: int = backend.get_di(i)
            name: str = config.get_di_name(i)
            if args.raw:
                print(val)
            else:
                print(f"DI-{i} ({name:18s}): {val}")
        for i in range(8):
            val = backend.get_do(i)
            name = config.get_do_name(i)
            if args.raw:
                print(val)
            else:
                status: str = config.apply_active_low_do(i, val)
                print(f"DO-{i} ({name:18s}): {val} [{status}]")
        return

    # Single pin -- determine DI or DO
    upper: str = pin_arg.upper()
    is_do: bool = upper.startswith("DO-") or upper in {
        n for n in config._do_names
    }

    if is_do:
        pin_num: int = config.resolve_do(pin_arg)
        val = backend.get_do(pin_num)
        name = config.get_do_name(pin_num)
        if args.raw:
            print(val)
        else:
            status = config.apply_active_low_do(pin_num, val)
            print(f"DO-{pin_num} ({name}): {val} [{status}]")
    else:
        pin_num = config.resolve_di(pin_arg)
        val = backend.get_di(pin_num)
        name = config.get_di_name(pin_num)
        if args.raw:
            print(val)
        else:
            print(f"DI-{pin_num} ({name}): {val}")


def cmd_daemon(args: argparse.Namespace) -> None:
    """Execute the 'daemon' subcommand (gpio-sim only)."""
    if args.backend not in ("gpio-sim",):
        print("Error: daemon mode requires --backend gpio-sim", file=sys.stderr)
        sys.exit(1)

    backend: GpioBackend = detect_backend("gpio-sim")
    print("gpio-sim daemon running. Press Ctrl-C to stop.")

    def shutdown(signum: int, frame: object) -> None:
        print("\nShutting down gpio-sim...", file=sys.stderr)
        backend.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    signal.pause()


def main() -> None:
    """Main entry point."""
    args: argparse.Namespace = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.command is None:
        parse_args.__wrapped__ if hasattr(parse_args, "__wrapped__") else None
        print("Error: no command specified. Use --help for usage.", file=sys.stderr)
        sys.exit(1)

    if args.command == "daemon":
        cmd_daemon(args)
        return

    config: GpioConfig = GpioConfig(config_path=Path(args.config))

    # Extract fault_injection from gpio config YAML if present
    fault_cfg: dict | None = None
    config_path: Path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            gpio_yaml: dict = yaml.safe_load(f) or {}
        fault_cfg = gpio_yaml.get("fault_injection") or None
        if fault_cfg:
            log.info(
                "FAULT: %s",
                ", ".join(f"{k}={v}" for k, v in fault_cfg.items()),
            )

    backend: GpioBackend = detect_backend(args.backend, shm_name=args.shm_name, fault_cfg=fault_cfg)

    try:
        if args.command == "set":
            cmd_set(args, backend, config)
        elif args.command == "get":
            cmd_get(args, backend, config)
    finally:
        backend.close()


if __name__ == "__main__":
    main()
