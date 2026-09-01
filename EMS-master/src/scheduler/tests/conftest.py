"""Shared test fixtures for scheduler tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def valid_schedule_config() -> dict[str, Any]:
    """A valid schedule config dict matching the schema."""
    return {
        "_schema_version": "1.0",
        "mode": "time_of_day",
        "time_windows": [
            {
                "start": "06:00",
                "end": "18:00",
                "action": "discharge",
                "power_kw": 10,
            },
            {
                "start": "22:00",
                "end": "06:00",
                "action": "charge",
                "power_kw": 15,
            },
        ],
        "day_night": {
            "day_start": "06:00",
            "night_start": "18:00",
        },
        "power_curve": [0] * 96,
    }


@pytest.fixture
def sample_time_windows() -> list[dict[str, Any]]:
    """Sample time windows for evaluator tests."""
    return [
        {
            "start": "06:00",
            "end": "18:00",
            "action": "discharge",
            "power_kw": 10,
        },
        {
            "start": "22:00",
            "end": "06:00",
            "action": "charge",
            "power_kw": 15,
        },
    ]


@pytest.fixture
def sample_power_curve() -> list[float]:
    """Sample 96-point power curve with some non-zero values."""
    curve: list[float] = [0.0] * 96
    curve[0] = 5.0        # 00:00 discharge
    curve[50] = -10.0     # 12:30 charge
    curve[95] = 3.0       # 23:45 discharge
    return curve


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Return tmp_path for writing config files."""
    return tmp_path


@pytest.fixture
def schema_path() -> Path:
    """Path to the real JSON Schema file (resolved from repo root)."""
    # Walk up from this test file to find the repo root containing config/
    candidate: Path = Path(__file__).resolve().parent
    for _ in range(10):
        if (candidate / "config" / "schemas" / "schedule_config.schema.json").exists():
            return candidate / "config" / "schemas" / "schedule_config.schema.json"
        candidate = candidate.parent
    # Fallback to relative path (works when CWD is repo root)
    return Path("config/schemas/schedule_config.schema.json")
