"""Tests for ems_alarm_manager.config — alarms_config loader and validator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ems_alarm_manager.config import load_alarm_config

# Paths relative to the EMS repo root (CWD when running uv run pytest)
REAL_CONFIG_PATH: Path = Path("config/alarms_config.yaml")
REAL_SCHEMA_PATH: Path = Path("config/schemas/alarms_config.schema.json")


class TestLoadAlarmConfig:
    """Test suite for load_alarm_config()."""

    def test_load_valid_config(self) -> None:
        """Valid alarms_config.yaml passes validation and returns dict with rules and defaults."""
        config = load_alarm_config(REAL_CONFIG_PATH, REAL_SCHEMA_PATH)
        assert isinstance(config, dict)
        assert "rules" in config
        assert "defaults" in config
        assert len(config["rules"]) == 9

    def test_load_missing_file(self) -> None:
        """Nonexistent config path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_alarm_config(Path("nonexistent.yaml"), REAL_SCHEMA_PATH)

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML raises ValueError."""
        bad_path: Path = tmp_path / "bad.yaml"
        bad_path.write_text("key: :\n  invalid: yaml: [unclosed", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid YAML"):
            load_alarm_config(bad_path, REAL_SCHEMA_PATH)

    def test_load_schema_violation(self, tmp_path: Path) -> None:
        """Config missing required field raises ValueError with validation info."""
        bad_config: dict = {
            "_schema_version": "1.0",
            # Missing: defaults and rules
        }
        bad_path: Path = tmp_path / "missing_fields.yaml"
        bad_path.write_text(yaml.dump(bad_config), encoding="utf-8")
        with pytest.raises(ValueError, match="validation failed"):
            load_alarm_config(bad_path, REAL_SCHEMA_PATH)
