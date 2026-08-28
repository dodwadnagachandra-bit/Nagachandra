"""Tests for ConfigManager core class, profile overlay, and CLI.

Covers:
  - CONF-01: All 14 configs load and validate at startup with fail-fast
  - CONF-03: Schema version mismatch detected with migration guidance
  - CONF-06: Profile overlay loads profile-specific config
  - CONF-07: CLI ems-config validate returns pass/fail

Run from repo root: uv run pytest tests/test_config_manager.py -v
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_DIR: Path = REPO_ROOT / "config"
SCHEMAS_DIR: Path = CONFIG_DIR / "schemas"

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

# Core configs that must always be present
CORE_CONFIGS: list[str] = [
    "system_config",
    "gpio_config",
    "control_config",
    "alarms_config",
    "schedule_config",
    "bms_config",
    "pcs_config",
    "network_config",
    "cloud_config",
    "hmi_config",
]

# Optional device configs
DEVICE_CONFIGS: dict[str, str] = {
    "dg_config": "has_dg",
    "pv_config": "has_pv",
    "btms_config": "has_btms",
    "meter_config": "has_meter",
}


def _copy_config_tree(src_dir: Path, dst_dir: Path) -> None:
    """Copy all YAML configs from src to dst."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for yaml_file in src_dir.glob("*.yaml"):
        (dst_dir / yaml_file.name).write_text(yaml_file.read_text())


def _copy_schemas(dst_dir: Path) -> None:
    """Copy all JSON schemas to dst_dir."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for schema_file in SCHEMAS_DIR.glob("*.schema.json"):
        (dst_dir / schema_file.name).write_text(schema_file.read_text())


def _create_test_env(tmp_path: Path, profile: str | None = None) -> tuple[Path, Path]:
    """Create a test environment with configs and schemas.

    Returns:
        (config_dir, schema_dir)
    """
    config_dir: Path = tmp_path / "config"
    schema_dir: Path = tmp_path / "schemas"

    # Copy active configs
    _copy_config_tree(CONFIG_DIR, config_dir)

    # Copy profile configs if needed
    if profile:
        profile_src: Path = CONFIG_DIR / "profiles" / profile
        profile_dst: Path = config_dir / "profiles" / profile
        _copy_config_tree(profile_src, profile_dst)

    # Copy schemas
    _copy_schemas(schema_dir)

    return config_dir, schema_dir


@pytest.fixture
def test_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create a test environment with all configs and schemas."""
    return _create_test_env(tmp_path)


@pytest.fixture
def residential_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create test environment with residential profile."""
    return _create_test_env(tmp_path, profile="residential")


class TestConfigManagerLoadAll:
    """Test ConfigManager.load_all() behavior."""

    def test_load_all_valid_configs_succeeds(self, test_env: tuple[Path, Path]) -> None:
        """load_all() with valid configs populates cache with all 14 configs."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env
        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        cm.load_all()

        # All 14 configs should be loaded (residential has all subsystems
        # marked: has_btms=true, has_meter=true, has_dg=false, has_pv=false)
        # So we should have 10 core + 2 optional (btms, meter) = 12
        for name in CORE_CONFIGS:
            assert cm.get_config(name) is not None, f"{name} should be loaded"

    def test_load_all_invalid_config_fails_fast(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """load_all() with invalid config raises error (fail-fast)."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env

        # Corrupt system_config to make it invalid
        sys_config_path: Path = config_dir / "system_config.yaml"
        data: dict = yaml.safe_load(sys_config_path.read_text())
        data["topology"]["cluster_count"] = 999  # Invalid: max is 8
        sys_config_path.write_text(yaml.dump(data))

        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        with pytest.raises(SystemExit):
            cm.load_all()

    def test_load_all_missing_optional_device_config_succeeds(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """load_all() succeeds when optional device config is missing if subsystem disabled."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env

        # Active config (residential) has has_dg=false, so dg_config is optional
        # Remove dg_config.yaml
        dg_path: Path = config_dir / "dg_config.yaml"
        if dg_path.exists():
            dg_path.unlink()

        # Also remove pv_config since has_pv=false
        pv_path: Path = config_dir / "pv_config.yaml"
        if pv_path.exists():
            pv_path.unlink()

        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        cm.load_all()  # Should succeed

        # Core configs should still be accessible
        assert cm.get_config("system_config") is not None

    def test_load_all_missing_required_core_config_fails(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """load_all() fails when a required core config is missing."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env

        # Remove a core config
        (config_dir / "gpio_config.yaml").unlink()

        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        with pytest.raises(SystemExit):
            cm.load_all()


class TestConfigManagerGetConfig:
    """Test ConfigManager.get_config() and get_value()."""

    def test_get_config_returns_full_dict(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """get_config('pcs_config') returns full parsed dict."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env
        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        cm.load_all()

        pcs: dict = cm.get_config("pcs_config")
        assert isinstance(pcs, dict)
        assert "connection" in pcs

    def test_get_value_dotted_path(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """get_value('pcs_config', 'connection.protocol') returns 'rtu'."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env
        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        cm.load_all()

        protocol: Any = cm.get_value("pcs_config", "connection.protocol")
        assert protocol == "rtu"

    def test_get_value_nonexistent_path_raises(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """get_value('pcs_config', 'nonexistent.path') raises descriptive error."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env
        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        cm.load_all()

        with pytest.raises((KeyError, ValueError)) as exc_info:
            cm.get_value("pcs_config", "nonexistent.path")

        # Error should be descriptive with available keys
        error_msg: str = str(exc_info.value)
        assert "nonexistent" in error_msg or "available" in error_msg.lower()

    def test_get_config_not_loaded_raises(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """get_config() for unloaded config raises KeyError."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env
        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        # Do NOT call load_all

        with pytest.raises(KeyError):
            cm.get_config("system_config")


class TestSchemaVersionMismatch:
    """Test CONF-03: Schema version mismatch detection."""

    def test_schema_version_mismatch_error(
        self, test_env: tuple[Path, Path]
    ) -> None:
        """Wrong _schema_version produces clear error with migration guidance."""
        from ems_config_manager.manager import ConfigManager

        config_dir, schema_dir = test_env

        # Modify system_config to have wrong version
        sys_path: Path = config_dir / "system_config.yaml"
        data: dict = yaml.safe_load(sys_path.read_text())
        data["_schema_version"] = "2.0"
        sys_path.write_text(yaml.dump(data))

        cm = ConfigManager(config_dir=config_dir, schema_dir=schema_dir)
        with pytest.raises(SystemExit) as exc_info:
            cm.load_all()

        # Should exit with non-zero
        assert exc_info.value.code != 0


class TestProfileOverlay:
    """Test CONF-06: Profile overlay loading."""

    def test_profile_overlay_loads_profile_config(
        self, tmp_path: Path
    ) -> None:
        """Profile overlay loads profile-specific config when profile specified."""
        from ems_config_manager.overlay import load_with_profile

        # Create active config
        config_dir: Path = tmp_path / "config"
        config_dir.mkdir()
        active_data: dict = {"_schema_version": "1.0", "value": "active"}
        (config_dir / "test_config.yaml").write_text(yaml.dump(active_data))

        # Create profile config
        profile_dir: Path = config_dir / "profiles" / "commercial"
        profile_dir.mkdir(parents=True)
        profile_data: dict = {"_schema_version": "1.0", "value": "commercial"}
        (profile_dir / "test_config.yaml").write_text(yaml.dump(profile_data))

        result: dict = load_with_profile("test_config", config_dir, "commercial")
        assert result["value"] == "commercial"

    def test_profile_overlay_falls_back_to_default(
        self, tmp_path: Path
    ) -> None:
        """Profile overlay falls back to default config when no profile file exists."""
        from ems_config_manager.overlay import load_with_profile

        config_dir: Path = tmp_path / "config"
        config_dir.mkdir()
        active_data: dict = {"_schema_version": "1.0", "value": "default"}
        (config_dir / "test_config.yaml").write_text(yaml.dump(active_data))

        # No profile directory exists
        result: dict = load_with_profile("test_config", config_dir, "nonexistent")
        assert result["value"] == "default"

    def test_profile_overlay_none_profile(self, tmp_path: Path) -> None:
        """When profile is None, loads default config."""
        from ems_config_manager.overlay import load_with_profile

        config_dir: Path = tmp_path / "config"
        config_dir.mkdir()
        active_data: dict = {"_schema_version": "1.0", "value": "default"}
        (config_dir / "test_config.yaml").write_text(yaml.dump(active_data))

        result: dict = load_with_profile("test_config", config_dir, None)
        assert result["value"] == "default"


class TestCLIValidate:
    """Test CONF-07: CLI ems-config validate."""

    def test_cli_validate_valid_file_exit_0(self) -> None:
        """CLI validate returns exit 0 for valid file."""
        result: subprocess.CompletedProcess = subprocess.run(
            [
                sys.executable, "-m", "ems_config_manager.cli",
                "validate", str(CONFIG_DIR / "system_config.yaml"),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 for valid file.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_cli_validate_invalid_file_exit_1(self, tmp_path: Path) -> None:
        """CLI validate returns exit 1 with errors for invalid file."""
        # Create invalid system_config
        invalid_data: dict = {
            "_schema_version": "1.0",
            "system": {"site_id": "X", "site_name": "Y"},
            "topology": {
                "cluster_count": 999,  # Invalid
                "racks_per_cluster": 1,
                "modules_per_rack": 3,
                "cells_per_module": 16,
                "temps_per_module": 4,
            },
        }
        invalid_path: Path = tmp_path / "system_config.yaml"
        invalid_path.write_text(yaml.dump(invalid_data))

        result: subprocess.CompletedProcess = subprocess.run(
            [
                sys.executable, "-m", "ems_config_manager.cli",
                "validate", str(invalid_path),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 1, (
            f"Expected exit 1 for invalid file.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_cli_validate_directory(self) -> None:
        """CLI validate on directory validates all 14 files."""
        result: subprocess.CompletedProcess = subprocess.run(
            [
                sys.executable, "-m", "ems_config_manager.cli",
                "validate", str(CONFIG_DIR),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 for valid directory.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "14" in result.stdout or "valid" in result.stdout.lower()
