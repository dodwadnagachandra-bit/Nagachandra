"""Shared test fixtures for the EMS logger module.

Provides tmp data directories, sample telemetry/event dicts matching
the publisher.py and ipc.py envelope contracts, and default topology.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory for Parquet/JSONL test output."""
    data_dir: Path = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def default_topology() -> dict[str, int]:
    """Default residential topology dimensions."""
    return {
        "cluster_count": 1,
        "racks_per_cluster": 4,
        "modules_per_rack": 8,
        "cells_per_module": 15,
        "temps_per_module": 4,
    }


def sample_rack_telemetry() -> dict[str, Any]:
    """Sample rack telemetry dict matching publisher._rack_to_dict() output."""
    return {
        "last_update_ms": int(time.time() * 1000),
        "pack_v": 51.2,
        "pack_i": -12.5,
        "pack_soc": 78.3,
        "pack_soh": 99.1,
        "min_cell_v": 3.18,
        "max_cell_v": 3.25,
        "avg_cell_v": 3.21,
        "min_cell_t": 22.0,
        "max_cell_t": 28.5,
        "avg_cell_t": 25.2,
        "fault_code": 0,
        "online": 1,
    }


def sample_system_telemetry() -> dict[str, dict[str, Any]]:
    """Sample system telemetry dicts matching all publisher sections.

    Returns a dict keyed by section name with the payload dict for each.
    """
    return {
        "pcs": {
            "last_update_ms": int(time.time() * 1000),
            "ac_voltage": 230.1,
            "ac_current": 45.2,
            "active_power": 10350.0,
            "reactive_power": 120.0,
            "dc_voltage": 384.0,
            "dc_current": 27.5,
            "frequency": 50.01,
            "temperature": 42.3,
            "state": 3,
            "fault_code": 0,
        },
        "meter": {
            "last_update_ms": int(time.time() * 1000),
            "voltage": 230.0,
            "current": 44.8,
            "active_power": 10300.0,
            "reactive_power": 110.0,
            "frequency": 50.00,
            "power_factor": 0.99,
            "energy_import": 12345.6,
            "energy_export": 9876.5,
        },
        "btms": {
            "last_update_ms": int(time.time() * 1000),
            "inlet_temp": 20.5,
            "outlet_temp": 25.3,
            "fan_speed_pct": 60.0,
            "cooling_active": 1,
        },
        "gpio": {
            "last_update_ms": int(time.time() * 1000),
            "di": [1, 0, 1, 0, 0, 0, 0, 1],
            "do_state": [0, 0, 0, 0, 0, 0, 0, 0],
        },
        "system": {
            "last_update_ms": int(time.time() * 1000),
            "control_state": 2,
            "source_priority": 1,
            "active_setpoint_kw": 10.0,
            "total_soc": 78.3,
            "total_power_kw": 10.35,
            "total_energy_kwh": 48.0,
            "ems_uptime_s": 86400,
        },
    }


def sample_event() -> dict[str, Any]:
    """Sample event dict matching ipc.encode_event() envelope."""
    return {
        "ts": int(time.time() * 1000),
        "src": "safety_manager",
        "severity": "warning",
        "event_type": "comm_fault",
        "message": "BMS rack 0 communication timeout",
        "data": {"rack": 0, "timeout_ms": 5000},
    }
