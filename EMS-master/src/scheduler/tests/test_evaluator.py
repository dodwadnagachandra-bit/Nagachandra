"""Tests for schedule evaluator pure functions."""

from __future__ import annotations

from typing import Any

import pytest

from ems_scheduler.evaluator import (
    CurveResult,
    WindowResult,
    evaluate_curve,
    evaluate_day_night,
    evaluate_time_of_day,
    parse_time,
)


class TestParseTime:
    """Tests for parse_time helper."""

    def test_parse_time_morning(self) -> None:
        """Parse '06:00' returns (6, 0)."""
        assert parse_time("06:00") == (6, 0)

    def test_parse_time_late_night(self) -> None:
        """Parse '23:45' returns (23, 45)."""
        assert parse_time("23:45") == (23, 45)


class TestEvaluateTimeOfDay:
    """Tests for evaluate_time_of_day function."""

    def test_time_of_day_in_window(
        self, sample_time_windows: list[dict[str, Any]]
    ) -> None:
        """Time 10:00 matches window 06:00-18:00 discharge."""
        result: WindowResult = evaluate_time_of_day(10, 0, sample_time_windows)
        assert result.index == 0
        assert result.action == "discharge"
        assert result.power_kw == 10

    def test_time_of_day_outside_windows(
        self, sample_time_windows: list[dict[str, Any]]
    ) -> None:
        """Time 19:00 outside all windows returns idle."""
        result: WindowResult = evaluate_time_of_day(19, 0, sample_time_windows)
        assert result.index is None
        assert result.action == "idle"
        assert result.power_kw == 0

    def test_time_of_day_midnight_wrap(
        self, sample_time_windows: list[dict[str, Any]]
    ) -> None:
        """Time 23:00 matches window 22:00-06:00 (midnight wrapping)."""
        result: WindowResult = evaluate_time_of_day(23, 0, sample_time_windows)
        assert result.index == 1
        assert result.action == "charge"
        assert result.power_kw == -15  # charge is negative

    def test_time_of_day_midnight_wrap_early(
        self, sample_time_windows: list[dict[str, Any]]
    ) -> None:
        """Time 03:00 matches window 22:00-06:00 (early morning side)."""
        result: WindowResult = evaluate_time_of_day(3, 0, sample_time_windows)
        assert result.index == 1
        assert result.action == "charge"
        assert result.power_kw == -15  # charge is negative

    def test_time_of_day_boundary_start_inclusive(
        self, sample_time_windows: list[dict[str, Any]]
    ) -> None:
        """Time exactly at window start is included."""
        result: WindowResult = evaluate_time_of_day(6, 0, sample_time_windows)
        assert result.index == 0
        assert result.action == "discharge"

    def test_time_of_day_boundary_end_exclusive(
        self, sample_time_windows: list[dict[str, Any]]
    ) -> None:
        """Time exactly at window end is excluded."""
        # 18:00 is the end of the first window -- should NOT match window 0
        # But 18:00 is also outside window 1 (22:00-06:00)
        result: WindowResult = evaluate_time_of_day(18, 0, sample_time_windows)
        assert result.index is None
        assert result.action == "idle"

    def test_time_of_day_first_match_wins(self) -> None:
        """Overlapping windows: first in list wins."""
        overlapping: list[dict[str, Any]] = [
            {"start": "08:00", "end": "12:00", "action": "discharge", "power_kw": 5},
            {"start": "10:00", "end": "14:00", "action": "charge", "power_kw": 10},
        ]
        # 10:00 matches both -- first should win
        result: WindowResult = evaluate_time_of_day(10, 0, overlapping)
        assert result.index == 0
        assert result.action == "discharge"
        assert result.power_kw == 5


class TestEvaluateCurve:
    """Tests for evaluate_curve function."""

    def test_curve_index_midnight(
        self, sample_power_curve: list[float]
    ) -> None:
        """hour=0, minute=0 -> index=0."""
        result: CurveResult = evaluate_curve(0, 0, sample_power_curve)
        assert result.index == 0
        assert result.power_kw == 5.0

    def test_curve_index_midday(
        self, sample_power_curve: list[float]
    ) -> None:
        """hour=12, minute=30 -> index=50."""
        result: CurveResult = evaluate_curve(12, 30, sample_power_curve)
        assert result.index == 50
        assert result.power_kw == -10.0

    def test_curve_index_end_of_day(
        self, sample_power_curve: list[float]
    ) -> None:
        """hour=23, minute=45 -> index=95."""
        result: CurveResult = evaluate_curve(23, 45, sample_power_curve)
        assert result.index == 95
        assert result.power_kw == 3.0

    def test_curve_returns_signed_value(self) -> None:
        """Positive=discharge, negative=charge, zero=idle (pass-through)."""
        curve: list[float] = [0.0] * 96
        curve[0] = 50.0    # positive = discharge
        curve[1] = -25.0   # negative = charge
        curve[2] = 0.0     # zero = idle

        assert evaluate_curve(0, 0, curve).power_kw == 50.0
        assert evaluate_curve(0, 15, curve).power_kw == -25.0
        assert evaluate_curve(0, 30, curve).power_kw == 0.0


class TestEvaluateDayNight:
    """Tests for evaluate_day_night function."""

    def test_day_night_daytime(self) -> None:
        """Time 10:00 with day_start=06:00 night_start=18:00 returns 'day'."""
        day_night: dict[str, str] = {
            "day_start": "06:00",
            "night_start": "18:00",
        }
        assert evaluate_day_night(10, 0, day_night) == "day"

    def test_day_night_nighttime(self) -> None:
        """Time 20:00 returns 'night'."""
        day_night: dict[str, str] = {
            "day_start": "06:00",
            "night_start": "18:00",
        }
        assert evaluate_day_night(20, 0, day_night) == "night"

    def test_day_night_at_boundary(self) -> None:
        """Time at day_start returns 'day'; time at night_start returns 'night'."""
        day_night: dict[str, str] = {
            "day_start": "06:00",
            "night_start": "18:00",
        }
        assert evaluate_day_night(6, 0, day_night) == "day"
        assert evaluate_day_night(18, 0, day_night) == "night"
