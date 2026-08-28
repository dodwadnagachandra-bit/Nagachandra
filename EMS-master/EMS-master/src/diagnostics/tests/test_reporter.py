"""Tests for ReportBuilder — query dispatch and prediction engine.

Tests cover:
- build_current(): aggregates all 4 analyzer snapshots
- build_report(): daily/weekly period dispatch with query_fn callback
- build_predictions(): linear regression SOH trend projection
- Severity classification helper
"""

from __future__ import annotations

import statistics
import time
from typing import Callable
from unittest.mock import MagicMock

import pytest

from ems_diagnostics.config import (
    DiagnosticsConfig,
    IntervalsConfig,
    ThresholdsConfig,
    PredictionConfig,
    BatteryConfig,
    MetricsConfig,
)
from ems_diagnostics.analyzers import SohAnalyzer, PcsAnalyzer, ThermalAnalyzer, CommAnalyzer
from ems_diagnostics.reporter import ReportBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(
    min_history_days: int = 7,
    history_window_days: int = 30,
    soh_critical_pct: float = 80.0,
    soh_warning_pct: float = 85.0,
) -> DiagnosticsConfig:
    """Return a minimal DiagnosticsConfig for testing."""
    return DiagnosticsConfig(
        intervals=IntervalsConfig(publish_s=60, trend_update_s=3600),
        thresholds=ThresholdsConfig(
            soh_warning_pct=soh_warning_pct,
            soh_critical_pct=soh_critical_pct,
            pcs_efficiency_warning_pct=90.0,
            thermal_delta_warning_c=8.0,
            comm_fault_warning_rate=5,
        ),
        prediction=PredictionConfig(
            min_history_days=min_history_days,
            history_window_days=history_window_days,
        ),
        battery=BatteryConfig(rated_capacity_ah=100.0),
        metrics=MetricsConfig(
            soh_enabled=True,
            pcs_efficiency_enabled=True,
            thermal_enabled=True,
            comm_health_enabled=True,
            predictions_enabled=True,
        ),
    )


@pytest.fixture
def config() -> DiagnosticsConfig:
    return _make_config()


@pytest.fixture
def report_builder(config: DiagnosticsConfig) -> ReportBuilder:
    return ReportBuilder(config)


@pytest.fixture
def soh_analyzer() -> SohAnalyzer:
    a: SohAnalyzer = SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)
    a.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=95.0)
    return a


@pytest.fixture
def pcs_analyzer() -> PcsAnalyzer:
    a: PcsAnalyzer = PcsAnalyzer()
    a.update(active_power=5000.0, dc_voltage=600.0, dc_current=9.0)
    return a


@pytest.fixture
def thermal_analyzer() -> ThermalAnalyzer:
    a: ThermalAnalyzer = ThermalAnalyzer()
    a.update(max_cell_t=35.0, outlet_temp=28.0, fan_speed_pct=60.0)
    return a


@pytest.fixture
def comm_analyzer() -> CommAnalyzer:
    a: CommAnalyzer = CommAnalyzer(known_devices=["bms_rack_1", "pcs"])
    a.update_from_event_log([], window_hours=1.0)
    return a


# ---------------------------------------------------------------------------
# build_current()
# ---------------------------------------------------------------------------


def test_get_current_structure(
    report_builder: ReportBuilder,
    soh_analyzer: SohAnalyzer,
    pcs_analyzer: PcsAnalyzer,
    thermal_analyzer: ThermalAnalyzer,
    comm_analyzer: CommAnalyzer,
) -> None:
    """build_current() returns dict with all 4 analyzer sections."""
    result: dict = report_builder.build_current(
        soh_analyzers=[soh_analyzer],
        pcs=pcs_analyzer,
        thermal=thermal_analyzer,
        comm=comm_analyzer,
    )
    assert "soh" in result
    assert "pcs_efficiency" in result
    assert "thermal" in result
    assert "comm" in result


def test_get_current_soh_list(
    report_builder: ReportBuilder,
    soh_analyzer: SohAnalyzer,
    pcs_analyzer: PcsAnalyzer,
    thermal_analyzer: ThermalAnalyzer,
    comm_analyzer: CommAnalyzer,
) -> None:
    """build_current() returns list of SOH snapshots, one per rack."""
    result: dict = report_builder.build_current(
        soh_analyzers=[soh_analyzer],
        pcs=pcs_analyzer,
        thermal=thermal_analyzer,
        comm=comm_analyzer,
    )
    assert isinstance(result["soh"], list)
    assert len(result["soh"]) == 1
    assert result["soh"][0]["rack_id"] == 1
    assert result["soh"][0]["soh_pct"] == 95.0


def test_get_current_multiple_racks(
    report_builder: ReportBuilder,
    pcs_analyzer: PcsAnalyzer,
    thermal_analyzer: ThermalAnalyzer,
    comm_analyzer: CommAnalyzer,
) -> None:
    """build_current() handles multiple rack SOH analyzers."""
    rack1: SohAnalyzer = SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)
    rack1.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=94.0)
    rack2: SohAnalyzer = SohAnalyzer(rack_id=2, rated_capacity_ah=100.0)
    rack2.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=91.0)

    result: dict = report_builder.build_current(
        soh_analyzers=[rack1, rack2],
        pcs=pcs_analyzer,
        thermal=thermal_analyzer,
        comm=comm_analyzer,
    )
    assert len(result["soh"]) == 2
    assert result["soh"][0]["rack_id"] == 1
    assert result["soh"][1]["rack_id"] == 2


def test_get_current_pcs_passthrough(
    report_builder: ReportBuilder,
    soh_analyzer: SohAnalyzer,
    pcs_analyzer: PcsAnalyzer,
    thermal_analyzer: ThermalAnalyzer,
    comm_analyzer: CommAnalyzer,
) -> None:
    """build_current() passes PCS snapshot directly through."""
    result: dict = report_builder.build_current(
        soh_analyzers=[soh_analyzer],
        pcs=pcs_analyzer,
        thermal=thermal_analyzer,
        comm=comm_analyzer,
    )
    pcs_snapshot: dict = pcs_analyzer.get_current()
    assert result["pcs_efficiency"] == pcs_snapshot


def test_get_current_thermal_passthrough(
    report_builder: ReportBuilder,
    soh_analyzer: SohAnalyzer,
    pcs_analyzer: PcsAnalyzer,
    thermal_analyzer: ThermalAnalyzer,
    comm_analyzer: CommAnalyzer,
) -> None:
    """build_current() passes thermal snapshot directly through."""
    result: dict = report_builder.build_current(
        soh_analyzers=[soh_analyzer],
        pcs=pcs_analyzer,
        thermal=thermal_analyzer,
        comm=comm_analyzer,
    )
    assert result["thermal"]["delta_t"] == 7.0


# ---------------------------------------------------------------------------
# build_report()
# ---------------------------------------------------------------------------


def test_get_report_daily_period(report_builder: ReportBuilder) -> None:
    """build_report('daily', ...) uses 24h window and returns correct keys."""
    captured_params: dict = {}

    def mock_query_fn(params: dict) -> list[dict]:
        captured_params.update(params)
        return []

    result: dict = report_builder.build_report(period="daily", query_fn=mock_query_fn)
    assert result["period"] == "daily"
    assert "generated_at" in result
    assert "soh_trend" in result
    assert "efficiency_trend" in result
    assert "comm_summary" in result
    assert "alerts" in result


def test_get_report_weekly_period(report_builder: ReportBuilder) -> None:
    """build_report('weekly', ...) returns period='weekly'."""
    def mock_query_fn(params: dict) -> list[dict]:
        return []

    result: dict = report_builder.build_report(period="weekly", query_fn=mock_query_fn)
    assert result["period"] == "weekly"


def test_get_report_daily_calls_query_fn(report_builder: ReportBuilder) -> None:
    """build_report('daily') calls query_fn at least once."""
    call_count: list[int] = [0]

    def counting_query_fn(params: dict) -> list[dict]:
        call_count[0] += 1
        return []

    report_builder.build_report(period="daily", query_fn=counting_query_fn)
    assert call_count[0] >= 1


def test_get_report_weekly_calls_query_fn(report_builder: ReportBuilder) -> None:
    """build_report('weekly') calls query_fn at least once."""
    call_count: list[int] = [0]

    def counting_query_fn(params: dict) -> list[dict]:
        call_count[0] += 1
        return []

    report_builder.build_report(period="weekly", query_fn=counting_query_fn)
    assert call_count[0] >= 1


def test_get_report_generated_at_is_recent(report_builder: ReportBuilder) -> None:
    """build_report() generated_at timestamp is within 5 seconds of now."""
    def mock_query_fn(params: dict) -> list[dict]:
        return []

    before_ms: int = int(time.time() * 1000)
    result: dict = report_builder.build_report(period="daily", query_fn=mock_query_fn)
    after_ms: int = int(time.time() * 1000)
    assert before_ms <= result["generated_at"] <= after_ms + 5000


# ---------------------------------------------------------------------------
# build_predictions()
# ---------------------------------------------------------------------------


def test_predictions_min_days_empty(config: DiagnosticsConfig) -> None:
    """build_predictions returns empty list if < min_history_days points."""
    builder: ReportBuilder = ReportBuilder(config)
    rack: SohAnalyzer = SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)
    rack.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=95.0)
    # Add only 5 history points (< 7 min_history_days)
    for i in range(5):
        rack.add_history_point(timestamp_ms=i * 86400000)

    result: dict = builder.build_predictions(soh_analyzers=[rack], config=config)
    assert "predictions" in result
    assert len(result["predictions"]) == 0


def test_predictions_with_declining_soh(config: DiagnosticsConfig) -> None:
    """build_predictions returns projection for 30-point declining SOH history."""
    builder: ReportBuilder = ReportBuilder(config)
    rack: SohAnalyzer = SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)
    rack.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=95.0)

    # 30 daily points: SOH declining from 95 to 66 (slope = -1 pct/day)
    base_ts: int = 0
    for i in range(30):
        rack.add_history_point(timestamp_ms=base_ts + i * 86400000)
        # Mutate internal SOH to simulate decline
        rack._soh_pct = 95.0 - i
    # Rebuild history with correct SOH values
    rack._history = [(i * 86400000, 95.0 - i) for i in range(30)]

    result: dict = builder.build_predictions(soh_analyzers=[rack], config=config)
    preds: list[dict] = result["predictions"]
    assert len(preds) == 1
    pred: dict = preds[0]
    assert pred["metric"] == "soh"
    assert pred["rack_id"] == 1
    assert pred["trend_rate_per_day"] < 0  # degrading
    assert pred["days_to_threshold"] is not None
    assert pred["days_to_threshold"] > 0


def test_predictions_no_degradation(config: DiagnosticsConfig) -> None:
    """Flat SOH (slope ~0) -> days_to_threshold = None."""
    builder: ReportBuilder = ReportBuilder(config)
    rack: SohAnalyzer = SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)
    rack.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=95.0)
    # 10 points all at same SOH (flat)
    rack._history = [(i * 86400000, 95.0) for i in range(10)]

    result: dict = builder.build_predictions(soh_analyzers=[rack], config=config)
    preds: list[dict] = result["predictions"]
    assert len(preds) == 1
    pred: dict = preds[0]
    assert pred["days_to_threshold"] is None


def test_predictions_improving_soh(config: DiagnosticsConfig) -> None:
    """Positive slope (improving SOH) -> days_to_threshold = None (not degrading)."""
    builder: ReportBuilder = ReportBuilder(config)
    rack: SohAnalyzer = SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)
    rack.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=90.0)
    # 10 points with increasing SOH
    rack._history = [(i * 86400000, 80.0 + i * 0.5) for i in range(10)]

    result: dict = builder.build_predictions(soh_analyzers=[rack], config=config)
    preds: list[dict] = result["predictions"]
    assert len(preds) == 1
    pred: dict = preds[0]
    assert pred["days_to_threshold"] is None


def test_linear_regression_correct_values(config: DiagnosticsConfig) -> None:
    """build_predictions produces correct slope for known linear data."""
    builder: ReportBuilder = ReportBuilder(config)
    rack: SohAnalyzer = SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)
    rack.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=90.0)
    # Exact linear: y = 95 - 0.5x (day 0=95.0, day 20=85.0) — 21 points
    rack._history = [(i * 86400000, 95.0 - 0.5 * i) for i in range(21)]

    result: dict = builder.build_predictions(soh_analyzers=[rack], config=config)
    preds: list[dict] = result["predictions"]
    assert len(preds) == 1
    pred: dict = preds[0]
    # Slope should be very close to -0.5 pct/day
    assert abs(pred["trend_rate_per_day"] - (-0.5)) < 0.001


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------


def test_severity_critical(config: DiagnosticsConfig) -> None:
    """days_to_threshold < 30 -> severity = 'critical'."""
    builder: ReportBuilder = ReportBuilder(config)
    assert builder._classify_severity(25.0) == "critical"


def test_severity_warning(config: DiagnosticsConfig) -> None:
    """30 <= days_to_threshold < 90 -> severity = 'warning'."""
    builder: ReportBuilder = ReportBuilder(config)
    assert builder._classify_severity(60.0) == "warning"
    assert builder._classify_severity(30.0) == "warning"


def test_severity_info(config: DiagnosticsConfig) -> None:
    """days_to_threshold >= 90 -> severity = 'info'."""
    builder: ReportBuilder = ReportBuilder(config)
    assert builder._classify_severity(90.0) == "info"
    assert builder._classify_severity(365.0) == "info"


def test_severity_none(config: DiagnosticsConfig) -> None:
    """days_to_threshold = None -> severity = 'info' (not degrading)."""
    builder: ReportBuilder = ReportBuilder(config)
    assert builder._classify_severity(None) == "info"
