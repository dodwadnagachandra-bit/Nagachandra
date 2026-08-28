"""ControlIntelligence — pure decision logic for EMS control_manager.

Implements source priority dispatch, SOC limits, temperature derating,
power ramping, interlock guards, and alarm protection response.

This module is pure logic with no I/O: no RTDB access, no ZMQ, no asyncio.
ControlLoop._tick() calls evaluate() once per 1Hz cycle after reading RTDB
and before writing RTDB.

Follows the same pure-logic pattern as ControlStateMachine from Phase 14.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from ems_control_manager.state_machine import (
    PCS_STATE_RUNNING,
    STATE_CHARGING,
    STATE_DISCHARGING,
    STATE_FAULT,
)

# ---------------------------------------------------------------------------
# IntelligenceResult — all outputs of a single evaluate() call
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class IntelligenceResult:
    """Outputs of a single ControlIntelligence.evaluate() call.

    Callers (ControlLoop) use these fields to:
    - Write active_derating_pct to RTDB system section
    - Write active_source to RTDB system section
    - Force IDLE if soc_cutoff_hit is True
    - Block dispatch if interlock_blocked is True
    - Enter FAULT if protection_active is True
    """

    effective_max_power_kw: float    # max_power_kw * active_derating_pct / 100
    desired_setpoint_kw: float       # ramped setpoint (may be reduced by alarm action)
    active_derating_pct: float       # 0.0-100.0 where 100=full power
    active_source: str               # "solar" | "grid" | "bess" | "dg" | "manual" | "none"
    soc_cutoff_hit: bool             # True → caller should force IDLE
    interlock_blocked: bool          # True → caller should block dispatch
    protection_active: bool          # True → caller should enter FAULT


# ---------------------------------------------------------------------------
# ControlIntelligence
# ---------------------------------------------------------------------------


class ControlIntelligence:
    """Pure decision intelligence for EMS control_manager.

    Encapsulates five concerns:
    1. Source priority waterfall (CTRL-04)
    2. SOC limit guards (CTRL-05)
    3. Temperature derating (CTRL-06)
    4. Power setpoint ramping (CTRL-08)
    5. Interlock guards (CTRL-09)

    Plus alarm protection response (for Plan 16-02 alarm integration).

    Call evaluate() once per 1Hz tick. Call update_config() on hot-reload.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise from a validated control_config dict.

        Args:
            config: Full control_config dict with keys: soc_limits, power_limits,
                    source_priority, derating, ramping. Additional keys are ignored.
        """
        self._soc_limits: dict[str, Any] = config["soc_limits"]
        self._power_limits: dict[str, Any] = config["power_limits"]
        self._source_priority: dict[str, Any] = config["source_priority"]
        self._derating: dict[str, Any] = config["derating"]
        self._ramping: dict[str, Any] = config["ramping"]

        # Ramp state: previous setpoint and timestamp for delta_t computation
        self._last_setpoint_kw: float = 0.0
        self._last_tick_s: float | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update_config(self, new_config: dict[str, Any]) -> None:
        """Atomically replace all config sections from a new control_config dict.

        Called by ControlLoop on hot-reload. Replaces all section references
        atomically — no partial application risk.

        Args:
            new_config: Full validated control_config dict.
        """
        self._soc_limits = new_config["soc_limits"]
        self._power_limits = new_config["power_limits"]
        self._source_priority = new_config["source_priority"]
        self._derating = new_config["derating"]
        self._ramping = new_config["ramping"]

    def evaluate(
        self,
        now_s: float,
        raw_setpoint_kw: float,
        pcs_state: int,
        safety_emergency: bool,
        gpio_di: list[int],
        total_soc: float,
        bms_max_cell_t: float,
        bms_min_cell_t: float,
        pcs_temp_c: float,
        alarm_severity: str | None,
        current_state: int,
        mode: str,
        solar_available: bool,
    ) -> IntelligenceResult:
        """Execute one tick of intelligence evaluation.

        All inputs are caller-provided sensor values. Returns IntelligenceResult
        with all derived decisions. Same inputs always produce same outputs
        (pure function except for ramp state and last_tick_s).

        Args:
            now_s: Current monotonic time in seconds (time.monotonic()).
            raw_setpoint_kw: Desired power from state machine / operator.
                             Positive = charging, negative = discharging.
            pcs_state: Current PCS state (ems_pcs_state_t int).
            safety_emergency: True if safety_manager asserts emergency.
            gpio_di: RTDB gpio.di[0..7] digital inputs. DI-0 = ACDB feedback.
            total_soc: System total state of charge (%).
            bms_max_cell_t: Maximum cell temperature across all online racks (°C).
            bms_min_cell_t: Minimum cell temperature across all online racks (°C).
            pcs_temp_c: PCS inverter internal temperature (°C).
            alarm_severity: Highest active alarm severity this tick, or None.
                            One of: "warning", "action", "protection", None.
            current_state: Current control state (ems_control_state_t int).
            mode: Dispatch mode: "DAY" | "NIGHT" | "MANUAL".
            solar_available: True if PV power is above threshold (caller checks).

        Returns:
            IntelligenceResult with all decision outputs for this tick.
        """
        # 1. Source priority
        active_source: str = self._evaluate_source_priority(
            mode=mode,
            solar_available=solar_available,
            gpio_di=gpio_di,
            total_soc=total_soc,
            current_state=current_state,
        )

        # 2. SOC limit check
        soc_cutoff_hit: bool = self._check_soc_limits(
            total_soc=total_soc,
            current_state=current_state,
        )

        # 3. Temperature derating
        active_derating_pct: float = self._compute_derating(
            bms_max_cell_t=bms_max_cell_t,
            bms_min_cell_t=bms_min_cell_t,
            pcs_temp_c=pcs_temp_c,
        )

        # effective_max_power_kw uses max_charge_kw (symmetric in this implementation;
        # can be split for charge/discharge asymmetry in future)
        max_power_kw: float = float(self._power_limits["max_charge_kw"])
        effective_max_power_kw: float = max_power_kw * active_derating_pct / 100.0

        # 4. Alarm severity response (before ramping)
        adjusted_setpoint_kw: float = raw_setpoint_kw
        protection_active: bool = False
        if alarm_severity == "protection":
            protection_active = True
        elif alarm_severity == "action":
            adjusted_setpoint_kw = raw_setpoint_kw * 0.5

        # 5. Power ramping — limit rate of setpoint change
        desired_setpoint_kw: float = self._apply_ramp(
            now_s=now_s,
            desired_kw=adjusted_setpoint_kw,
        )

        # 6. Interlock check
        interlock_blocked: bool = self._check_interlocks(
            pcs_state=pcs_state,
            safety_emergency=safety_emergency,
        )

        return IntelligenceResult(
            effective_max_power_kw=effective_max_power_kw,
            desired_setpoint_kw=desired_setpoint_kw,
            active_derating_pct=active_derating_pct,
            active_source=active_source,
            soc_cutoff_hit=soc_cutoff_hit,
            interlock_blocked=interlock_blocked,
            protection_active=protection_active,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_source_priority(
        self,
        mode: str,
        solar_available: bool,
        gpio_di: list[int],
        total_soc: float,
        current_state: int,
    ) -> str:
        """Waterfall source priority selection.

        Args:
            mode: "DAY" | "NIGHT" | "MANUAL"
            solar_available: True if PV power > threshold (caller checks)
            gpio_di: Digital inputs; DI-0=1 means grid (ACDB) available
            total_soc: System SOC (%)
            current_state: Current control state

        Returns:
            Source string: "solar" | "grid" | "bess" | "dg" | "manual" | "none"
        """
        if mode == "MANUAL":
            return "manual"

        # Build availability map for this tick
        grid_available: bool = len(gpio_di) > 0 and gpio_di[0] == 1
        bess_available: bool = (
            total_soc > float(self._soc_limits["discharge_cutoff_pct"])
            and current_state != STATE_FAULT
        )
        # DG always False in M2 — no DG control protocol yet
        dg_available: bool = False

        availability: dict[str, bool] = {
            "solar": solar_available,
            "grid": grid_available,
            "bess": bess_available,
            "dg": dg_available,
        }

        # Select priority order based on mode
        if mode == "DAY":
            order: list[str] = list(self._source_priority["day_order"])
        else:
            # NIGHT or any unrecognised mode falls back to night order
            order = list(self._source_priority["night_order"])

        # Waterfall: return first available source
        for source in order:
            if availability.get(source, False):
                return source

        return "none"

    def _check_soc_limits(
        self,
        total_soc: float,
        current_state: int,
    ) -> bool:
        """Check if SOC limits have been breached for the current state.

        Args:
            total_soc: System SOC (%)
            current_state: Current control state

        Returns:
            True if SOC cutoff is hit (caller should force IDLE).
        """
        charge_cutoff: float = float(self._soc_limits["charge_cutoff_pct"])
        discharge_cutoff: float = float(self._soc_limits["discharge_cutoff_pct"])

        if current_state == STATE_CHARGING and total_soc >= charge_cutoff:
            return True

        if current_state == STATE_DISCHARGING and total_soc <= discharge_cutoff:
            return True

        return False

    def _compute_derating(
        self,
        bms_max_cell_t: float,
        bms_min_cell_t: float,
        pcs_temp_c: float,
    ) -> float:
        """Compute active derating percentage across all thermal zones.

        Three zones evaluated independently; most restrictive (minimum) wins.
        Returns a percentage in [0.0, 100.0] where 100 = full power.

        Args:
            bms_max_cell_t: Maximum BMS cell temperature across all online racks (°C).
            bms_min_cell_t: Minimum BMS cell temperature across all online racks (°C).
            pcs_temp_c: PCS inverter internal temperature (°C).

        Returns:
            Active derating percentage (0.0 = full cutoff, 100.0 = no derating).
        """
        cfg: dict[str, Any] = self._derating

        # BMS high-temp zone (hot → derate)
        bms_high_factor: float = _zone_factor(
            signal=bms_max_cell_t,
            start=float(cfg["bms_high_start_c"]),
            cutoff=float(cfg["bms_high_cutoff_c"]),
        )

        # PCS high-temp zone (hot → derate)
        pcs_high_factor: float = _zone_factor(
            signal=pcs_temp_c,
            start=float(cfg["pcs_high_start_c"]),
            cutoff=float(cfg["pcs_high_cutoff_c"]),
        )

        # BMS cold zone (cold → derate, inverted)
        bms_cold_factor: float = _zone_factor_low(
            signal=bms_min_cell_t,
            start=float(cfg["bms_low_start_c"]),
            cutoff=float(cfg["bms_low_cutoff_c"]),
        )

        # Most restrictive wins
        return min(bms_high_factor, pcs_high_factor, bms_cold_factor)

    def _apply_ramp(
        self,
        now_s: float,
        desired_kw: float,
    ) -> float:
        """Limit setpoint change rate to configured ramp_kw_s * elapsed_time.

        Uses actual elapsed time (time.monotonic() delta), not configured loop
        interval — handles jitter correctly.

        Args:
            now_s: Current monotonic time in seconds.
            desired_kw: Desired setpoint after alarm adjustment.

        Returns:
            Ramped setpoint limited to max step per elapsed time.
        """
        if self._last_tick_s is None:
            # First tick — no previous state; track current as baseline
            self._last_tick_s = now_s
            self._last_setpoint_kw = desired_kw
            return desired_kw

        delta_t_s: float = now_s - self._last_tick_s

        # Choose ramp rate based on direction (charge vs discharge)
        if desired_kw >= self._last_setpoint_kw:
            ramp_rate: float = float(self._ramping["charge_ramp_kw_s"])
        else:
            ramp_rate = float(self._ramping["discharge_ramp_kw_s"])

        max_step: float = ramp_rate * delta_t_s
        delta: float = desired_kw - self._last_setpoint_kw

        if abs(delta) <= max_step:
            ramped: float = desired_kw
        elif delta > 0:
            ramped = self._last_setpoint_kw + max_step
        else:
            ramped = self._last_setpoint_kw - max_step

        self._last_setpoint_kw = ramped
        self._last_tick_s = now_s
        return ramped

    def _check_interlocks(
        self,
        pcs_state: int,
        safety_emergency: bool,
    ) -> bool:
        """Check interlock conditions that block dispatch.

        Dispatch is blocked when:
        - safety_manager is in emergency (safety_emergency=True), OR
        - PCS is not in RUNNING state

        Args:
            pcs_state: Current PCS state (ems_pcs_state_t int).
            safety_emergency: True if safety_manager asserts emergency.

        Returns:
            True if dispatch should be blocked.
        """
        if safety_emergency:
            return True

        if pcs_state != PCS_STATE_RUNNING:
            return True

        return False


# ---------------------------------------------------------------------------
# Module-level pure helper functions for derating zones
# ---------------------------------------------------------------------------


def _zone_factor(signal: float, start: float, cutoff: float) -> float:
    """Compute derating factor for a high-side thermal zone.

    Linear ramp from 100% at signal=start to 0% at signal=cutoff.
    Returns 100% below start, 0% at or above cutoff.

    Args:
        signal: Current temperature reading (°C).
        start: Temperature at which derating begins (100% power below this).
        cutoff: Temperature at which power is zero.

    Returns:
        Derating factor as percentage [0.0, 100.0].
    """
    if signal <= start:
        return 100.0
    if signal >= cutoff:
        return 0.0
    span: float = cutoff - start
    return 100.0 - (signal - start) / span * 100.0


def _zone_factor_low(signal: float, start: float, cutoff: float) -> float:
    """Compute derating factor for a low-side (cold) thermal zone.

    Linear ramp from 100% at signal=start to 0% at signal=cutoff.
    For cold zones: signal BELOW start triggers derating.

    Args:
        signal: Current temperature reading (°C).
        start: Temperature below which derating begins (100% power above this).
        cutoff: Temperature at which power is zero (must be < start).

    Returns:
        Derating factor as percentage [0.0, 100.0].
    """
    if signal >= start:
        return 100.0
    if signal <= cutoff:
        return 0.0
    span: float = start - cutoff
    return 100.0 - (start - signal) / span * 100.0
