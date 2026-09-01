"""PCS signal generation with sinusoidal drift and Gaussian noise."""

from __future__ import annotations

import math
import random
import time


class PCSSignalGenerator:
    """Generates realistic PCS telemetry values with configurable drift and noise.

    AC voltage and frequency drift sinusoidally around nominal values.
    Temperature rises under load. All values are clamped to physical ranges.
    """

    def __init__(
        self,
        nominal_voltage_v: float = 230.0,
        nominal_frequency_hz: float = 50.0,
        phase_count: int = 3,
    ) -> None:
        self.nominal_voltage_v: float = nominal_voltage_v
        self.nominal_frequency_hz: float = nominal_frequency_hz
        self.phase_count: int = phase_count
        self._start_time: float = time.monotonic()

    def _elapsed(self) -> float:
        """Seconds since generator was created."""
        return time.monotonic() - self._start_time

    def ac_voltage(self, phase: int) -> float:
        """AC voltage for a given phase (1-indexed). Returns 0.0 for phases > phase_count.

        Sinusoidal drift +/- 5V around nominal with Gaussian noise.
        """
        if phase < 1 or phase > self.phase_count:
            return 0.0
        elapsed: float = self._elapsed()
        drift: float = 5.0 * math.sin(2 * math.pi * elapsed / 120.0 + phase * 0.7)
        noise: float = random.gauss(0, 0.3)
        return max(0.0, self.nominal_voltage_v + drift + noise)

    def ac_frequency(self) -> float:
        """AC grid frequency with sinusoidal drift +/- 0.2 Hz and noise."""
        elapsed: float = self._elapsed()
        drift: float = 0.2 * math.sin(2 * math.pi * elapsed / 90.0)
        noise: float = random.gauss(0, 0.01)
        return max(45.0, min(55.0, self.nominal_frequency_hz + drift + noise))

    def dc_voltage(self) -> float:
        """DC bus voltage with slow drift around 400V."""
        elapsed: float = self._elapsed()
        drift: float = 5.0 * math.sin(2 * math.pi * elapsed / 180.0)
        noise: float = random.gauss(0, 0.5)
        return max(0.0, 400.0 + drift + noise)

    def dc_bus_voltage(self) -> float:
        """Internal DC bus voltage, stable around 400V with small noise."""
        noise: float = random.gauss(0, 0.2)
        return max(0.0, 400.0 + noise)

    def temperature(self, base: float, load_factor: float) -> float:
        """Temperature with load-dependent rise.

        Args:
            base: Baseline temperature in degC (e.g., 25.0 for ambient).
            load_factor: 0.0 (no load) to 1.0 (full load).

        Returns:
            Temperature in degC with noise and load-dependent offset.
        """
        elapsed: float = self._elapsed()
        drift: float = 1.0 * math.sin(2 * math.pi * elapsed / 300.0)
        load_rise: float = 20.0 * load_factor
        noise: float = random.gauss(0, 0.2)
        return base + drift + load_rise + noise
