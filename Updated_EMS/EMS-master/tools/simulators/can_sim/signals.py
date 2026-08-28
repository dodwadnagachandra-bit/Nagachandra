"""Signal data generation with sinusoidal drift and Gaussian noise."""

from __future__ import annotations

import math
import random
import time


class SignalGenerator:
    """Generates realistic BMS signal values for one rack."""

    def __init__(self, rack_index: int, cluster_index: int = 0, tuning: dict[str, float] | None = None) -> None:
        self.rack_index: int = rack_index
        self.cluster_index: int = cluster_index
        self.rack_offset: float = (cluster_index * 16 + rack_index) * 0.02
        self.start_time: float = time.monotonic()

        # Signal tuning -- defaults match original hardcoded values
        t: dict[str, float] = tuning or {}
        self.noise_sigma: float = t.get("noise_sigma", 0.005)
        self.drift_amplitude: float = t.get("drift_amplitude", 0.15)
        self.drift_period_s: float = t.get("drift_period_s", 60.0)
        self.base_voltage: float = t.get("base_voltage", 3.35)

    def _elapsed(self) -> float:
        return time.monotonic() - self.start_time

    def cell_voltage(self, cell_idx: int) -> float:
        """Cell voltage with sinusoidal drift + Gaussian noise. Range [2.5, 4.2]V."""
        elapsed: float = self._elapsed()
        base: float = self.base_voltage + self.rack_offset
        drift: float = self.drift_amplitude * math.sin(2 * math.pi * elapsed / self.drift_period_s + cell_idx * 0.1)
        noise: float = random.gauss(0, self.noise_sigma)
        return max(2.5, min(4.2, base + drift + noise))

    def cell_temperature(self, temp_idx: int) -> float:
        """Cell temperature with slow drift + noise. Range [-10, 60] degC."""
        elapsed: float = self._elapsed()
        base: float = 32.0 + self.rack_offset * 50
        drift: float = 5.0 * math.sin(2 * math.pi * elapsed / 120.0 + temp_idx * 0.2)
        noise: float = random.gauss(0, 0.5)
        return max(-10.0, min(60.0, base + drift + noise))

    def pack_voltage(self) -> float:
        """Pack voltage: ~52V with slow sinusoidal drift."""
        elapsed: float = self._elapsed()
        return 52.0 + 2.0 * math.sin(elapsed / 60.0) + self.rack_offset * 10

    def pack_current(self) -> float:
        """Pack current: charge/discharge cycling +-50A."""
        elapsed: float = self._elapsed()
        return 50.0 * math.sin(2 * math.pi * elapsed / 60.0)

    def pack_soc(self) -> float:
        """SOC: ramp 20% -> 80% -> 20% over 600s cycle. Range [0, 100]%."""
        elapsed: float = self._elapsed()
        cycle: float = elapsed % 600.0
        if cycle < 300.0:
            soc: float = 20.0 + 60.0 * (cycle / 300.0)
        else:
            soc = 80.0 - 60.0 * ((cycle - 300.0) / 300.0)
        return max(0.0, min(100.0, soc))

    def pack_soh(self) -> float:
        """SOH: static 98%."""
        return 98.0

    def min_max_avg_cell_v(self, num_cells: int = 16) -> tuple[float, float, float]:
        """Generate num_cells voltages and return (min, max, mean)."""
        voltages: list[float] = [self.cell_voltage(i) for i in range(num_cells)]
        return min(voltages), max(voltages), sum(voltages) / len(voltages)

    def min_max_avg_cell_t(self, num_temps: int = 8) -> tuple[float, float, float]:
        """Generate num_temps temperatures and return (min, max, mean)."""
        temps: list[float] = [self.cell_temperature(i) for i in range(num_temps)]
        return min(temps), max(temps), sum(temps) / len(temps)
