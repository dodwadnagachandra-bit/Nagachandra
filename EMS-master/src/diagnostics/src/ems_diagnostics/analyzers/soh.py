"""SohAnalyzer — per-rack battery State-of-Health trending and cycle detection.

Uses BMS-reported pack_soh as the primary SOH value (authoritative source).
A state-machine tracks full charge/discharge cycles to prevent double-counting.
Coulomb counting accumulates discharged Ah for cross-validation against rated
capacity.
"""

from __future__ import annotations

from enum import Enum, auto


# ---------------------------------------------------------------------------
# Internal state machine enum
# ---------------------------------------------------------------------------


class _CycleState(Enum):
    """States for full-cycle detection state machine."""

    IDLE = auto()
    CHARGED = auto()
    DISCHARGING = auto()


# SOC thresholds for cycle detection
_SOC_HIGH_THRESHOLD: float = 90.0
_SOC_LOW_THRESHOLD: float = 10.0

# Minimum discharge current to transition from CHARGED -> DISCHARGING (A)
_DISCHARGE_CURRENT_THRESHOLD: float = -0.5


# ---------------------------------------------------------------------------
# SohAnalyzer
# ---------------------------------------------------------------------------


class SohAnalyzer:
    """Tracks BMS-reported SOH and detects full charge/discharge cycles.

    One instance per rack. Intended to be updated at 1 Hz with live telemetry
    from the ZMQ bms.rack.{N} topic.

    Cycle detection uses a three-state machine:
        IDLE -> CHARGED  when pack_soc >= SOC_HIGH_THRESHOLD
        CHARGED -> DISCHARGING  when pack_i < DISCHARGE_CURRENT_THRESHOLD
        DISCHARGING -> IDLE     when pack_soc <= SOC_LOW_THRESHOLD (cycle done)

    During DISCHARGING, Coulomb counting accumulates abs(pack_i) / 3600 Ah at
    each 1 Hz sample for cross-validation against rated_capacity_ah.
    """

    def __init__(self, rack_id: int, rated_capacity_ah: float) -> None:
        """Initialise the analyzer.

        Args:
            rack_id: Rack index (1-based, matches bms.rack.{N} topic).
            rated_capacity_ah: Nameplate capacity in Ah (from BatteryConfig).
        """
        self._rack_id: int = rack_id
        self._rated_capacity_ah: float = rated_capacity_ah

        # Primary SOH from BMS
        self._soh_pct: float = 100.0

        # Cycle detection state machine
        self._state: _CycleState = _CycleState.IDLE
        self._cycle_count: int = 0

        # Coulomb counting — accumulated Ah in current discharge phase
        self._cycle_ah: float = 0.0

        # Coulomb-derived SOH from most recently completed cycle (% of rated)
        self._coulomb_soh_pct: float = 0.0

        # SOH history: list of (timestamp_ms, soh_pct) tuples
        self._history: list[tuple[int, float]] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(
        self,
        pack_i: float,
        pack_soc: float,
        pack_soh_bms: float,
    ) -> None:
        """Process one telemetry sample.

        Args:
            pack_i: Pack current in Amperes (negative = discharge).
            pack_soc: State of charge 0–100 %.
            pack_soh_bms: BMS-reported State of Health 0–100 %.
        """
        # BMS-reported SOH is the authoritative primary value (Research Pitfall 4)
        self._soh_pct = pack_soh_bms

        # Advance state machine
        self._advance_state_machine(pack_i=pack_i, pack_soc=pack_soc)

    def get_current(self) -> dict:
        """Return a snapshot of current SOH state.

        Returns:
            Dict with keys: rack_id, soh_pct, cycle_count, coulomb_soh_pct.
        """
        return {
            "rack_id": self._rack_id,
            "soh_pct": self._soh_pct,
            "cycle_count": self._cycle_count,
            "coulomb_soh_pct": self._coulomb_soh_pct,
        }

    def add_history_point(self, timestamp_ms: int) -> None:
        """Append current SOH to the history list.

        Called by the outer diagnostic loop at trend_update_s intervals.

        Args:
            timestamp_ms: Unix epoch in milliseconds.
        """
        self._history.append((timestamp_ms, self._soh_pct))

    def get_history(self) -> list[tuple[int, float]]:
        """Return the SOH history for trend queries.

        Returns:
            List of (timestamp_ms, soh_pct) tuples in insertion order.
        """
        return list(self._history)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _advance_state_machine(self, pack_i: float, pack_soc: float) -> None:
        """Run one step of the cycle-detection state machine.

        Args:
            pack_i: Pack current A (negative = discharge).
            pack_soc: State of charge %.
        """
        if self._state is _CycleState.IDLE:
            # Transition to CHARGED when SOC reaches high threshold
            if pack_soc >= _SOC_HIGH_THRESHOLD:
                self._state = _CycleState.CHARGED

        elif self._state is _CycleState.CHARGED:
            # Transition to DISCHARGING only when discharge current confirmed
            if pack_i < _DISCHARGE_CURRENT_THRESHOLD:
                self._state = _CycleState.DISCHARGING
                self._cycle_ah = 0.0  # reset coulomb accumulator
                # Accumulate for this first discharging sample
                self._cycle_ah += abs(pack_i) / 3600.0

        elif self._state is _CycleState.DISCHARGING:
            # Accumulate discharged Ah at 1 Hz (1 sample = 1 second)
            self._cycle_ah += abs(pack_i) / 3600.0

            # Cycle complete when SOC reaches low threshold
            if pack_soc <= _SOC_LOW_THRESHOLD:
                self._cycle_count += 1
                # Coulomb SOH: how much capacity was actually discharged vs rated
                if self._rated_capacity_ah > 0.0:
                    self._coulomb_soh_pct = (
                        self._cycle_ah / self._rated_capacity_ah
                    ) * 100.0
                self._cycle_ah = 0.0
                self._state = _CycleState.IDLE
