"""Tests for diagnostics config loader.

Covers load_diagnostics_config() correctness, error handling,
and typed field accessibility on the returned DiagnosticsConfig dataclass.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
import jsonschema


# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

def _find_repo_root(start: str) -> str:
    """Walk up until a directory containing [tool.uv.workspace] pyproject.toml is found."""
    current: str = os.path.abspath(start)
    for _ in range(10):
        candidate: str = os.path.join(current, "pyproject.toml")
        if os.path.isfile(candidate):
            with open(candidate) as fh:
                contents: str = fh.read()
            if "[tool.uv.workspace]" in contents:
                return current
        parent: str = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise RuntimeError(f"Could not find repo root from {start}")


_REPO_ROOT: str = _find_repo_root(os.path.dirname(__file__))
_DEFAULT_CONFIG: str = os.path.join(_REPO_ROOT, "config", "diagnostics_config.yaml")
_SCHEMA_DIR: str = os.path.join(_REPO_ROOT, "config", "schemas")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_default_config() -> None:
    """load_diagnostics_config must load the default config without error."""
    from ems_diagnostics.config import load_diagnostics_config, DiagnosticsConfig

    cfg: DiagnosticsConfig = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    assert isinstance(cfg, DiagnosticsConfig)


def test_intervals_fields() -> None:
    """Loaded config must expose intervals.publish_s and intervals.trend_update_s."""
    from ems_diagnostics.config import load_diagnostics_config

    cfg = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    assert cfg.intervals.publish_s == 60
    assert cfg.intervals.trend_update_s == 3600


def test_thresholds_soh_warning() -> None:
    """Loaded config must expose thresholds.soh_warning_pct as a float."""
    from ems_diagnostics.config import load_diagnostics_config

    cfg = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    assert isinstance(cfg.thresholds.soh_warning_pct, float)
    assert cfg.thresholds.soh_warning_pct == 85.0


def test_battery_rated_capacity() -> None:
    """Loaded config must expose battery.rated_capacity_ah."""
    from ems_diagnostics.config import load_diagnostics_config

    cfg = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    assert cfg.battery.rated_capacity_ah == 100.0


def test_metrics_soh_enabled() -> None:
    """Loaded config must expose metrics.soh_enabled as bool."""
    from ems_diagnostics.config import load_diagnostics_config

    cfg = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    assert cfg.metrics.soh_enabled is True


def test_all_thresholds_accessible() -> None:
    """All threshold fields must be accessible and correctly typed."""
    from ems_diagnostics.config import load_diagnostics_config

    cfg = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    t = cfg.thresholds
    assert isinstance(t.soh_warning_pct, float)
    assert isinstance(t.soh_critical_pct, float)
    assert isinstance(t.pcs_efficiency_warning_pct, float)
    assert isinstance(t.thermal_delta_warning_c, float)
    assert isinstance(t.comm_fault_warning_rate, int)

    # Sanity: critical < warning (both are below healthy range)
    assert t.soh_critical_pct < t.soh_warning_pct


def test_all_metrics_accessible() -> None:
    """All metrics enable flags must be accessible and boolean."""
    from ems_diagnostics.config import load_diagnostics_config

    cfg = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    m = cfg.metrics
    assert isinstance(m.soh_enabled, bool)
    assert isinstance(m.pcs_efficiency_enabled, bool)
    assert isinstance(m.thermal_enabled, bool)
    assert isinstance(m.comm_health_enabled, bool)
    assert isinstance(m.predictions_enabled, bool)


def test_prediction_fields() -> None:
    """Prediction sub-config must expose min_history_days and history_window_days."""
    from ems_diagnostics.config import load_diagnostics_config

    cfg = load_diagnostics_config(_DEFAULT_CONFIG, _SCHEMA_DIR)
    assert cfg.prediction.min_history_days == 7
    assert cfg.prediction.history_window_days == 30


def test_missing_file_raises() -> None:
    """load_diagnostics_config must raise FileNotFoundError for a missing file."""
    from ems_diagnostics.config import load_diagnostics_config

    with pytest.raises(FileNotFoundError):
        load_diagnostics_config("/nonexistent/path/diag.yaml", _SCHEMA_DIR)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    """load_diagnostics_config must raise on a YAML parse error."""
    import yaml
    from ems_diagnostics.config import load_diagnostics_config

    bad_file: Path = tmp_path / "bad.yaml"
    bad_file.write_text("key: [unclosed bracket\n")

    with pytest.raises(yaml.YAMLError):
        load_diagnostics_config(str(bad_file), _SCHEMA_DIR)


def test_schema_violation_raises(tmp_path: Path) -> None:
    """load_diagnostics_config must raise jsonschema.ValidationError when required section is missing."""
    from ems_diagnostics.config import load_diagnostics_config

    # A valid-looking YAML but missing the required 'metrics' section
    minimal: str = textwrap.dedent("""
        _schema_version: "1.0"
        intervals:
          publish_s: 60
          trend_update_s: 3600
        thresholds:
          soh_warning_pct: 85.0
          soh_critical_pct: 80.0
          pcs_efficiency_warning_pct: 90.0
          thermal_delta_warning_c: 8.0
          comm_fault_warning_rate: 5
        prediction:
          min_history_days: 7
          history_window_days: 30
        battery:
          rated_capacity_ah: 100.0
        # metrics section intentionally omitted
    """).strip()

    bad_file: Path = tmp_path / "no_metrics.yaml"
    bad_file.write_text(minimal)

    with pytest.raises(jsonschema.ValidationError):
        load_diagnostics_config(str(bad_file), _SCHEMA_DIR)
