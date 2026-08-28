"""PcsAnalyzer — AC/DC power conversion efficiency tracking.

Computes instantaneous and rolling-average efficiency of the Power Conversion
System by comparing AC-side power (active_power) with DC-side power
(dc_voltage * dc_current).

Idle states (abs power below IDLE_THRESHOLD_W) are skipped to avoid dividing
by noise and polluting the rolling average with meaningless samples.
"""

from __future__ import annotations

import collections


# Minimum power on either AC or DC side to consider the PCS active.
IDLE_THRESHOLD_W: float = 500.0

# Rolling window length (3600 samples at 1 Hz = 1 hour)
_ROLLING_WINDOW: int = 3600


class PcsAnalyzer:
    """Tracks PCS AC/DC conversion efficiency at 1 Hz.

    Discharge efficiency (active_power > 0): useful output (AC) / input (DC)
    Charge efficiency  (active_power < 0): useful output (DC) / input (AC)

    Values are clamped to [0.0, 100.0] to guard against measurement noise
    that could otherwise produce values slightly above 100%.
    """

    def __init__(self) -> None:
        """Initialise the PCS efficiency analyzer."""
        # Most recent valid efficiency sample, or None if last sample was idle
        self._last_efficiency: float | None = None

        # Rolling deque of valid efficiency samples (1-hour window at 1 Hz)
        self._samples: collections.deque[float] = collections.deque(
            maxlen=_ROLLING_WINDOW
        )

        # Total count of valid (non-idle) samples seen so far
        self._sample_count: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(
        self,
        active_power: float,
        dc_voltage: float,
        dc_current: float,
    ) -> None:
        """Process one telemetry sample from the PCS ZMQ payload.

        Args:
            active_power: AC-side real power in Watts (positive = discharge,
                          negative = charge).
            dc_voltage: DC bus voltage in Volts.
            dc_current: DC bus current in Amperes.
        """
        dc_power: float = dc_voltage * dc_current

        # Skip idle states to avoid meaningless efficiency calculations
        if (
            abs(active_power) < IDLE_THRESHOLD_W
            or abs(dc_power) < IDLE_THRESHOLD_W
        ):
            self._last_efficiency = None
            return

        efficiency: float
        if active_power > 0.0:
            # Discharge: useful output = AC power, input = DC power
            efficiency = abs(active_power) / abs(dc_power) * 100.0
        else:
            # Charge: useful output = DC power (stored energy), input = AC grid
            efficiency = abs(dc_power) / abs(active_power) * 100.0

        # Clamp to [0, 100] to handle measurement noise
        efficiency = max(0.0, min(100.0, efficiency))

        self._last_efficiency = efficiency
        self._samples.append(efficiency)
        self._sample_count += 1

    def get_current(self) -> dict:
        """Return the current efficiency state.

        Returns:
            Dict with keys:
                efficiency_pct  — most recent valid efficiency or None (idle)
                sample_count    — total non-idle samples processed
                avg_efficiency_pct — rolling average over last 3600 samples,
                                     or None if no valid samples yet
        """
        avg: float | None = None
        if self._samples:
            avg = sum(self._samples) / len(self._samples)

        return {
            "efficiency_pct": self._last_efficiency,
            "sample_count": self._sample_count,
            "avg_efficiency_pct": avg,
        }
