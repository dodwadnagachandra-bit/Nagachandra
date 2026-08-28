"""Entry point for the EMS HMI server.

Usage:
    python -m ems_hmi_server [--config path] [--log-level level]
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from ems_hmi_server.app import create_app
from ems_hmi_server.config import DEFAULT_CONFIG_PATH, load_hmi_config

logger: logging.Logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="EMS HMI Server",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to hmi_config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: info)",
    )
    return parser.parse_args()


def main() -> None:
    """Start the EMS HMI server with uvicorn."""
    args: argparse.Namespace = _parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    logger.info("Loading config from %s", args.config)
    config: dict = load_hmi_config(args.config)

    app = create_app(config)

    server_config: uvicorn.Config = uvicorn.Config(
        app=app,
        host=config["server"]["host"],
        port=config["server"]["http_port"],
        log_level=args.log_level,
    )
    server: uvicorn.Server = uvicorn.Server(server_config)

    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("Shutting down HMI server")


if __name__ == "__main__":
    main()
