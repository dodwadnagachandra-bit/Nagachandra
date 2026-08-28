"""Tests for schedule config loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from ems_scheduler.config import load_schedule_config


class TestLoadScheduleConfig:
    """Tests for load_schedule_config function."""

    def test_load_valid_config(
        self,
        valid_schedule_config: dict[str, Any],
        config_dir: Path,
        schema_path: Path,
    ) -> None:
        """Loading a valid schedule config returns a dict with expected keys."""
        config_file: Path = config_dir / "schedule_config.yaml"
        config_file.write_text(yaml.dump(valid_schedule_config), encoding="utf-8")

        result: dict[str, Any] = load_schedule_config(config_file, schema_path)

        assert isinstance(result, dict)
        assert result["mode"] == "time_of_day"
        assert "time_windows" in result
        assert "day_night" in result
        assert "power_curve" in result
        assert len(result["power_curve"]) == 96

    def test_load_missing_file(self, schema_path: Path) -> None:
        """Loading a nonexistent config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_schedule_config(Path("/nonexistent/config.yaml"), schema_path)

    def test_load_invalid_schema(
        self,
        valid_schedule_config: dict[str, Any],
        config_dir: Path,
        schema_path: Path,
    ) -> None:
        """Loading a config with invalid mode value raises ValueError."""
        invalid_config: dict[str, Any] = valid_schedule_config.copy()
        invalid_config["mode"] = "invalid_mode"

        config_file: Path = config_dir / "bad_config.yaml"
        config_file.write_text(yaml.dump(invalid_config), encoding="utf-8")

        with pytest.raises(ValueError, match="validation failed"):
            load_schedule_config(config_file, schema_path)

    def test_load_missing_schema_file(
        self,
        valid_schedule_config: dict[str, Any],
        config_dir: Path,
    ) -> None:
        """Loading with a nonexistent schema file raises FileNotFoundError."""
        config_file: Path = config_dir / "schedule_config.yaml"
        config_file.write_text(yaml.dump(valid_schedule_config), encoding="utf-8")

        with pytest.raises(FileNotFoundError):
            load_schedule_config(
                config_file,
                Path("/nonexistent/schema.json"),
            )
