"""Unit tests for ControlIntelligence pure logic class.

Covers CTRL-04 (source priority), CTRL-05 (SOC limits), CTRL-06 (temperature
derating), CTRL-08 (power ramping), CTRL-09 (interlock guards), and alarm
protection severity handling.

All tests use pure in-memory inputs — no RTDB, no ZMQ, no asyncio.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from ems_control_manager.intelligence import ControlIntelligence, IntelligenceResult
from ems_control_manager.state_machine import (
    PCS_STATE_FAULT,
    PCS_STATE_OFF,
    PCS_STATE_RUNNING,
    PCS_STATE_STANDBY,
    STATE_CHARGING,
    STATE_DISCHARGING,
    STATE_FAULT,
    STATE_IDLE,
    STATE_STANDBY,
)

# ---------------------------------------------------------------------------
# Shared config fixture
# ---------------------------------------------------------------------------

_BASE_CONFIG: dict[str, Any] = {
    "soc_limits": {
        "charge_cutoff_pct": 95,
        "discharge_cutoff_pct": 10,
    },
    "power_limits": {
        "max_charge_kw": 25.0,
        "max_discharge_kw": 25.0,
    },
    "source_priority": {
        "day_order": ["solar", "grid", "bess", "dg"],
        "night_order": ["grid", "bess", "dg"],
    },
    "derating": {
        "bms_high_start_c": 40.0,
        "bms_high_cutoff_c": 50.0,
        "pcs_high_start_c": 65.0,
        "pcs_high_cutoff_c": 80.0,
        "bms_low_start_c": 5.0,
        "bms_low_cutoff_c": 0.0,
    },
    "ramping": {
        "charge_ramp_kw_s": 5.0,
        "discharge_ramp_kw_s": 5.0,
    },
}

# Stable eval kwargs for tests that only care about one concern
_NORMAL_TEMPS: dict[str, float] = {
    "bms_max_cell_t": 25.0,
    "bms_min_cell_t": 20.0,
    "pcs_temp_c": 40.0,
}

# Safe GPIO: DI-0=1 means grid available
_GRID_AVAILABLE_DI: list[int] = [1, 0, 0, 0, 0, 0, 0, 0]
_NO_GRID_DI: list[int] = [0, 0, 0, 0, 0, 0, 0, 0]


def _make_intelligence(config: dict[str, Any] | None = None) -> ControlIntelligence:
    return ControlIntelligence(config or _BASE_CONFIG)


def _evaluate(
    ci: ControlIntelligence,
    *,
    now_s: float = 1000.0,
    raw_setpoint_kw: float = 10.0,
    pcs_state: int = PCS_STATE_RUNNING,
    safety_emergency: bool = False,
    gpio_di: list[int] | None = None,
    total_soc: float = 50.0,
    bms_max_cell_t: float = 25.0,
    bms_min_cell_t: float = 20.0,
    pcs_temp_c: float = 40.0,
    alarm_severity: str | None = None,
    current_state: int = STATE_STANDBY,
    mode: str = "DAY",
    solar_available: bool = False,
) -> IntelligenceResult:
    return ci.evaluate(
        now_s=now_s,
        raw_setpoint_kw=raw_setpoint_kw,
        pcs_state=pcs_state,
        safety_emergency=safety_emergency,
        gpio_di=gpio_di if gpio_di is not None else _GRID_AVAILABLE_DI,
        total_soc=total_soc,
        bms_max_cell_t=bms_max_cell_t,
        bms_min_cell_t=bms_min_cell_t,
        pcs_temp_c=pcs_temp_c,
        alarm_severity=alarm_severity,
        current_state=current_state,
        mode=mode,
        solar_available=solar_available,
    )


# ---------------------------------------------------------------------------
# IntelligenceResult structure
# ---------------------------------------------------------------------------


class TestIntelligenceResultStructure:
    def test_result_has_expected_fields(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(ci)
        assert hasattr(r, "effective_max_power_kw")
        assert hasattr(r, "desired_setpoint_kw")
        assert hasattr(r, "active_derating_pct")
        assert hasattr(r, "active_source")
        assert hasattr(r, "soc_cutoff_hit")
        assert hasattr(r, "interlock_blocked")
        assert hasattr(r, "protection_active")

    def test_result_normal_operation_types(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(ci, gpio_di=_GRID_AVAILABLE_DI)
        assert isinstance(r.effective_max_power_kw, float)
        assert isinstance(r.desired_setpoint_kw, float)
        assert isinstance(r.active_derating_pct, float)
        assert isinstance(r.active_source, str)
        assert isinstance(r.soc_cutoff_hit, bool)
        assert isinstance(r.interlock_blocked, bool)
        assert isinstance(r.protection_active, bool)


# ---------------------------------------------------------------------------
# CTRL-04: Source Priority — DAY mode
# ---------------------------------------------------------------------------


class TestSourcePriorityDayMode:
    def test_day_solar_first_when_solar_available(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, mode="DAY", solar_available=True, gpio_di=_GRID_AVAILABLE_DI
        )
        assert r.active_source == "solar"

    def test_day_grid_when_solar_unavailable(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, mode="DAY", solar_available=False, gpio_di=_GRID_AVAILABLE_DI
        )
        assert r.active_source == "grid"

    def test_day_bess_when_solar_and_grid_unavailable(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="DAY",
            solar_available=False,
            gpio_di=_NO_GRID_DI,
            total_soc=50.0,  # above discharge_cutoff (10%)
            current_state=STATE_STANDBY,
        )
        assert r.active_source == "bess"

    def test_day_dg_always_false_in_m2(self) -> None:
        """DG is read-only in M2 — no dg_available field, always unavailable."""
        ci: ControlIntelligence = _make_intelligence()
        # All sources unavailable except dg (which is always False)
        r: IntelligenceResult = _evaluate(
            ci,
            mode="DAY",
            solar_available=False,
            gpio_di=_NO_GRID_DI,
            total_soc=5.0,  # at discharge_cutoff, bess unavailable
            current_state=STATE_STANDBY,
        )
        assert r.active_source == "none"

    def test_day_no_source_when_nothing_available(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="DAY",
            solar_available=False,
            gpio_di=_NO_GRID_DI,
            total_soc=9.0,  # below discharge_cutoff (10%)
        )
        assert r.active_source == "none"

    def test_day_bess_unavailable_when_in_fault(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="DAY",
            solar_available=False,
            gpio_di=_NO_GRID_DI,
            total_soc=50.0,
            current_state=STATE_FAULT,
        )
        # BESS not available in FAULT state; DG always False → none
        assert r.active_source == "none"

    def test_day_solar_prioritized_over_bess_even_if_bess_available(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="DAY",
            solar_available=True,
            gpio_di=_NO_GRID_DI,
            total_soc=50.0,
            current_state=STATE_STANDBY,
        )
        assert r.active_source == "solar"


# ---------------------------------------------------------------------------
# CTRL-04: Source Priority — NIGHT mode
# ---------------------------------------------------------------------------


class TestSourcePriorityNightMode:
    def test_night_grid_first(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, mode="NIGHT", gpio_di=_GRID_AVAILABLE_DI
        )
        assert r.active_source == "grid"

    def test_night_bess_when_grid_unavailable(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="NIGHT",
            gpio_di=_NO_GRID_DI,
            total_soc=50.0,
            current_state=STATE_STANDBY,
        )
        assert r.active_source == "bess"

    def test_night_none_when_grid_and_bess_unavailable(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="NIGHT",
            gpio_di=_NO_GRID_DI,
            total_soc=5.0,  # at discharge_cutoff
        )
        assert r.active_source == "none"

    def test_night_solar_not_checked(self) -> None:
        """solar_available has no effect in NIGHT mode (not in night_order array)."""
        ci: ControlIntelligence = _make_intelligence()
        # Even with solar=True, night mode uses night_order (no solar)
        r: IntelligenceResult = _evaluate(
            ci,
            mode="NIGHT",
            solar_available=True,
            gpio_di=_NO_GRID_DI,
            total_soc=50.0,
            current_state=STATE_STANDBY,
        )
        # Grid not available (DI-0=0), BESS available (SOC=50 > cutoff)
        assert r.active_source == "bess"


# ---------------------------------------------------------------------------
# CTRL-04: Source Priority — MANUAL mode
# ---------------------------------------------------------------------------


class TestSourcePriorityManualMode:
    def test_manual_returns_manual_source(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(ci, mode="MANUAL")
        assert r.active_source == "manual"

    def test_manual_bypasses_grid_check(self) -> None:
        """Manual mode doesn't check GPIO DI-0."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, mode="MANUAL", gpio_di=_NO_GRID_DI
        )
        assert r.active_source == "manual"

    def test_manual_bypasses_soc_check_for_source(self) -> None:
        """Manual mode doesn't check BESS SOC for source selection."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, mode="MANUAL", total_soc=1.0, gpio_di=_NO_GRID_DI
        )
        assert r.active_source == "manual"


# ---------------------------------------------------------------------------
# CTRL-05: SOC Limits
# ---------------------------------------------------------------------------


class TestSocLimits:
    def test_soc_within_range_no_cutoff(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=50.0, current_state=STATE_CHARGING
        )
        assert r.soc_cutoff_hit is False

    def test_charge_cutoff_at_exact_threshold(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=95.0, current_state=STATE_CHARGING
        )
        assert r.soc_cutoff_hit is True

    def test_charge_cutoff_above_threshold(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=97.0, current_state=STATE_CHARGING
        )
        assert r.soc_cutoff_hit is True

    def test_charge_cutoff_just_below_threshold(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=94.9, current_state=STATE_CHARGING
        )
        assert r.soc_cutoff_hit is False

    def test_discharge_cutoff_at_exact_threshold(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=10.0, current_state=STATE_DISCHARGING
        )
        assert r.soc_cutoff_hit is True

    def test_discharge_cutoff_below_threshold(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=5.0, current_state=STATE_DISCHARGING
        )
        assert r.soc_cutoff_hit is True

    def test_discharge_cutoff_just_above_threshold(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=10.1, current_state=STATE_DISCHARGING
        )
        assert r.soc_cutoff_hit is False

    def test_high_soc_in_discharging_no_cutoff(self) -> None:
        """High SOC in DISCHARGING doesn't trigger charge cutoff."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=95.0, current_state=STATE_DISCHARGING
        )
        assert r.soc_cutoff_hit is False

    def test_low_soc_in_charging_no_cutoff(self) -> None:
        """Low SOC in CHARGING doesn't trigger discharge cutoff."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=5.0, current_state=STATE_CHARGING
        )
        assert r.soc_cutoff_hit is False

    def test_soc_cutoff_not_hit_in_idle(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=95.0, current_state=STATE_IDLE
        )
        assert r.soc_cutoff_hit is False

    def test_soc_cutoff_not_hit_in_standby(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, total_soc=5.0, current_state=STATE_STANDBY
        )
        assert r.soc_cutoff_hit is False


# ---------------------------------------------------------------------------
# CTRL-06: Temperature Derating — BMS high-temp zone
# ---------------------------------------------------------------------------


class TestDeratingBmsHigh:
    @pytest.mark.parametrize(
        "bms_temp, expected_pct",
        [
            (30.0, 100.0),   # well below start — no derating
            (40.0, 100.0),   # exactly at start — no derating
            (45.0, 50.0),    # midpoint: (45-40)/(50-40) = 50% derating → 50% power
            (50.0, 0.0),     # exactly at cutoff — zero power
            (55.0, 0.0),     # above cutoff — zero power (clamped)
        ],
    )
    def test_bms_high_derating_curve(
        self, bms_temp: float, expected_pct: float
    ) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=bms_temp,
            bms_min_cell_t=20.0,  # warm — no cold derating
            pcs_temp_c=40.0,  # cool — no PCS derating
        )
        assert r.active_derating_pct == pytest.approx(expected_pct, abs=0.01)

    def test_bms_high_just_above_start(self) -> None:
        """40.1°C should produce very slight derating, not 100%."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, bms_max_cell_t=40.1, bms_min_cell_t=20.0, pcs_temp_c=40.0
        )
        assert r.active_derating_pct < 100.0
        assert r.active_derating_pct > 90.0


# ---------------------------------------------------------------------------
# CTRL-06: Temperature Derating — PCS high-temp zone
# ---------------------------------------------------------------------------


class TestDeratingPcsHigh:
    @pytest.mark.parametrize(
        "pcs_temp, expected_pct",
        [
            (50.0, 100.0),   # well below start
            (65.0, 100.0),   # exactly at start
            (72.5, 50.0),    # midpoint: (72.5-65)/(80-65) = 50% derating → 50% power
            (80.0, 0.0),     # exactly at cutoff
            (90.0, 0.0),     # above cutoff
        ],
    )
    def test_pcs_high_derating_curve(
        self, pcs_temp: float, expected_pct: float
    ) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=25.0,  # cool BMS
            bms_min_cell_t=20.0,  # warm BMS
            pcs_temp_c=pcs_temp,
        )
        assert r.active_derating_pct == pytest.approx(expected_pct, abs=0.01)


# ---------------------------------------------------------------------------
# CTRL-06: Temperature Derating — BMS cold zone
# ---------------------------------------------------------------------------


class TestDeratingBmsLow:
    @pytest.mark.parametrize(
        "bms_min_temp, expected_pct",
        [
            (20.0, 100.0),   # well above start — no derating
            (5.0, 100.0),    # exactly at start — no derating
            (2.5, 50.0),     # midpoint: (5-2.5)/(5-0) = 50% derating → 50% power
            (0.0, 0.0),      # exactly at cutoff — zero power
            (-5.0, 0.0),     # below cutoff — zero power
        ],
    )
    def test_bms_cold_derating_curve(
        self, bms_min_temp: float, expected_pct: float
    ) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=25.0,  # cool BMS high (no high-temp derating)
            bms_min_cell_t=bms_min_temp,
            pcs_temp_c=40.0,  # cool PCS
        )
        assert r.active_derating_pct == pytest.approx(expected_pct, abs=0.01)

    def test_bms_cold_just_below_start(self) -> None:
        """4.9°C should produce very slight derating."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci, bms_max_cell_t=25.0, bms_min_cell_t=4.9, pcs_temp_c=40.0
        )
        assert r.active_derating_pct < 100.0
        assert r.active_derating_pct > 90.0


# ---------------------------------------------------------------------------
# CTRL-06: Temperature Derating — most restrictive zone wins
# ---------------------------------------------------------------------------


class TestDeratingMostRestrictive:
    def test_most_restrictive_bms_high_wins(self) -> None:
        """BMS high at 45°C (50%) more restrictive than PCS at 65°C (100%)."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=45.0,  # 50% derating
            bms_min_cell_t=20.0,  # no cold derating
            pcs_temp_c=65.0,      # exactly at start — 100%
        )
        assert r.active_derating_pct == pytest.approx(50.0, abs=0.01)

    def test_most_restrictive_pcs_high_wins(self) -> None:
        """PCS at 72.5°C (50%) more restrictive than BMS at 25°C (100%)."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=25.0,
            bms_min_cell_t=20.0,
            pcs_temp_c=72.5,  # 50% derating
        )
        assert r.active_derating_pct == pytest.approx(50.0, abs=0.01)

    def test_most_restrictive_cold_wins(self) -> None:
        """BMS cold at 2.5°C (50%) more restrictive than warm BMS high (100%)."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=25.0,  # no high-temp derating
            bms_min_cell_t=2.5,   # 50% derating
            pcs_temp_c=40.0,      # no PCS derating
        )
        assert r.active_derating_pct == pytest.approx(50.0, abs=0.01)

    def test_all_zones_normal_gives_100(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=25.0,
            bms_min_cell_t=20.0,
            pcs_temp_c=40.0,
        )
        assert r.active_derating_pct == pytest.approx(100.0, abs=0.01)

    def test_multiple_zones_degraded_uses_minimum(self) -> None:
        """If BMS high at 45°C (50%) AND PCS at 72.5°C (50%), result is 50%."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=45.0,  # 50%
            bms_min_cell_t=20.0,
            pcs_temp_c=72.5,      # 50%
        )
        assert r.active_derating_pct == pytest.approx(50.0, abs=0.01)

    def test_effective_max_power_computed_from_derating(self) -> None:
        """effective_max_power_kw = max_power_kw * derating_pct / 100."""
        ci: ControlIntelligence = _make_intelligence()
        # 50% derating: BMS high at 45°C
        r: IntelligenceResult = _evaluate(
            ci,
            raw_setpoint_kw=10.0,
            bms_max_cell_t=45.0,
            bms_min_cell_t=20.0,
            pcs_temp_c=40.0,
        )
        # max_charge_kw=25, derating=50% → effective=12.5
        assert r.effective_max_power_kw == pytest.approx(12.5, abs=0.01)
        assert r.active_derating_pct == pytest.approx(50.0, abs=0.01)

    def test_effective_max_power_full_derating_is_zero(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            bms_max_cell_t=55.0,  # above cutoff — 0%
            bms_min_cell_t=20.0,
            pcs_temp_c=40.0,
        )
        assert r.active_derating_pct == pytest.approx(0.0, abs=0.01)
        assert r.effective_max_power_kw == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# CTRL-08: Power Ramping
# ---------------------------------------------------------------------------


class TestPowerRamping:
    def _make_ci_with_ramp(self, ramp_kw_s: float = 5.0) -> ControlIntelligence:
        config: dict[str, Any] = {**_BASE_CONFIG}
        config["ramping"] = {
            "charge_ramp_kw_s": ramp_kw_s,
            "discharge_ramp_kw_s": ramp_kw_s,
        }
        return ControlIntelligence(config)

    def test_first_tick_no_ramping_applies(self) -> None:
        """First tick: _last_setpoint_kw=0, delta_t is synthetic (use 1.0s).
        With ramp=5 kW/s and delta=10 kW, setpoint snaps to 10 kW (10 <= 5*2? no, use 1s).
        Actually first tick uses large delta_t → snap-to-desired may apply."""
        ci: ControlIntelligence = self._make_ci_with_ramp(ramp_kw_s=5.0)
        # First evaluate — no previous setpoint known
        r: IntelligenceResult = _evaluate(ci, now_s=1000.0, raw_setpoint_kw=10.0)
        # Setpoint will be clamped by ramp from 0 → 10 kW; with first tick delta_t
        # implementation may start from 0 → check it doesn't exceed 10 kW
        assert r.desired_setpoint_kw <= 10.0

    def test_ramp_limits_large_step_in_1_second(self) -> None:
        """Ramp 5 kW/s: 1s elapsed, max step = 5 kW. From 0 → 20 kW desired, should get 5 kW."""
        ci: ControlIntelligence = self._make_ci_with_ramp(ramp_kw_s=5.0)
        # First tick to initialize _last_setpoint_kw at 0
        _evaluate(ci, now_s=1000.0, raw_setpoint_kw=0.0)
        # Second tick: 1 second later, desired=20 kW
        r: IntelligenceResult = _evaluate(ci, now_s=1001.0, raw_setpoint_kw=20.0)
        assert r.desired_setpoint_kw == pytest.approx(5.0, abs=0.1)

    def test_ramp_snaps_when_change_within_limit(self) -> None:
        """Ramp 5 kW/s: 1s elapsed, max step = 5 kW. Desired change = 3 kW → snap."""
        ci: ControlIntelligence = self._make_ci_with_ramp(ramp_kw_s=5.0)
        _evaluate(ci, now_s=1000.0, raw_setpoint_kw=0.0)
        r: IntelligenceResult = _evaluate(ci, now_s=1001.0, raw_setpoint_kw=3.0)
        assert r.desired_setpoint_kw == pytest.approx(3.0, abs=0.01)

    def test_ramp_negative_direction(self) -> None:
        """Ramp from +10 toward -20 kW: negative ramp limit applies."""
        ci: ControlIntelligence = self._make_ci_with_ramp(ramp_kw_s=5.0)
        # Start at 10 kW
        _evaluate(ci, now_s=1000.0, raw_setpoint_kw=10.0)
        # Jump to 0 (1s elapsed) → max step down = 5 kW → should get 5.0
        r: IntelligenceResult = _evaluate(ci, now_s=1001.0, raw_setpoint_kw=0.0)
        assert r.desired_setpoint_kw == pytest.approx(5.0, abs=0.1)

    def test_ramp_discharge_direction(self) -> None:
        """Ramp from 0 toward -20 kW in 1s: max step = -5 kW."""
        ci: ControlIntelligence = self._make_ci_with_ramp(ramp_kw_s=5.0)
        _evaluate(ci, now_s=1000.0, raw_setpoint_kw=0.0)
        r: IntelligenceResult = _evaluate(ci, now_s=1001.0, raw_setpoint_kw=-20.0)
        assert r.desired_setpoint_kw == pytest.approx(-5.0, abs=0.1)

    def test_ramp_uses_actual_elapsed_time(self) -> None:
        """Ramp 5 kW/s, 2 seconds elapsed: max step = 10 kW."""
        ci: ControlIntelligence = self._make_ci_with_ramp(ramp_kw_s=5.0)
        _evaluate(ci, now_s=1000.0, raw_setpoint_kw=0.0)
        r: IntelligenceResult = _evaluate(ci, now_s=1002.0, raw_setpoint_kw=20.0)
        assert r.desired_setpoint_kw == pytest.approx(10.0, abs=0.1)

    def test_ramp_no_overshoot_when_desired_is_within_step(self) -> None:
        """Ensure setpoint never overshoots desired value."""
        ci: ControlIntelligence = self._make_ci_with_ramp(ramp_kw_s=5.0)
        _evaluate(ci, now_s=1000.0, raw_setpoint_kw=0.0)
        # Only 2 kW desired, ramp allows 5 kW — should snap to 2, not overshoot
        r: IntelligenceResult = _evaluate(ci, now_s=1001.0, raw_setpoint_kw=2.0)
        assert r.desired_setpoint_kw == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------------------
# CTRL-09: Interlock Guards
# ---------------------------------------------------------------------------


class TestInterlockGuards:
    def test_interlock_clear_when_pcs_running_no_emergency(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            pcs_state=PCS_STATE_RUNNING,
            safety_emergency=False,
        )
        assert r.interlock_blocked is False

    def test_interlock_blocked_when_safety_emergency(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            pcs_state=PCS_STATE_RUNNING,
            safety_emergency=True,
        )
        assert r.interlock_blocked is True

    def test_interlock_blocked_when_pcs_not_running_off(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            pcs_state=PCS_STATE_OFF,
            safety_emergency=False,
        )
        assert r.interlock_blocked is True

    def test_interlock_blocked_when_pcs_standby(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            pcs_state=PCS_STATE_STANDBY,
            safety_emergency=False,
        )
        assert r.interlock_blocked is True

    def test_interlock_blocked_when_pcs_fault(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            pcs_state=PCS_STATE_FAULT,
            safety_emergency=False,
        )
        assert r.interlock_blocked is True

    def test_interlock_blocked_when_both_conditions(self) -> None:
        """Both emergency and PCS not running — still blocked."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            pcs_state=PCS_STATE_OFF,
            safety_emergency=True,
        )
        assert r.interlock_blocked is True


# ---------------------------------------------------------------------------
# Alarm Protection Handling
# ---------------------------------------------------------------------------


class TestAlarmProtection:
    def test_no_alarm_no_protection(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(ci, alarm_severity=None)
        assert r.protection_active is False

    def test_warning_severity_no_protection(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(ci, alarm_severity="warning")
        assert r.protection_active is False

    def test_action_severity_no_protection(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(ci, alarm_severity="action")
        assert r.protection_active is False

    def test_protection_severity_sets_flag(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(ci, alarm_severity="protection")
        assert r.protection_active is True

    def test_action_severity_reduces_setpoint_to_50_pct(self) -> None:
        """Action severity reduces adjusted_setpoint to 50% of raw_setpoint.

        With ramp pre-warmed to 10 kW (steady state), action severity reduces
        raw=10 kW to 5 kW. After ramp limiting (5 kW/s over 1s, delta=5 kW),
        the setpoint snaps to 5 kW since change=5 <= max_step=5.
        """
        ci: ControlIntelligence = _make_intelligence()
        # Pre-warm ramp to steady state at 10 kW
        _evaluate(ci, now_s=1000.0, raw_setpoint_kw=10.0)
        _evaluate(ci, now_s=1005.0, raw_setpoint_kw=10.0)
        _evaluate(ci, now_s=1010.0, raw_setpoint_kw=10.0)
        # Now with action alarm, raw=10 kW → adjusted=5 kW (50%)
        # delta = 10 - 5 = 5 kW; ramp 5 kW/s × 1s = 5 kW max_step → snap to 5 kW
        r: IntelligenceResult = _evaluate(
            ci, now_s=1011.0, raw_setpoint_kw=10.0, alarm_severity="action"
        )
        assert r.desired_setpoint_kw == pytest.approx(5.0, abs=0.5)

    def test_warning_severity_does_not_reduce_setpoint(self) -> None:
        """Warning severity has no effect on desired_setpoint."""
        ci: ControlIntelligence = _make_intelligence()
        # Allow ramp to steady state
        for t in range(10):
            _evaluate(ci, now_s=float(1000 + t), raw_setpoint_kw=10.0)
        r: IntelligenceResult = _evaluate(
            ci, now_s=1010.0, raw_setpoint_kw=10.0, alarm_severity="warning"
        )
        assert r.desired_setpoint_kw == pytest.approx(10.0, abs=0.5)

    def test_protection_does_not_affect_derating_pct(self) -> None:
        """Alarm severity does NOT affect effective_max_power (derating is separate)."""
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            alarm_severity="protection",
            bms_max_cell_t=25.0,
            bms_min_cell_t=20.0,
            pcs_temp_c=40.0,
        )
        # Derating should still be 100% (temps are normal)
        assert r.active_derating_pct == pytest.approx(100.0, abs=0.01)


# ---------------------------------------------------------------------------
# Config Update (hot-reload)
# ---------------------------------------------------------------------------


class TestConfigUpdate:
    def test_update_config_changes_soc_limits(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        # Before: charge_cutoff=95 — SOC at 90% should not trigger
        r1: IntelligenceResult = _evaluate(
            ci, total_soc=90.0, current_state=STATE_CHARGING
        )
        assert r1.soc_cutoff_hit is False

        # Update config: lower charge_cutoff to 85%
        new_config: dict[str, Any] = {
            **_BASE_CONFIG,
            "soc_limits": {
                "charge_cutoff_pct": 85,
                "discharge_cutoff_pct": 10,
            },
        }
        ci.update_config(new_config)

        # Now SOC at 90% should trigger cutoff
        r2: IntelligenceResult = _evaluate(
            ci, total_soc=90.0, current_state=STATE_CHARGING
        )
        assert r2.soc_cutoff_hit is True

    def test_update_config_changes_derating_thresholds(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        # Default: BMS high start=40°C — at 35°C, no derating
        r1: IntelligenceResult = _evaluate(
            ci, bms_max_cell_t=35.0, bms_min_cell_t=20.0, pcs_temp_c=40.0
        )
        assert r1.active_derating_pct == pytest.approx(100.0, abs=0.01)

        # Update: lower BMS high start to 30°C — at 35°C, should derate
        new_config: dict[str, Any] = {
            **_BASE_CONFIG,
            "derating": {
                **_BASE_CONFIG["derating"],
                "bms_high_start_c": 30.0,
                "bms_high_cutoff_c": 40.0,
            },
        }
        ci.update_config(new_config)

        r2: IntelligenceResult = _evaluate(
            ci, bms_max_cell_t=35.0, bms_min_cell_t=20.0, pcs_temp_c=40.0
        )
        assert r2.active_derating_pct < 100.0

    def test_update_config_is_atomic(self) -> None:
        """After update, subsequent evaluate uses new config, not a mix."""
        ci: ControlIntelligence = _make_intelligence()
        new_config: dict[str, Any] = {
            **_BASE_CONFIG,
            "ramping": {
                "charge_ramp_kw_s": 1.0,   # much slower ramp
                "discharge_ramp_kw_s": 1.0,
            },
        }
        ci.update_config(new_config)
        # Re-initialize ramp state by evaluating at 0
        _evaluate(ci, now_s=2000.0, raw_setpoint_kw=0.0)
        # 1 second later with 1 kW/s ramp: max step = 1 kW
        r: IntelligenceResult = _evaluate(ci, now_s=2001.0, raw_setpoint_kw=20.0)
        assert r.desired_setpoint_kw == pytest.approx(1.0, abs=0.1)


# ---------------------------------------------------------------------------
# BESS availability edge cases
# ---------------------------------------------------------------------------


class TestBessAvailability:
    def test_bess_available_when_soc_above_cutoff(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="DAY",
            solar_available=False,
            gpio_di=_NO_GRID_DI,
            total_soc=10.1,  # just above discharge_cutoff (10%)
            current_state=STATE_STANDBY,
        )
        assert r.active_source == "bess"

    def test_bess_unavailable_when_soc_at_cutoff(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="DAY",
            solar_available=False,
            gpio_di=_NO_GRID_DI,
            total_soc=10.0,  # exactly at discharge_cutoff
            current_state=STATE_STANDBY,
        )
        # BESS at exactly cutoff → unavailable → no source
        assert r.active_source == "none"

    def test_bess_unavailable_in_fault_state(self) -> None:
        ci: ControlIntelligence = _make_intelligence()
        r: IntelligenceResult = _evaluate(
            ci,
            mode="NIGHT",
            gpio_di=_NO_GRID_DI,
            total_soc=80.0,
            current_state=STATE_FAULT,
        )
        assert r.active_source == "none"
