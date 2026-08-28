"""Tests for IPC constants and diagnostics config schema.

RED phase: verifies that IPC constants and config YAML/schema exist
and that the schema correctly validates and rejects configs.
"""

from __future__ import annotations

import json
import os

import pytest
import yaml
import jsonschema


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _find_repo_root(start: str) -> str:
    """Walk up from start until a directory containing pyproject.toml with [tool.uv.workspace] is found."""
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
_CONFIG_DIR: str = os.path.join(_REPO_ROOT, "config")
_SCHEMA_DIR: str = os.path.join(_CONFIG_DIR, "schemas")
_DIAG_CONFIG: str = os.path.join(_CONFIG_DIR, "diagnostics_config.yaml")
_DIAG_SCHEMA: str = os.path.join(_SCHEMA_DIR, "diagnostics_config.schema.json")


# ---------------------------------------------------------------------------
# IPC constants
# ---------------------------------------------------------------------------


def test_ipc_sock_diagnostics_cmd_exists() -> None:
    """SOCK_DIAGNOSTICS_CMD constant must be importable from ems_common.ipc."""
    from ems_common.ipc import SOCK_DIAGNOSTICS_CMD

    assert SOCK_DIAGNOSTICS_CMD == "ipc:///run/ems/diagnostics_cmd.sock"


def test_ipc_sock_diagnostics_pub_exists() -> None:
    """SOCK_DIAGNOSTICS_PUB constant must be importable from ems_common.ipc."""
    from ems_common.ipc import SOCK_DIAGNOSTICS_PUB

    assert SOCK_DIAGNOSTICS_PUB == "ipc:///run/ems/diagnostics_pub.sock"


def test_ipc_topic_diagnostics_exists() -> None:
    """TOPIC_DIAGNOSTICS constant must be importable from ems_common.ipc."""
    from ems_common.ipc import TOPIC_DIAGNOSTICS

    assert TOPIC_DIAGNOSTICS == "diagnostics"


# ---------------------------------------------------------------------------
# Config YAML existence and structure
# ---------------------------------------------------------------------------


def test_diag_config_yaml_exists() -> None:
    """diagnostics_config.yaml must exist."""
    assert os.path.isfile(_DIAG_CONFIG), f"Missing: {_DIAG_CONFIG}"


def test_diag_schema_exists() -> None:
    """diagnostics_config.schema.json must exist."""
    assert os.path.isfile(_DIAG_SCHEMA), f"Missing: {_DIAG_SCHEMA}"


def test_diag_config_loads_with_yaml() -> None:
    """diagnostics_config.yaml must parse cleanly with yaml.safe_load."""
    with open(_DIAG_CONFIG) as fh:
        data: dict = yaml.safe_load(fh)
    assert isinstance(data, dict)


def test_diag_config_has_required_sections() -> None:
    """diagnostics_config.yaml must contain all top-level sections."""
    with open(_DIAG_CONFIG) as fh:
        data: dict = yaml.safe_load(fh)
    for section in ("intervals", "thresholds", "prediction", "battery", "metrics"):
        assert section in data, f"Missing section: {section}"


def test_diag_config_has_soh_warning_pct() -> None:
    """thresholds section must contain soh_warning_pct."""
    with open(_DIAG_CONFIG) as fh:
        data: dict = yaml.safe_load(fh)
    assert "soh_warning_pct" in data["thresholds"]


def test_diag_config_validates_against_schema() -> None:
    """diagnostics_config.yaml must pass jsonschema validation."""
    with open(_DIAG_CONFIG) as fh:
        cfg: dict = yaml.safe_load(fh)
    with open(_DIAG_SCHEMA) as fh:
        schema: dict = json.load(fh)
    jsonschema.validate(cfg, schema)  # raises on failure


def test_schema_rejects_missing_intervals() -> None:
    """Schema must reject a config that omits the intervals section."""
    with open(_DIAG_CONFIG) as fh:
        cfg: dict = yaml.safe_load(fh)
    with open(_DIAG_SCHEMA) as fh:
        schema: dict = json.load(fh)

    bad_cfg: dict = {k: v for k, v in cfg.items() if k != "intervals"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_cfg, schema)


def test_schema_rejects_bad_soh_type() -> None:
    """Schema must reject a config where soh_warning_pct is a string, not a number."""
    with open(_DIAG_CONFIG) as fh:
        cfg: dict = yaml.safe_load(fh)
    with open(_DIAG_SCHEMA) as fh:
        schema: dict = json.load(fh)

    import copy
    bad_cfg: dict = copy.deepcopy(cfg)
    bad_cfg["thresholds"]["soh_warning_pct"] = "bad"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_cfg, schema)
