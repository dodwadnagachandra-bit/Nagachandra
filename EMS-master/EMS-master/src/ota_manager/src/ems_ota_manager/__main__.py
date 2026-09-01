"""ota_manager entry point — A/B partition OTA update service.

Usage:
    python -m ems_ota_manager [--config PATH] [--log-level LEVEL]
    uv run python -m ems_ota_manager --config config/ota_config.yaml

Environment variables (override ZMQ defaults):
    EMS_OTA_PUB_ENDPOINT   -- PUB bind address (default: ipc:///run/ems/ota_pub.sock)
    EMS_OTA_CMD_ENDPOINT   -- REP bind address (default: ipc:///run/ems/ota_cmd.sock)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from ems_ota_manager.config import load_ota_config
from ems_ota_manager.downloader import HttpDownloader
from ems_ota_manager.health import HealthChecker
from ems_ota_manager.loop import OtaManager
from ems_ota_manager.partition import PartitionBackend
from ems_ota_manager.state_machine import OtaStateMachine
from ems_ota_manager.verifier import PackageVerifier

logger: logging.Logger = logging.getLogger("ems_ota_manager")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed namespace with .config and .log_level.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="ems_ota_manager",
        description="EMS OTA Manager — A/B partition firmware update service",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/ota_config.yaml"),
        help="Path to ota_config.yaml (default: config/ota_config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    """Async entry point: load config, wire components, run until signal.

    Args:
        args: Parsed command-line arguments from parse_args().
    """
    logger.info("Loading OTA config from %s", args.config)
    config: dict = load_ota_config(args.config)

    # Read ZMQ endpoint overrides from env vars
    ota_pub: str | None = os.environ.get("EMS_OTA_PUB_ENDPOINT")
    ota_cmd: str | None = os.environ.get("EMS_OTA_CMD_ENDPOINT")

    # Build components from config sections
    staging_cfg: dict = config["staging"]
    staging_dir: Path = Path(staging_cfg["dir"])
    max_mb: int = int(staging_cfg.get("max_package_mb", 500))
    download_cfg: dict = config["download"]
    chunk_size: int = int(download_cfg.get("chunk_size", 65536))
    timeout_s: float = float(download_cfg.get("timeout_s", 300.0))

    downloader: HttpDownloader = HttpDownloader(
        staging_dir=staging_dir,
        max_package_mb=max_mb,
        chunk_size=chunk_size,
        timeout_s=timeout_s,
    )

    security_cfg: dict = config["security"]
    verifier: PackageVerifier = PackageVerifier(
        public_key_hex=security_cfg["public_key_hex"]
    )

    partition: PartitionBackend = PartitionBackend(config)

    health_cfg: dict = config["health_check"]
    health_checker: HealthChecker = HealthChecker(
        services=health_cfg["services"],
        timeout_s=float(health_cfg.get("timeout_s", 300.0)),
        poll_interval_s=float(health_cfg.get("poll_interval_s", 10.0)),
    )

    version_cfg: dict = config["version"]
    version_state_path: Path = Path(version_cfg["state_path"])

    state_machine: OtaStateMachine = OtaStateMachine(
        downloader=downloader,
        verifier=verifier,
        partition=partition,
        health_checker=health_checker,
        version_state_path=version_state_path,
    )

    ota_manager: OtaManager = OtaManager(
        config=config,
        state_machine=state_machine,
        partition=partition,
        ota_pub_endpoint=ota_pub,
        ota_cmd_endpoint=ota_cmd,
    )

    # Install signal handlers for graceful shutdown
    asyncio_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        ota_manager.stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, _signal_handler)

    logger.info("OTA manager starting")
    try:
        await ota_manager.run()
    finally:
        ota_manager.cleanup()
        logger.info("OTA manager stopped")


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
