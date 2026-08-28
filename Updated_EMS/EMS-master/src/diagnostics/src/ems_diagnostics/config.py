"""Diagnostics module configuration loader.

Loads diagnostics_config.yaml, validates it against the JSON Schema, and
returns a typed DiagnosticsConfig dataclass. Raises on any error so that
misconfiguration is caught early at startup.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import yaml
import jsonschema
from jsonschema.validators import Draft202012Validator


# ---------------------------------------------------------------------------
# Nested config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IntervalsConfig:
    """Timing intervals for the diagnostics publish and trend-update loops."""

    publish_s: int
    trend_update_s: int


@dataclass
class ThresholdsConfig:
    """Alert thresholds for each diagnostic category."""

    soh_warning_pct: float
    soh_critical_pct: float
    pcs_efficiency_warning_pct: float
    thermal_delta_warning_c: float
    comm_fault_warning_rate: int


@dataclass
class PredictionConfig:
    """Parameters for the linear-regression predictive alert algorithm."""

    min_history_days: int
    history_window_days: int


@dataclass
class BatteryConfig:
    """Physical battery parameters (per installation)."""

    rated_capacity_ah: float


@dataclass
class MetricsConfig:
    """Enable / disable flags for each diagnostic category."""

    soh_enabled: bool
    pcs_efficiency_enabled: bool
    thermal_enabled: bool
    comm_health_enabled: bool
    predictions_enabled: bool


@dataclass
class DiagnosticsConfig:
    """Root diagnostics configuration. All sub-configs are typed dataclasses."""

    intervals: IntervalsConfig
    thresholds: ThresholdsConfig
    prediction: PredictionConfig
    battery: BatteryConfig
    metrics: MetricsConfig


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_diagnostics_config(
    config_path: str,
    schema_dir: str = "config/schemas",
) -> DiagnosticsConfig:
    """Load and validate diagnostics configuration from a YAML file.

    Args:
        config_path: Absolute (or CWD-relative) path to diagnostics_config.yaml.
        schema_dir: Directory containing diagnostics_config.schema.json.

    Returns:
        A fully populated DiagnosticsConfig dataclass.

    Raises:
        FileNotFoundError: If config_path does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
        jsonschema.ValidationError: If the config fails schema validation.
        FileNotFoundError: If the schema file is not found.
    """
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Diagnostics config not found: {config_path}")

    with open(config_path) as fh:
        raw: dict = yaml.safe_load(fh)

    schema_path: str = os.path.join(schema_dir, "diagnostics_config.schema.json")
    if not os.path.isfile(schema_path):
        raise FileNotFoundError(f"Diagnostics schema not found: {schema_path}")

    with open(schema_path) as fh:
        schema: dict = json.load(fh)

    Draft202012Validator(schema).validate(raw)

    return _build_config(raw)


def _build_config(raw: dict) -> DiagnosticsConfig:
    """Convert the validated raw dict into a typed DiagnosticsConfig.

    Args:
        raw: Validated config dict from yaml.safe_load.

    Returns:
        Populated DiagnosticsConfig dataclass tree.
    """
    iv: dict = raw["intervals"]
    intervals: IntervalsConfig = IntervalsConfig(
        publish_s=int(iv["publish_s"]),
        trend_update_s=int(iv["trend_update_s"]),
    )

    th: dict = raw["thresholds"]
    thresholds: ThresholdsConfig = ThresholdsConfig(
        soh_warning_pct=float(th["soh_warning_pct"]),
        soh_critical_pct=float(th["soh_critical_pct"]),
        pcs_efficiency_warning_pct=float(th["pcs_efficiency_warning_pct"]),
        thermal_delta_warning_c=float(th["thermal_delta_warning_c"]),
        comm_fault_warning_rate=int(th["comm_fault_warning_rate"]),
    )

    pr: dict = raw["prediction"]
    prediction: PredictionConfig = PredictionConfig(
        min_history_days=int(pr["min_history_days"]),
        history_window_days=int(pr["history_window_days"]),
    )

    bt: dict = raw["battery"]
    battery: BatteryConfig = BatteryConfig(
        rated_capacity_ah=float(bt["rated_capacity_ah"]),
    )

    mt: dict = raw["metrics"]
    metrics: MetricsConfig = MetricsConfig(
        soh_enabled=bool(mt["soh_enabled"]),
        pcs_efficiency_enabled=bool(mt["pcs_efficiency_enabled"]),
        thermal_enabled=bool(mt["thermal_enabled"]),
        comm_health_enabled=bool(mt["comm_health_enabled"]),
        predictions_enabled=bool(mt["predictions_enabled"]),
    )

    return DiagnosticsConfig(
        intervals=intervals,
        thresholds=thresholds,
        prediction=prediction,
        battery=battery,
        metrics=metrics,
    )
