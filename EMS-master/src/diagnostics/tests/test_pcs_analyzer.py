"""Unit tests for PcsAnalyzer — AC/DC efficiency calculation.

Tests follow TDD: written before implementation to drive the design.
"""

from __future__ import annotations

import pytest

from ems_diagnostics.analyzers.pcs import PcsAnalyzer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def analyzer() -> PcsAnalyzer:
    """Fresh PcsAnalyzer instance."""
    return PcsAnalyzer()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_idle_skipped_zero_power(analyzer: PcsAnalyzer) -> None:
    """Zero AC and DC power returns None efficiency (idle state skipped)."""
    analyzer.update(active_power=0.0, dc_voltage=0.0, dc_current=0.0)
    result: dict = analyzer.get_current()
    assert result["efficiency_pct"] is None


def test_low_power_skipped(analyzer: PcsAnalyzer) -> None:
    """200W AC, 300W DC — both below 500W threshold, returns None."""
    analyzer.update(active_power=200.0, dc_voltage=100.0, dc_current=3.0)
    result: dict = analyzer.get_current()
    assert result["efficiency_pct"] is None


def test_discharge_efficiency(analyzer: PcsAnalyzer) -> None:
    """Discharge: 9500W AC, 100V * 100A DC = 10000W -> efficiency = 95.0%."""
    analyzer.update(active_power=9500.0, dc_voltage=100.0, dc_current=100.0)
    result: dict = analyzer.get_current()
    assert result["efficiency_pct"] is not None
    assert abs(result["efficiency_pct"] - 95.0) < 0.01


def test_charge_efficiency_normal(analyzer: PcsAnalyzer) -> None:
    """Charge: -10000W AC, 100V * -95A DC = -9500W -> dc/ac = 9500/10000 = 95.0%."""
    analyzer.update(active_power=-10000.0, dc_voltage=100.0, dc_current=-95.0)
    result: dict = analyzer.get_current()
    assert result["efficiency_pct"] is not None
    assert abs(result["efficiency_pct"] - 95.0) < 0.01


def test_charge_efficiency_clamped_to_100(analyzer: PcsAnalyzer) -> None:
    """Charge: -9500W AC, 100V * -100A = 10000W DC -> ratio > 100 -> clamped to 100."""
    analyzer.update(active_power=-9500.0, dc_voltage=100.0, dc_current=-100.0)
    result: dict = analyzer.get_current()
    assert result["efficiency_pct"] is not None
    assert result["efficiency_pct"] == 100.0


def test_rolling_average(analyzer: PcsAnalyzer) -> None:
    """Multiple discharge updates produce a correct rolling average."""
    # 90% efficient discharge
    analyzer.update(active_power=9000.0, dc_voltage=100.0, dc_current=100.0)
    # 80% efficient discharge
    analyzer.update(active_power=8000.0, dc_voltage=100.0, dc_current=100.0)

    result: dict = analyzer.get_current()
    assert result["avg_efficiency_pct"] is not None
    assert abs(result["avg_efficiency_pct"] - 85.0) < 0.01
    assert result["sample_count"] == 2


def test_get_current_structure(analyzer: PcsAnalyzer) -> None:
    """get_current() returns dict with required keys."""
    result: dict = analyzer.get_current()
    assert "efficiency_pct" in result
    assert "sample_count" in result
    assert "avg_efficiency_pct" in result


def test_idle_does_not_count_samples(analyzer: PcsAnalyzer) -> None:
    """Idle samples (below threshold) do not increment sample_count."""
    analyzer.update(active_power=100.0, dc_voltage=0.0, dc_current=0.0)
    analyzer.update(active_power=0.0, dc_voltage=100.0, dc_current=0.0)
    result: dict = analyzer.get_current()
    assert result["sample_count"] == 0
    assert result["avg_efficiency_pct"] is None


def test_above_threshold_only_ac(analyzer: PcsAnalyzer) -> None:
    """AC power above threshold but DC power below threshold -> skipped."""
    # active_power=1000W but dc_power = 100V * 0.1A = 10W (below threshold)
    analyzer.update(active_power=1000.0, dc_voltage=100.0, dc_current=0.1)
    result: dict = analyzer.get_current()
    assert result["efficiency_pct"] is None
