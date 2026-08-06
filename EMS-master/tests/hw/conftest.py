"""Hardware validation shared fixtures, helpers, and constants.

Provides:
- ECU_IP, ECU_USER, DATA_DIR, EMS_VENV constants (env-var overridable)
- SERVICES list: all 14 runtime EMS services
- ecu_ssh(): plain helper function for SSH command execution on ECU
- ecu_reachable: session-scoped fixture that skips all tests if ECU is offline

Usage:
    All hw tests auto-skip when no ECU is reachable via SSH.
    Override ECU address with: EMS_ECU_IP=10.0.0.50 uv run pytest tests/hw/

pyproject.toml marker registration:
    # Add to [tool.pytest.ini_options] markers:
    # "hw: hardware validation tests (require ECU connection)"
"""

from __future__ import annotations

import os
import subprocess

import pytest

# ---------------------------------------------------------------------------
# ECU connection constants (overridable via environment variables)
# ---------------------------------------------------------------------------

ECU_IP: str = os.environ.get("EMS_ECU_IP", "192.168.1.100")
ECU_USER: str = os.environ.get("EMS_ECU_USER", "root")

# ECU SSD mount point for Parquet data and logs (Yocto fstab from Phase 27)
DATA_DIR: str = "/data"

# Yocto venv path from Phase 27 ems-python-venv_0.1.0.bb recipe
EMS_VENV: str = "/opt/ems/python/.venv"

# ---------------------------------------------------------------------------
# EMS runtime service list (all 14 services checked in Stage 1 boot validation)
# Source: deploy/systemd/ — 15 service files + ems.target; 14 runtime services
# ---------------------------------------------------------------------------

SERVICES: list[str] = [
    "data_manager",
    "ems-data-manager-python",
    "config_manager",
    "safety_manager",
    "comm_manager_c",
    "comm_manager",
    "logger",
    "control_manager",
    "alarm_manager",
    "scheduler",
    "diagnostics",
    "cloud_manager",
    "ota_manager",
    "hmi_server",
]

# ---------------------------------------------------------------------------
# SSH helper
# ---------------------------------------------------------------------------


def ecu_ssh(command: str) -> subprocess.CompletedProcess[str]:
    """Run a shell command on the ECU via SSH.

    Plain function (not a pytest fixture) so it can be called multiple times
    per test without fixture overhead.

    Args:
        command: Shell command to run on the remote ECU.

    Returns:
        CompletedProcess with stdout, stderr, and returncode.
        stdout/stderr are stripped of leading/trailing whitespace.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            f"{ECU_USER}@{ECU_IP}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


# ---------------------------------------------------------------------------
# Session-scoped reachability fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def ecu_reachable() -> None:
    """Skip all hardware tests if the ECU is not reachable via SSH.

    Session-scoped + autouse: SSH check runs once per test session. All hw
    tests in this package are automatically skipped when the ECU is offline
    or SSH is not configured, allowing `uv run pytest tests/hw/` to
    gracefully report skipped (not failed) without hardware connected.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=3",
            "-o", "BatchMode=yes",
            f"{ECU_USER}@{ECU_IP}",
            "echo ok",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(
            f"ECU not reachable via SSH at {ECU_USER}@{ECU_IP} — "
            f"connect hardware and configure SSH keys, or set EMS_ECU_IP"
        )


# ---------------------------------------------------------------------------
# Module-level marker applied to all hw tests imported from this package
# ---------------------------------------------------------------------------

# NOTE: Tests in this package apply pytest.mark.hw via their own pytestmark.
# Register the marker in pyproject.toml [tool.pytest.ini_options] markers:
#   "hw: hardware validation tests (require ECU connection)"
