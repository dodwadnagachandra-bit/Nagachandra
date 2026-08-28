"""Tests for ems_control_manager.config — control_config loader and validator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ems_control_manager.config import load_control_config

# Paths relative to the EMS repo root (CWD when running uv run pytest)
REAL_CONFIG_PATH: Path = Path("config/control_config.yaml")
REAL_SCHEMA_PATH: Path = Path("config/schemas/control_config.schema.json")


class TestLoadControlConfig:
    """Test suite for load_control_config()."""

    def test_loads_valid_config(self) -> None:
        """Valid control_config.yaml passes validation and returns a dict."""
        config = load_control_config(REAL_CONFIG_PATH, REAL_SCHEMA_PATH)
        assert isinstance(config, dict)
        assert "soc_limits" in config
        assert "power_limits" in config
        assert "source_priority" in config
        assert "state_machine" in config

    def test_config_values_match_yaml(self) -> None:
        """Loaded values match the raw YAML content (no silent transformation)."""
        config = load_control_config(REAL_CONFIG_PATH, REAL_SCHEMA_PATH)
        soc: dict = config["soc_limits"]
        assert soc["charge_cutoff_pct"] == 95
        assert soc["discharge_cutoff_pct"] == 10

        power: dict = config["power_limits"]
        assert power["max_charge_kw"] == 25
        assert power["max_discharge_kw"] == 25

        sm: dict = config["state_machine"]
        assert sm["loop_interval_ms"] == 1000
        assert sm["fault_retry_count"] == 3

    def test_missing_required_field_raises_value_error(self, tmp_path: Path) -> None:
        """Config missing a required field raises ValueError with field info."""
        bad_config: dict = {
            "_schema_version": "1.0",
            # Missing: soc_limits, power_limits, source_priority, state_machine
        }
        bad_path: Path = tmp_path / "bad_control_config.yaml"
        bad_path.write_text(yaml.dump(bad_config), encoding="utf-8")

        with pytest.raises(ValueError, match="validation failed"):
            load_control_config(bad_path, REAL_SCHEMA_PATH)

    def test_wrong_type_raises_value_error(self, tmp_path: Path) -> None:
        """Config with wrong field type (string where number expected) raises ValueError."""
        bad_config: dict = {
            "_schema_version": "1.0",
            "soc_limits": {
                "charge_cutoff_pct": "not-a-number",  # should be number
                "discharge_cutoff_pct": 10,
            },
            "power_limits": {
                "max_charge_kw": 25,
                "max_discharge_kw": 25,
            },
            "source_priority": {
                "day_order": ["solar", "grid", "bess"],
                "night_order": ["grid", "bess"],
            },
            "state_machine": {
                "loop_interval_ms": 1000,
                "fault_retry_count": 3,
            },
        }
        bad_path: Path = tmp_path / "bad_types.yaml"
        bad_path.write_text(yaml.dump(bad_config), encoding="utf-8")

        with pytest.raises(ValueError, match="validation failed"):
            load_control_config(bad_path, REAL_SCHEMA_PATH)

    def test_out_of_range_value_raises_value_error(self, tmp_path: Path) -> None:
        """Config with out-of-range value (e.g. charge_cutoff > 100%) raises ValueError."""
        bad_config: dict = {
            "_schema_version": "1.0",
            "soc_limits": {
                "charge_cutoff_pct": 150,  # maximum is 100
                "discharge_cutoff_pct": 10,
            },
            "power_limits": {
                "max_charge_kw": 25,
                "max_discharge_kw": 25,
            },
            "source_priority": {
                "day_order": ["solar", "grid", "bess"],
                "night_order": ["grid", "bess"],
            },
            "state_machine": {
                "loop_interval_ms": 1000,
                "fault_retry_count": 3,
            },
        }
        bad_path: Path = tmp_path / "out_of_range.yaml"
        bad_path.write_text(yaml.dump(bad_config), encoding="utf-8")

        with pytest.raises(ValueError, match="validation failed"):
            load_control_config(bad_path, REAL_SCHEMA_PATH)

    def test_additional_properties_raises_value_error(self, tmp_path: Path) -> None:
        """Config with extra unexpected field raises ValueError (additionalProperties: false)."""
        bad_config: dict = {
            "_schema_version": "1.0",
            "soc_limits": {
                "charge_cutoff_pct": 95,
                "discharge_cutoff_pct": 10,
            },
            "power_limits": {
                "max_charge_kw": 25,
                "max_discharge_kw": 25,
            },
            "source_priority": {
                "day_order": ["solar", "grid", "bess"],
                "night_order": ["grid", "bess"],
            },
            "state_machine": {
                "loop_interval_ms": 1000,
                "fault_retry_count": 3,
            },
            "unknown_field": "should_not_be_here",  # additionalProperties: false
        }
        bad_path: Path = tmp_path / "extra_field.yaml"
        bad_path.write_text(yaml.dump(bad_config), encoding="utf-8")

        with pytest.raises(ValueError, match="validation failed"):
            load_control_config(bad_path, REAL_SCHEMA_PATH)

    def test_missing_config_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """Nonexistent config file raises FileNotFoundError."""
        missing: Path = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            load_control_config(missing, REAL_SCHEMA_PATH)

    def test_non_mapping_yaml_raises_value_error(self, tmp_path: Path) -> None:
        """YAML that is a list (not a mapping) raises ValueError."""
        bad_path: Path = tmp_path / "list_config.yaml"
        bad_path.write_text("- item1\n- item2\n", encoding="utf-8")

        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_control_config(bad_path, REAL_SCHEMA_PATH)
