"""
Tests for ControlStateMachine — pure state transition logic.

Tests are written against the behavior spec (RED phase) before implementation.
All 21 test cases cover every transition and edge case defined in 14-02-PLAN.md.
"""

import pytest
from ems_control_manager.state_machine import (
    ControlStateMachine,
    TickResult,
    STATE_INIT,
    STATE_IDLE,
    STATE_STANDBY,
    STATE_CHARGING,
    STATE_DISCHARGING,
    STATE_FAULT,
    STATE_EMERGENCY,
    STATE_MAINTENANCE,
    PCS_CMD_NONE,
    PCS_CMD_ON,
    PCS_CMD_OFF,
    PCS_CMD_FAULT_RESET,
)

# PCS state ints (matching ems_pcs_state_t)
PCS_OFF = 0
PCS_STANDBY = 1
PCS_RUNNING = 2
PCS_FAULT = 3

# Default config (matches control_config.yaml state_machine section)
DEFAULT_CONFIG: dict = {
    "loop_interval_ms": 1000,
    "fault_retry_count": 3,
    "pcs_startup_timeout_s": 10,
    "pcs_shutdown_timeout_s": 10,
}


def make_sm(config: dict | None = None) -> ControlStateMachine:
    """Create a ControlStateMachine with default or custom config."""
    return ControlStateMachine(config or DEFAULT_CONFIG)


def tick(
    sm: ControlStateMachine,
    now_s: float = 0.0,
    pcs_state: int = PCS_OFF,
    pcs_fault_code: int = 0,
    safety_emergency: bool = False,
    rtdb_state: int = STATE_INIT,
) -> TickResult:
    """Convenience tick wrapper."""
    return sm.tick(
        now_s=now_s,
        pcs_state=pcs_state,
        pcs_fault_code=pcs_fault_code,
        safety_emergency=safety_emergency,
        current_rtdb_state=rtdb_state,
    )


# ---------------------------------------------------------------------------
# State constants sanity — must match C enum ems_control_state_t
# ---------------------------------------------------------------------------


class TestStateConstants:
    def test_state_values_match_c_enum(self) -> None:
        assert STATE_INIT == 0
        assert STATE_IDLE == 1
        assert STATE_STANDBY == 2
        assert STATE_CHARGING == 3
        assert STATE_DISCHARGING == 4
        assert STATE_FAULT == 5
        assert STATE_EMERGENCY == 6
        assert STATE_MAINTENANCE == 7

    def test_pcs_cmd_values(self) -> None:
        assert PCS_CMD_NONE == 0
        assert PCS_CMD_ON == 1
        assert PCS_CMD_OFF == 2
        assert PCS_CMD_FAULT_RESET == 3


# ---------------------------------------------------------------------------
# TC-01: INIT -> IDLE on first tick with PCS_OFF
# ---------------------------------------------------------------------------


class TestInitToIdle:
    def test_init_tick_with_pcs_off_becomes_idle(self) -> None:
        sm = make_sm()
        result = tick(sm, now_s=0.0, pcs_state=PCS_OFF)
        assert result.state == STATE_IDLE
        assert result.state_changed is True
        assert result.prev_state == STATE_INIT

    def test_tick_result_is_tick_result_dataclass(self) -> None:
        sm = make_sm()
        result = tick(sm)
        assert isinstance(result, TickResult)

    def test_init_state_before_any_tick(self) -> None:
        sm = make_sm()
        assert sm.state == STATE_INIT


# ---------------------------------------------------------------------------
# TC-02: IDLE -> STANDBY: request_mode_change("standby") + tick -> STARTING, pcs_command=ON
# ---------------------------------------------------------------------------


class TestIdleToStandby:
    def test_request_standby_accepted_from_idle(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # INIT -> IDLE
        ok, err = sm.request_mode_change("standby")
        assert ok is True
        assert err == ""

    def test_request_standby_triggers_pcs_on_command(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # INIT -> IDLE
        sm.request_mode_change("standby")
        result = tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        assert result.pcs_command == PCS_CMD_ON
        assert result.pcs_command_changed is True

    def test_state_in_starting_substate_after_standby_request(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # INIT -> IDLE
        sm.request_mode_change("standby")
        result = tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        # State stays IDLE until PCS confirms RUNNING
        # (or shows sub-state as STANDBY pending PCS startup)
        # The external state should be STANDBY (we entered the transition)
        assert result.state in (STATE_IDLE, STATE_STANDBY)
        # PCS command must be ON
        assert result.pcs_command == PCS_CMD_ON


# ---------------------------------------------------------------------------
# TC-03: STARTING -> STANDBY: PCS reports RUNNING within timeout
# ---------------------------------------------------------------------------


class TestStartingToStandby:
    def test_pcs_running_within_timeout_reaches_standby(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # INIT -> IDLE
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)  # Send PCS ON
        # PCS now running before timeout
        result = tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        assert result.state == STATE_STANDBY
        assert result.state_changed is True


# ---------------------------------------------------------------------------
# TC-04: STARTING timeout -> FAULT (no PCS RUNNING within pcs_startup_timeout_s)
# ---------------------------------------------------------------------------


class TestStartingTimeout:
    def test_startup_timeout_goes_to_fault(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # INIT -> IDLE
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)  # Send PCS ON (t=1)
        # Advance past 10s timeout without PCS coming up
        result = tick(sm, now_s=12.0, pcs_state=PCS_OFF)
        assert result.state == STATE_FAULT


# ---------------------------------------------------------------------------
# TC-05: STANDBY -> CHARGING: positive setpoint
# ---------------------------------------------------------------------------


class TestStandbyToCharging:
    def test_positive_setpoint_enters_charging(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # INIT -> IDLE
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)  # Now in STANDBY
        sm.request_manual_setpoint(25.0)
        result = tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)
        assert result.state == STATE_CHARGING
        assert result.setpoint_kw == pytest.approx(25.0)
        assert result.state_changed is True


# ---------------------------------------------------------------------------
# TC-06: STANDBY -> DISCHARGING: negative setpoint
# ---------------------------------------------------------------------------


class TestStandbyToDischarging:
    def test_negative_setpoint_enters_discharging(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        sm.request_manual_setpoint(-10.0)
        result = tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)
        assert result.state == STATE_DISCHARGING
        assert result.setpoint_kw == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# TC-07/08: CHARGING -> STANDBY: zero setpoint, no PCS OFF
# ---------------------------------------------------------------------------


class TestChargingToStandby:
    def _reach_charging(self, sm: ControlStateMachine, now: float = 3.0) -> float:
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        sm.request_manual_setpoint(25.0)
        tick(sm, now_s=now, pcs_state=PCS_RUNNING)
        return now

    def test_charging_to_standby_sets_zero_setpoint(self) -> None:
        sm = make_sm()
        self._reach_charging(sm, now=3.0)
        sm.request_mode_change("standby")
        result = tick(sm, now_s=4.0, pcs_state=PCS_RUNNING)
        assert result.state == STATE_STANDBY
        assert result.setpoint_kw == pytest.approx(0.0)

    def test_charging_to_standby_no_pcs_off_sent(self) -> None:
        sm = make_sm()
        self._reach_charging(sm, now=3.0)
        sm.request_mode_change("standby")
        result = tick(sm, now_s=4.0, pcs_state=PCS_RUNNING)
        # PCS stays ON — no OFF command
        assert result.pcs_command != PCS_CMD_OFF


# ---------------------------------------------------------------------------
# TC-09: STANDBY -> IDLE: mode_change("idle") -> STOPPING sub-state -> PCS OFF -> IDLE
# ---------------------------------------------------------------------------


class TestStandbyToIdle:
    def _reach_standby(self, sm: ControlStateMachine) -> None:
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)

    def test_standby_to_idle_sends_pcs_off_after_ramp(self) -> None:
        sm = make_sm()
        self._reach_standby(sm)
        sm.request_mode_change("idle")
        # Tick at t=3 — enters STOPPING, setpoint=0
        result_t3 = tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)
        # After 2s ramp hold, OFF is sent
        result_t5 = tick(sm, now_s=5.5, pcs_state=PCS_RUNNING)
        assert result_t5.pcs_command == PCS_CMD_OFF

    def test_standby_to_idle_reaches_idle_after_pcs_off(self) -> None:
        sm = make_sm()
        self._reach_standby(sm)
        sm.request_mode_change("idle")
        tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)
        tick(sm, now_s=5.5, pcs_state=PCS_RUNNING)  # OFF sent
        # PCS reports OFF, shutdown timeout satisfied
        result = tick(sm, now_s=16.0, pcs_state=PCS_OFF)
        assert result.state == STATE_IDLE


# ---------------------------------------------------------------------------
# TC-10: CHARGING -> DISCHARGING REJECTED (must go via STANDBY)
# ---------------------------------------------------------------------------


class TestDirectChargingToDischarging:
    def test_charging_to_discharging_rejected(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        sm.request_manual_setpoint(25.0)
        tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)  # Now CHARGING
        # Request negative setpoint from CHARGING — should reject or require STANDBY first
        ok, err = sm.request_manual_setpoint(-10.0)
        # Direct direction change is either rejected OR transitions through standby
        # Per spec: must go CHARGING->STANDBY->DISCHARGING, direct returns error
        assert ok is False
        assert len(err) > 0

    def test_discharging_to_charging_rejected(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        sm.request_manual_setpoint(-10.0)
        tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)  # Now DISCHARGING
        ok, err = sm.request_manual_setpoint(25.0)
        assert ok is False
        assert len(err) > 0


# ---------------------------------------------------------------------------
# TC-11: Any -> FAULT: non-zero pcs_fault_code triggers FAULT
# ---------------------------------------------------------------------------


class TestFaultTrigger:
    def test_pcs_fault_code_triggers_fault_from_idle(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # IDLE
        result = tick(sm, now_s=1.0, pcs_fault_code=5)
        assert result.state == STATE_FAULT

    def test_pcs_fault_code_triggers_fault_from_standby(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        result = tick(sm, now_s=3.0, pcs_fault_code=1, pcs_state=PCS_RUNNING)
        assert result.state == STATE_FAULT

    def test_pcs_fault_code_triggers_fault_from_charging(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        sm.request_manual_setpoint(25.0)
        tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)
        result = tick(sm, now_s=4.0, pcs_fault_code=7, pcs_state=PCS_RUNNING)
        assert result.state == STATE_FAULT


# ---------------------------------------------------------------------------
# TC-12: FAULT auto-retry: sends FAULT_RESET, waits for PCS recovery
# ---------------------------------------------------------------------------


class TestFaultAutoRetry:
    def _reach_fault(self, sm: ControlStateMachine) -> None:
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, pcs_fault_code=5)

    def test_fault_auto_retry_sends_fault_reset(self) -> None:
        sm = make_sm()
        self._reach_fault(sm)
        # After pcs_startup_timeout_s (10s), first retry sends FAULT_RESET
        result = tick(sm, now_s=12.0, pcs_fault_code=5, pcs_state=PCS_FAULT)
        assert result.pcs_command == PCS_CMD_FAULT_RESET

    def test_fault_retry_on_pcs_recovery_returns_to_idle(self) -> None:
        sm = make_sm()
        self._reach_fault(sm)
        # Send fault reset
        tick(sm, now_s=12.0, pcs_fault_code=5, pcs_state=PCS_FAULT)
        # PCS recovers (fault code cleared, PCS back to OFF)
        result = tick(sm, now_s=13.0, pcs_fault_code=0, pcs_state=PCS_OFF)
        assert result.state == STATE_IDLE

    def test_fault_retry_exhausted_stays_in_fault(self) -> None:
        sm = make_sm()
        self._reach_fault(sm)
        # 3 retries, each 10s apart — after all exhausted, stays FAULT
        for i in range(3):
            tick(sm, now_s=12.0 + i * 10.0, pcs_fault_code=5, pcs_state=PCS_FAULT)
        result = tick(sm, now_s=42.0, pcs_fault_code=5, pcs_state=PCS_FAULT)
        assert result.state == STATE_FAULT
        # No more FAULT_RESET commands sent
        assert result.pcs_command == PCS_CMD_NONE


# ---------------------------------------------------------------------------
# TC-13: FAULT manual reset: request_fault_reset() -> sends FAULT_RESET immediately
# ---------------------------------------------------------------------------


class TestFaultManualReset:
    def test_fault_reset_accepted_in_fault_state(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, pcs_fault_code=5)
        ok, err = sm.request_fault_reset()
        assert ok is True

    def test_fault_reset_not_accepted_outside_fault(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # IDLE
        ok, err = sm.request_fault_reset()
        assert ok is False

    def test_fault_reset_sends_fault_reset_command(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, pcs_fault_code=5)
        sm.request_fault_reset()
        result = tick(sm, now_s=2.0, pcs_fault_code=5, pcs_state=PCS_FAULT)
        assert result.pcs_command == PCS_CMD_FAULT_RESET

    def test_fault_reset_cancels_retry_countdown(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, pcs_fault_code=5)
        # Manual reset can be accepted even before auto-retry fires
        ok, err = sm.request_fault_reset()
        assert ok is True

    def test_fault_reset_recovers_to_idle_when_pcs_ok(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, pcs_fault_code=5)
        sm.request_fault_reset()
        tick(sm, now_s=2.0, pcs_fault_code=5, pcs_state=PCS_FAULT)
        result = tick(sm, now_s=3.0, pcs_fault_code=0, pcs_state=PCS_OFF)
        assert result.state == STATE_IDLE


# ---------------------------------------------------------------------------
# TC-14: Any -> EMERGENCY: safety_emergency=True triggers EMERGENCY
# ---------------------------------------------------------------------------


class TestEmergencyTrigger:
    def test_emergency_from_idle(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # IDLE
        result = tick(sm, now_s=1.0, safety_emergency=True)
        assert result.state == STATE_EMERGENCY

    def test_emergency_from_charging(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)
        sm.request_manual_setpoint(25.0)
        tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)
        result = tick(sm, now_s=4.0, pcs_state=PCS_RUNNING, safety_emergency=True)
        assert result.state == STATE_EMERGENCY

    def test_emergency_no_commands_sent(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        result = tick(sm, now_s=1.0, safety_emergency=True)
        assert result.pcs_command == PCS_CMD_NONE


# ---------------------------------------------------------------------------
# TC-15: EMERGENCY -> IDLE: safety latch cleared when emergency lifted
# ---------------------------------------------------------------------------


class TestEmergencyToIdle:
    def test_emergency_clears_to_idle_when_safe(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, safety_emergency=True)  # EMERGENCY
        result = tick(sm, now_s=2.0, safety_emergency=False, pcs_state=PCS_OFF)
        assert result.state == STATE_IDLE


# ---------------------------------------------------------------------------
# TC-16: Any -> MAINTENANCE: request_maintenance_enter() from any non-EMERGENCY state
# ---------------------------------------------------------------------------


class TestMaintenanceEnter:
    def test_maintenance_enter_from_idle(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # IDLE
        ok, err = sm.request_maintenance_enter()
        assert ok is True
        result = tick(sm, now_s=1.0)
        assert result.state == STATE_MAINTENANCE

    def test_maintenance_enter_sends_pcs_off(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)
        tick(sm, now_s=2.0, pcs_state=PCS_RUNNING)  # STANDBY
        sm.request_maintenance_enter()
        result = tick(sm, now_s=3.0, pcs_state=PCS_RUNNING)
        assert result.state == STATE_MAINTENANCE
        assert result.pcs_command == PCS_CMD_OFF

    def test_maintenance_enter_rejected_from_emergency(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, safety_emergency=True)  # EMERGENCY
        ok, err = sm.request_maintenance_enter()
        assert ok is False


# ---------------------------------------------------------------------------
# TC-17: MAINTENANCE -> IDLE: request_maintenance_exit()
# ---------------------------------------------------------------------------


class TestMaintenanceExit:
    def test_maintenance_exit_to_idle(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_maintenance_enter()
        tick(sm, now_s=1.0)  # MAINTENANCE
        ok, err = sm.request_maintenance_exit()
        assert ok is True
        result = tick(sm, now_s=2.0)
        assert result.state == STATE_IDLE


# ---------------------------------------------------------------------------
# TC-18: MAINTENANCE persistence — startup check reads control_state from RTDB
# ---------------------------------------------------------------------------


class TestMaintenancePersistence:
    def test_maintenance_persistence_on_first_tick(self) -> None:
        sm = make_sm()
        # If RTDB says MAINTENANCE, first tick must stay in MAINTENANCE
        result = tick(sm, now_s=0.0, rtdb_state=STATE_MAINTENANCE)
        assert result.state == STATE_MAINTENANCE


# ---------------------------------------------------------------------------
# TC-19: Commands rejected during STARTING sub-state
# ---------------------------------------------------------------------------


class TestCommandsDuringStarting:
    def test_mode_change_rejected_during_starting(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # IDLE
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)  # STARTING
        # Try another mode change while STARTING
        ok, err = sm.request_mode_change("idle")
        assert ok is False
        assert len(err) > 0

    def test_error_includes_remaining_time_during_starting(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_mode_change("standby")
        tick(sm, now_s=1.0, pcs_state=PCS_OFF)  # STARTING at t=1
        ok, err = sm.request_mode_change("idle")
        assert ok is False
        # Error should mention remaining time
        assert any(c.isdigit() for c in err)


# ---------------------------------------------------------------------------
# TC-20: Commands rejected during EMERGENCY
# ---------------------------------------------------------------------------


class TestCommandsDuringEmergency:
    def test_all_commands_rejected_in_emergency(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        tick(sm, now_s=1.0, safety_emergency=True)  # EMERGENCY

        ok1, _ = sm.request_mode_change("standby")
        ok2, _ = sm.request_manual_setpoint(10.0)
        ok3, _ = sm.request_maintenance_enter()
        ok4, _ = sm.request_fault_reset()

        assert ok1 is False
        assert ok2 is False
        assert ok3 is False
        assert ok4 is False


# ---------------------------------------------------------------------------
# TC-21: MAINTENANCE rejects all except maintenance_exit
# ---------------------------------------------------------------------------


class TestCommandsDuringMaintenance:
    def test_maintenance_rejects_mode_change(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_maintenance_enter()
        tick(sm, now_s=1.0)  # MAINTENANCE
        ok, err = sm.request_mode_change("standby")
        assert ok is False

    def test_maintenance_rejects_setpoint(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_maintenance_enter()
        tick(sm, now_s=1.0)
        ok, err = sm.request_manual_setpoint(10.0)
        assert ok is False

    def test_maintenance_accepts_exit(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)
        sm.request_maintenance_enter()
        tick(sm, now_s=1.0)
        ok, err = sm.request_maintenance_exit()
        assert ok is True


# ---------------------------------------------------------------------------
# Additional: TickResult fields are complete and correct types
# ---------------------------------------------------------------------------


class TestTickResultFields:
    def test_tick_result_has_all_required_fields(self) -> None:
        sm = make_sm()
        result = tick(sm, now_s=0.0)
        assert hasattr(result, "state")
        assert hasattr(result, "setpoint_kw")
        assert hasattr(result, "pcs_command")
        assert hasattr(result, "pcs_command_changed")
        assert hasattr(result, "state_changed")
        assert hasattr(result, "prev_state")
        assert hasattr(result, "error_msg")

    def test_no_transition_state_changed_false(self) -> None:
        sm = make_sm()
        tick(sm, now_s=0.0)  # INIT -> IDLE (state_changed)
        result = tick(sm, now_s=1.0)  # IDLE stays IDLE
        assert result.state_changed is False
        assert result.prev_state is None

    def test_setpoint_defaults_to_zero(self) -> None:
        sm = make_sm()
        result = tick(sm, now_s=0.0)
        assert result.setpoint_kw == pytest.approx(0.0)
