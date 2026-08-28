"""ThermalAnalyzer — BTMS cooling effectiveness and fan duty tracking.

Monitors battery thermal management by computing:
- delta_t: difference between max cell temperature and coolant outlet temperature
- fan_duty_score: rolling average of fan duty cycle percentage

The delta_t metric uses outlet_temp (not ambient_temp — no ambient sensor on BTMS).
A higher delta_t indicates the coolant is not removing heat effectively from cells.

Called at 1Hz from BMS (max_cell_t) + BTMS (outlet_temp, fan_speed_pct) telemetry.
"""

from __future__ import annotations

import collections
from statistics import mean


class ThermalAnalyzer:
    """Tracks BTMS cooling effectiveness metrics from cell and coolant data.

    Attributes:
        _delta_t: Latest cell-to-outlet temperature difference (°C), or None.
        _max_cell_t: Latest max cell temperature (°C), or None.
        _outlet_temp: Latest coolant outlet temperature (°C), or None.
        _fan_samples: Rolling deque of fan_speed_pct readings for duty score.
    """

    def __init__(self, maxlen: int = 3600) -> None:
        """Initialize ThermalAnalyzer with an empty state.

        Args:
            maxlen: Maximum number of fan speed samples to keep (default 3600
                    = 1 hour at 1 Hz update rate). Configurable for testing.
        """
        self._delta_t: float | None = None
        self._max_cell_t: float | None = None
        self._outlet_temp: float | None = None
        self._fan_samples: collections.deque[float] = collections.deque(maxlen=maxlen)

    def update(
        self,
        max_cell_t: float,
        outlet_temp: float,
        fan_speed_pct: float,
    ) -> None:
        """Process a new telemetry sample from BMS and BTMS.

        Computes delta_t = max_cell_t - outlet_temp and appends fan_speed_pct
        to the rolling fan duty deque. Called at 1 Hz.

        Args:
            max_cell_t: Highest cell temperature in the rack (°C).
            outlet_temp: Coolant outlet temperature from BTMS (°C).
                         NOTE: This is outlet_temp, not ambient_temp.
                         The BTMS hardware has no ambient temperature sensor.
            fan_speed_pct: Fan duty cycle (0–100 %).
        """
        self._delta_t = max_cell_t - outlet_temp
        self._max_cell_t = max_cell_t
        self._outlet_temp = outlet_temp
        self._fan_samples.append(fan_speed_pct)

    def get_current(self) -> dict:
        """Return the current thermal diagnostics snapshot.

        Returns:
            A dict with keys:
            - "delta_t": float | None — cell-to-outlet temp difference (°C)
            - "fan_duty_score": float | None — rolling average fan duty (%)
            - "max_cell_t": float | None — latest max cell temperature (°C)
            - "outlet_temp": float | None — latest coolant outlet temp (°C)

            All values are None until the first update() call.
        """
        fan_duty_score: float | None = (
            mean(self._fan_samples) if self._fan_samples else None
        )

        return {
            "delta_t": self._delta_t,
            "fan_duty_score": fan_duty_score,
            "max_cell_t": self._max_cell_t,
            "outlet_temp": self._outlet_temp,
        }
