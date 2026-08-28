"""Unit tests for SohAnalyzer — BMS SOH trending and cycle detection.

Tests follow TDD: written before implementation to drive the design.
"""

from __future__ import annotations

import time
import pytest

from ems_diagnostics.analyzers.soh import SohAnalyzer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def analyzer() -> SohAnalyzer:
    """Fresh SohAnalyzer for rack 1 with rated capacity of 100 Ah."""
    return SohAnalyzer(rack_id=1, rated_capacity_ah=100.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_initial_state(analyzer: SohAnalyzer) -> None:
    """Fresh analyzer reports 100% SOH and zero cycles."""
    result: dict = analyzer.get_current()
    assert result["soh_pct"] == 100.0
    assert result["cycle_count"] == 0
    assert result["rack_id"] == 1


def test_bms_soh_used_as_primary(analyzer: SohAnalyzer) -> None:
    """BMS-reported pack_soh is used as the primary SOH value."""
    analyzer.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=95.5)
    result: dict = analyzer.get_current()
    assert result["soh_pct"] == 95.5


def test_full_cycle_detection(analyzer: SohAnalyzer) -> None:
    """A full 90%->10% cycle increments cycle_count by exactly 1."""
    # Simulate SOC going 50->90 (charges up to threshold)
    analyzer.update(pack_i=10.0, pack_soc=50.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=10.0, pack_soc=80.0, pack_soh_bms=100.0)
    # Reach CHARGED state
    analyzer.update(pack_i=10.0, pack_soc=90.0, pack_soh_bms=100.0)
    # Begin discharge (pack_i negative)
    analyzer.update(pack_i=-1.0, pack_soc=85.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=-1.0, pack_soc=50.0, pack_soh_bms=100.0)
    # Reach 10% — cycle completes
    analyzer.update(pack_i=-1.0, pack_soc=10.0, pack_soh_bms=100.0)

    result: dict = analyzer.get_current()
    assert result["cycle_count"] == 1


def test_no_double_count_on_oscillation(analyzer: SohAnalyzer) -> None:
    """SOC oscillating near 10% threshold does not count more than one cycle."""
    # Drive to CHARGED
    analyzer.update(pack_i=10.0, pack_soc=90.0, pack_soh_bms=100.0)
    # Start discharging
    analyzer.update(pack_i=-1.0, pack_soc=85.0, pack_soh_bms=100.0)
    # Reach 10% — first cycle completion
    analyzer.update(pack_i=-1.0, pack_soc=10.0, pack_soh_bms=100.0)
    # Oscillate around 10% — should NOT trigger another cycle
    analyzer.update(pack_i=-1.0, pack_soc=11.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=-1.0, pack_soc=10.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=-1.0, pack_soc=9.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=-1.0, pack_soc=10.0, pack_soh_bms=100.0)

    result: dict = analyzer.get_current()
    assert result["cycle_count"] == 1


def test_coulomb_counting_during_discharge(analyzer: SohAnalyzer) -> None:
    """Coulomb accumulator increments correctly at 1Hz with known current."""
    # Drive to CHARGED then start discharge
    analyzer.update(pack_i=10.0, pack_soc=90.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=-10.0, pack_soc=85.0, pack_soh_bms=100.0)  # DISCHARGING
    analyzer.update(pack_i=-10.0, pack_soc=80.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=-10.0, pack_soc=75.0, pack_soh_bms=100.0)

    # 3 discharge samples at -10A, each 1 second = 3 * 10/3600 Ah accumulated
    expected_ah: float = 3 * (10.0 / 3600.0)
    assert abs(analyzer._cycle_ah - expected_ah) < 1e-9


def test_get_current_structure(analyzer: SohAnalyzer) -> None:
    """get_current() returns dict with expected keys and correct rack_id."""
    result: dict = analyzer.get_current()
    assert "rack_id" in result
    assert "soh_pct" in result
    assert "cycle_count" in result
    assert "coulomb_soh_pct" in result
    assert result["rack_id"] == 1


def test_history_tracking(analyzer: SohAnalyzer) -> None:
    """add_history_point appends to history; get_history returns the list."""
    assert analyzer.get_history() == []

    ts1: int = int(time.time() * 1000)
    analyzer.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=97.0)
    analyzer.add_history_point(ts1)

    history: list[tuple[int, float]] = analyzer.get_history()
    assert len(history) == 1
    assert history[0][0] == ts1
    assert history[0][1] == 97.0

    ts2: int = ts1 + 3600_000
    analyzer.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=96.5)
    analyzer.add_history_point(ts2)

    history = analyzer.get_history()
    assert len(history) == 2
    assert history[1][1] == 96.5


def test_cycle_count_not_incremented_without_discharge(analyzer: SohAnalyzer) -> None:
    """If SOC reaches 90% but never discharges to 10%, no cycle is counted."""
    analyzer.update(pack_i=10.0, pack_soc=90.0, pack_soh_bms=100.0)  # CHARGED
    analyzer.update(pack_i=0.0, pack_soc=80.0, pack_soh_bms=100.0)   # no discharge current
    analyzer.update(pack_i=0.0, pack_soc=50.0, pack_soh_bms=100.0)
    analyzer.update(pack_i=0.0, pack_soc=10.0, pack_soh_bms=100.0)   # SOC=10 but no discharge

    result: dict = analyzer.get_current()
    assert result["cycle_count"] == 0
