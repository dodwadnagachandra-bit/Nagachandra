"""Tests for _schema_version fields and subsystem presence flags.

Covers:
  - CONF-03: Schema version validation rejects mismatched versions
  - system_config subsystems object with per-profile defaults

Run from repo root: uv run pytest tests/test_schema_version.py -v
"""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCHEMAS_DIR: Path = REPO_ROOT / "config" / "schemas"
CONFIG_DIR: Path = REPO_ROOT / "config"

ALL_CONFIG_NAMES: list[str] = [
    "system_config",
    "bms_config",
    "pcs_config",
    "btms_config",
    "meter_config",
    "dg_config",
    "pv_config",
    "control_config",
    "alarms_config",
    "schedule_config",
    "cloud_config",
    "network_config",
    "gpio_config",
    "hmi_config",
]

PROFILES: list[str] = ["residential", "commercial", "container"]


def _load_schema(name: str) -> dict:
    """Load a JSON Schema by config name."""
    path: Path = SCHEMAS_DIR / f"{name}.schema.json"
    with path.open("r") as fh:
        return json.load(fh)


def _load_yaml(name: str, config_dir: Path) -> dict:
    """Load a YAML config by config name."""
    path: Path = config_dir / f"{name}.yaml"
    with path.open("r") as fh:
        return yaml.safe_load(fh)


class TestSchemaVersionField:
    """All 14 schemas and configs must have a valid _schema_version."""

    # Configs that have been bumped beyond 1.0
    EXPECTED_VERSIONS: dict[str, str] = {
        "hmi_config": "2.0",
    }

    def _expected_version(self, name: str) -> str:
        """Return the expected schema version for a config name."""
        return self.EXPECTED_VERSIONS.get(name, "1.0")

    @pytest.mark.parametrize("name", ALL_CONFIG_NAMES)
    def test_schema_has_schema_version_const(self, name: str) -> None:
        """Each schema defines _schema_version as a required const field."""
        expected: str = self._expected_version(name)
        schema: dict = _load_schema(name)
        props = schema.get("properties", {})
        assert "_schema_version" in props, (
            f"{name}.schema.json missing _schema_version property"
        )
        sv = props["_schema_version"]
        assert sv.get("const") == expected, (
            f"{name}.schema.json _schema_version const should be '{expected}'"
        )
        required = schema.get("required", [])
        assert "_schema_version" in required, (
            f"{name}.schema.json must require _schema_version"
        )

    @pytest.mark.parametrize("name", ALL_CONFIG_NAMES)
    def test_active_config_has_schema_version(self, name: str) -> None:
        """Active config YAML has correct _schema_version."""
        expected: str = self._expected_version(name)
        data: dict = _load_yaml(name, CONFIG_DIR)
        assert data.get("_schema_version") == expected, (
            f"config/{name}.yaml missing _schema_version: '{expected}'"
        )

    @pytest.mark.parametrize("name", ALL_CONFIG_NAMES)
    @pytest.mark.parametrize("profile", PROFILES)
    def test_profile_config_has_schema_version(
        self, name: str, profile: str
    ) -> None:
        """Profile config YAML has correct _schema_version."""
        expected: str = self._expected_version(name)
        profile_dir: Path = CONFIG_DIR / "profiles" / profile
        data: dict = _load_yaml(name, profile_dir)
        assert data.get("_schema_version") == expected, (
            f"config/profiles/{profile}/{name}.yaml missing _schema_version: '{expected}'"
        )

    def test_wrong_schema_version_fails_validation(self) -> None:
        """system_config with wrong _schema_version fails JSON Schema validation."""
        schema: dict = _load_schema("system_config")
        data: dict = _load_yaml("system_config", CONFIG_DIR)
        data["_schema_version"] = "2.0"
        validator = Draft202012Validator(schema)
        errors = list(validator.iter_errors(data))
        assert len(errors) > 0, (
            "Wrong _schema_version should fail validation"
        )

    def test_correct_schema_version_passes(self) -> None:
        """system_config with correct _schema_version passes validation."""
        schema: dict = _load_schema("system_config")
        data: dict = _load_yaml("system_config", CONFIG_DIR)
        validator = Draft202012Validator(schema)
        errors = list(validator.iter_errors(data))
        assert len(errors) == 0, (
            f"Valid system_config should pass: {errors}"
        )


class TestSubsystemPresenceFlags:
    """system_config.yaml has subsystems with has_dg, has_pv, has_btms, has_meter."""

    def test_schema_has_subsystems_object(self) -> None:
        """system_config schema defines a subsystems object."""
        schema: dict = _load_schema("system_config")
        props = schema.get("properties", {})
        assert "subsystems" in props, (
            "system_config.schema.json missing subsystems property"
        )
        sub = props["subsystems"]
        sub_props = sub.get("properties", {})
        for field in ["has_dg", "has_pv", "has_btms", "has_meter"]:
            assert field in sub_props, (
                f"subsystems missing {field} field"
            )
            assert sub_props[field].get("type") == "boolean"

    def test_residential_subsystems(self) -> None:
        """Residential profile: has_dg=false, has_pv=false, has_btms=true, has_meter=true."""
        data: dict = _load_yaml(
            "system_config", CONFIG_DIR / "profiles" / "residential"
        )
        sub = data.get("subsystems", {})
        assert sub.get("has_dg") is False
        assert sub.get("has_pv") is False
        assert sub.get("has_btms") is True
        assert sub.get("has_meter") is True

    def test_commercial_subsystems(self) -> None:
        """Commercial profile: all subsystems present."""
        data: dict = _load_yaml(
            "system_config", CONFIG_DIR / "profiles" / "commercial"
        )
        sub = data.get("subsystems", {})
        assert sub.get("has_dg") is True
        assert sub.get("has_pv") is True
        assert sub.get("has_btms") is True
        assert sub.get("has_meter") is True

    def test_container_subsystems(self) -> None:
        """Container profile: all subsystems present."""
        data: dict = _load_yaml(
            "system_config", CONFIG_DIR / "profiles" / "container"
        )
        sub = data.get("subsystems", {})
        assert sub.get("has_dg") is True
        assert sub.get("has_pv") is True
        assert sub.get("has_btms") is True
        assert sub.get("has_meter") is True

    def test_active_config_subsystems(self) -> None:
        """Active config (residential) has correct subsystem defaults."""
        data: dict = _load_yaml("system_config", CONFIG_DIR)
        sub = data.get("subsystems", {})
        assert sub.get("has_dg") is False
        assert sub.get("has_pv") is False
        assert sub.get("has_btms") is True
        assert sub.get("has_meter") is True
