"""Unit tests for ThermalAnalyzer — cooling effectiveness and fan duty tracking.

Tests verify:
- Initial state returns None values before any update
- delta_t = max_cell_t - outlet_temp (NOT ambient_temp)
- Fan duty score is rolling average of fan_speed_pct
- Rolling window enforces maxlen (oldest samples dropped)
- Negative delta_t is valid (cooling running below cell temp)
- get_current() returns all expected dict keys
"""

from __future__ import annotations

from ems_diagnostics.analyzers.thermal import ThermalAnalyzer


class TestThermalAnalyzerInitialState:
    """Tests for initial state before any update calls."""

    def test_initial_state_none(self) -> None:
        """Before any update, all get_current() values should be None."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        result: dict = analyzer.get_current()
        assert result["delta_t"] is None
        assert result["fan_duty_score"] is None
        assert result["max_cell_t"] is None
        assert result["outlet_temp"] is None

    def test_get_current_structure(self) -> None:
        """get_current() must return a dict with all four expected keys."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        result: dict = analyzer.get_current()
        assert "delta_t" in result
        assert "fan_duty_score" in result
        assert "max_cell_t" in result
        assert "outlet_temp" in result
        assert len(result) == 4


class TestThermalAnalyzerDeltaT:
    """Tests for delta_t = max_cell_t - outlet_temp calculation."""

    def test_delta_t_calculation(self) -> None:
        """max_cell_t=35.0, outlet_temp=25.0 should give delta_t=10.0."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        analyzer.update(max_cell_t=35.0, outlet_temp=25.0, fan_speed_pct=50.0)
        result: dict = analyzer.get_current()
        assert result["delta_t"] == 10.0
        assert result["max_cell_t"] == 35.0
        assert result["outlet_temp"] == 25.0

    def test_negative_delta_t(self) -> None:
        """outlet_temp > max_cell_t -> negative delta_t is a valid scenario during active cooling."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        analyzer.update(max_cell_t=20.0, outlet_temp=25.0, fan_speed_pct=100.0)
        result: dict = analyzer.get_current()
        assert result["delta_t"] == -5.0

    def test_zero_delta_t(self) -> None:
        """Equal temperatures should produce delta_t = 0.0."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        analyzer.update(max_cell_t=30.0, outlet_temp=30.0, fan_speed_pct=0.0)
        result: dict = analyzer.get_current()
        assert result["delta_t"] == 0.0

    def test_update_overwrites_previous(self) -> None:
        """Second update should overwrite delta_t from the first update."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        analyzer.update(max_cell_t=40.0, outlet_temp=20.0, fan_speed_pct=50.0)
        analyzer.update(max_cell_t=35.0, outlet_temp=28.0, fan_speed_pct=60.0)
        result: dict = analyzer.get_current()
        assert result["delta_t"] == 7.0
        assert result["max_cell_t"] == 35.0
        assert result["outlet_temp"] == 28.0


class TestThermalAnalyzerFanDuty:
    """Tests for fan duty score as rolling average of fan_speed_pct."""

    def test_single_fan_reading(self) -> None:
        """Single update should give fan_duty_score equal to that reading."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        analyzer.update(max_cell_t=30.0, outlet_temp=25.0, fan_speed_pct=75.0)
        result: dict = analyzer.get_current()
        assert result["fan_duty_score"] == 75.0

    def test_fan_duty_score_average(self) -> None:
        """Multiple updates should average all fan_speed_pct samples correctly."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        fan_values: list[float] = [40.0, 60.0, 80.0, 100.0]
        for v in fan_values:
            analyzer.update(max_cell_t=30.0, outlet_temp=25.0, fan_speed_pct=v)
        result: dict = analyzer.get_current()
        expected_avg: float = sum(fan_values) / len(fan_values)
        assert result["fan_duty_score"] == expected_avg

    def test_rolling_window(self) -> None:
        """More than maxlen samples should drop oldest, keeping only the most recent maxlen."""
        # Use a small maxlen to make the test fast: we'll test by verifying
        # that ThermalAnalyzer accepts a maxlen parameter for testing.
        analyzer: ThermalAnalyzer = ThermalAnalyzer(maxlen=3)
        # Push 5 samples — first 2 should be dropped
        samples: list[float] = [10.0, 20.0, 30.0, 40.0, 50.0]
        for s in samples:
            analyzer.update(max_cell_t=30.0, outlet_temp=25.0, fan_speed_pct=s)
        result: dict = analyzer.get_current()
        # Only last 3 samples kept: [30.0, 40.0, 50.0]
        expected_avg: float = (30.0 + 40.0 + 50.0) / 3.0
        assert result["fan_duty_score"] == expected_avg

    def test_zero_fan_duty(self) -> None:
        """Fan off (0%) is a valid reading and should be averaged normally."""
        analyzer: ThermalAnalyzer = ThermalAnalyzer()
        analyzer.update(max_cell_t=30.0, outlet_temp=25.0, fan_speed_pct=0.0)
        analyzer.update(max_cell_t=30.0, outlet_temp=25.0, fan_speed_pct=0.0)
        result: dict = analyzer.get_current()
        assert result["fan_duty_score"] == 0.0
