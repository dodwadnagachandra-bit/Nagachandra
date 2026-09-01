"""Pure schedule evaluation functions.

No ZMQ, no async -- these functions take time + config as arguments and
return dataclass results. Ideal for unit testing.

Sign convention: positive = discharge (kW export), negative = charge (kW import).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WindowResult:
    """Result of time-of-day window evaluation."""

    index: int | None
    """Index of matched window, None if no match."""

    action: str
    """'charge', 'discharge', or 'idle'."""

    power_kw: float
    """Signed: negative=charge, positive=discharge, 0=idle."""


@dataclass(frozen=True, slots=True)
class CurveResult:
    """Result of curve evaluation."""

    index: int
    """0-95 curve index."""

    power_kw: float
    """Signed value from power_curve array."""


def parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' string to (hour, minute).

    Args:
        time_str: Time string in HH:MM 24-hour format.

    Returns:
        Tuple of (hour, minute).
    """
    parts: list[str] = time_str.split(":")
    return int(parts[0]), int(parts[1])


def _time_to_minutes(hour: int, minute: int) -> int:
    """Convert (hour, minute) to minutes since midnight."""
    return hour * 60 + minute


def _in_window(now_min: int, start_min: int, end_min: int) -> bool:
    """Check if now_min is in half-open interval [start, end) with midnight wrapping."""
    if start_min < end_min:
        # Normal window: e.g., 06:00-18:00
        return start_min <= now_min < end_min
    else:
        # Wraps midnight: e.g., 22:00-06:00 => now >= 22:00 OR now < 06:00
        return now_min >= start_min or now_min < end_min


def evaluate_time_of_day(
    hour: int,
    minute: int,
    time_windows: list[dict[str, Any]],
) -> WindowResult:
    """Find matching window for current time. First match wins.

    Uses half-open intervals [start, end). Handles midnight-wrapping windows
    (start > end). Returns idle with power_kw=0 when outside all windows.

    For charge action: negate power_kw (negative=charge per convention).
    For discharge action: keep power_kw positive.
    For idle action: power_kw=0.

    Args:
        hour: Current hour (0-23).
        minute: Current minute (0-59).
        time_windows: List of window dicts with start, end, action, power_kw.

    Returns:
        WindowResult with matched window info or idle fallback.
    """
    now_min: int = _time_to_minutes(hour, minute)

    for i, window in enumerate(time_windows):
        start_h, start_m = parse_time(window["start"])
        end_h, end_m = parse_time(window["end"])
        start_min: int = _time_to_minutes(start_h, start_m)
        end_min: int = _time_to_minutes(end_h, end_m)

        if _in_window(now_min, start_min, end_min):
            action: str = window["action"]
            raw_power: float = float(window["power_kw"])

            if action == "charge":
                signed_power: float = -abs(raw_power)
            elif action == "discharge":
                signed_power = abs(raw_power)
            else:  # idle
                signed_power = 0.0

            return WindowResult(index=i, action=action, power_kw=signed_power)

    # Outside all windows
    return WindowResult(index=None, action="idle", power_kw=0.0)


def evaluate_curve(
    hour: int,
    minute: int,
    power_curve: list[float],
) -> CurveResult:
    """Calculate 96-point index and return CurveResult with signed value.

    Index formula: hour * 4 + minute // 15 (0-95).
    Values are passed through as-is (positive=discharge, negative=charge).

    Args:
        hour: Current hour (0-23).
        minute: Current minute (0-59).
        power_curve: List of 96 power values in kW.

    Returns:
        CurveResult with index and power value.
    """
    index: int = hour * 4 + minute // 15
    return CurveResult(index=index, power_kw=float(power_curve[index]))


def evaluate_day_night(
    hour: int,
    minute: int,
    day_night: dict[str, str],
) -> str:
    """Return 'day' or 'night' based on current time and day_night config.

    If current time >= day_start and < night_start -> 'day', else 'night'.

    Args:
        hour: Current hour (0-23).
        minute: Current minute (0-59).
        day_night: Dict with 'day_start' and 'night_start' as HH:MM strings.

    Returns:
        'day' or 'night'.
    """
    now_min: int = _time_to_minutes(hour, minute)

    day_h, day_m = parse_time(day_night["day_start"])
    night_h, night_m = parse_time(day_night["night_start"])

    day_start_min: int = _time_to_minutes(day_h, day_m)
    night_start_min: int = _time_to_minutes(night_h, night_m)

    if day_start_min <= now_min < night_start_min:
        return "day"
    return "night"
