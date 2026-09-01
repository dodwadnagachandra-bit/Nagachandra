"""EMS Developer TUI — launch with: uv run python -m tools.tui [--profile NAME]"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EMS Developer TUI — process manager for simulator and services",
    )
    parser.add_argument(
        "--profile",
        default="residential",
        choices=["residential", "commercial", "container"],
        help="Deployment profile (default: residential)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to processes.yaml (default: tools/tui/processes.yaml)",
    )
    args = parser.parse_args()

    from pathlib import Path

    from tools.tui.app import EmsDevTui

    config_path = Path(args.config) if args.config else None
    app = EmsDevTui(profile=args.profile, config_path=config_path)
    app.run()


if __name__ == "__main__":
    main()
