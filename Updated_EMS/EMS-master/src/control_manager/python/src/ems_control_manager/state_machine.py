"""
ControlStateMachine — pure state transition logic for EMS control_manager.

This module implements the core 1Hz state machine that drives control decisions.
It is pure logic with no I/O: no RTDB access, no ZMQ, no asyncio.

State constants match ems_control_state_t in src/common/c/include/ems_types.h.
PCS state constants match ems_pcs_state_t in the same file.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Optional

# ---------------------------------------------------------------------------
# State constants — MUST match ems_control_state_t int values in ems_types.h
# ---------------------------------------------------------------------------

STATE_INIT: int = 0
STATE_IDLE: int = 1
STATE_STANDBY: int = 2
STATE_CHARGING: int = 3
STATE_DISCHARGING: int = 4
STATE_FAULT: int = 5
STATE_EMERGENCY: int = 6
STATE_MAINTENANCE: int = 7

# ---------------------------------------------------------------------------
# PCS state constants — match ems_pcs_state_t
# ---------------------------------------------------------------------------

PCS_STATE_OFF: int = 0
PCS_STATE_STANDBY: int = 1
PCS_STATE_RUNNING: int = 2
PCS_STATE_FAULT: int = 3

# ---------------------------------------------------------------------------
# PCS command constants — written to RTDB pcs_command field
# ---------------------------------------------------------------------------

PCS_CMD_NONE: int = 0
PCS_CMD_ON: int = 1
PCS_CMD_OFF: int = 2
PCS_CMD_FAULT_RESET: int = 3

# ---------------------------------------------------------------------------
# Internal sub-state enum (not exposed externally)
# ---------------------------------------------------------------------------

_STATE_NAMES: dict[int, str] = {
    STATE_INIT: "INIT",
    STATE_IDLE: "IDLE",
    STATE_STANDBY: "STANDBY",
    STATE_CHARGING: "CHARGING",
    STATE_DISCHARGING: "DISCHARGING",
    STATE_FAULT: "FAULT",
    STATE_EMERGENCY: "EMERGENCY",
    STATE_MAINTENANCE: "MAINTENANCE",
}


class _SubState(enum.Enum):
    """Internal sub-state for PCS sequencing transitions."""
    NONE = 0
    STARTING = 1   # PCS ON command sent; awaiting PCS_RUNNING confirmation
    STOPPING = 2   # PCS OFF sequencing in progress


# ---------------------------------------------------------------------------
# TickResult dataclass — all outputs of a single tick
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class TickResult:
    """Outputs of a single ControlStateMachine.tick() call.

    Callers must write state, setpoint_kw, and pcs_command to RTDB.
    If pcs_command_changed is True, increment pcs_command_seq in RTDB.
    """

    state: int                  # Current control state (int matching ems_control_state_t)
    setpoint_kw: float          # Active power setpoint to write to RTDB
    pcs_command: int            # PCS_CMD_* to write (PCS_CMD_NONE if no change)
    pcs_command_changed: bool   # True if pcs_command should increment seq
    state_changed: bool         # True if state transitioned this tick
    prev_state: Optional[int]   # Previous state if state_changed, else None
    error_msg: str              # Non-empty if a command was rejected this tick


# ---------------------------------------------------------------------------
# ControlStateMachine
# ---------------------------------------------------------------------------


class ControlStateMachine:
    """Pure state transition logic for EMS control manager.

    Receives sensor data + RTDB state, returns transition decisions + commands.
    No I/O, no RTDB access, no ZMQ, no asyncio.

    Call tick() once per 1Hz loop cycle. Call request_* methods before tick()
    to inject operator commands.
    """

    def __init__(self, config: dict) -> None:
        """Initialise state machine from the state_machine section of control_config.

        Args:
            config: Dict with keys: loop_interval_ms, fault_retry_count,
                    pcs_startup_timeout_s, pcs_shutdown_timeout_s.
        """
        self._config: dict = config
        self._fault_retry_count: int = int(config.get("fault_retry_count", 3))
        self._pcs_startup_timeout_s: float = float(config.get("pcs_startup_timeout_s", 10.0))
        self._pcs_shutdown_timeout_s: float = float(config.get("pcs_shutdown_timeout_s", 10.0))

        # Core state
        self._state: int = STATE_INIT
        self._sub_state: _SubState = _SubState.NONE
        self._transition_start_s: float = 0.0

        # Setpoint
        self._current_setpoint_kw: float = 0.0
        self._pending_setpoint_kw: Optional[float] = None

        # Pending mode change target state
        self._pending_target_state: Optional[int] = None

        # Fault tracking
        self._fault_retries_done: int = 0
        self._fault_reset_requested: bool = False
        self._fault_retry_timer_s: float = 0.0  # time when fault entered / last retry sent

        # Maintenance / emergency pending requests
        self._pending_maintenance_enter: bool = False
        self._pending_maintenance_exit: bool = False
        self._initialized: bool = False

        # Fault injection from alarm_manager protection events
        self._pending_fault_inject: bool = False

    # ------------------------------------------------------------------
    # Public property
    # ------------------------------------------------------------------

    @property
    def state(self) -> int:
        """Current control state int value."""
        return self._state

    # ------------------------------------------------------------------
    # Command methods — called by ControlLoop before tick()
    # ------------------------------------------------------------------

    def request_mode_change(self, target: str) -> tuple[bool, str]:
        """Request a mode change to 'standby', 'idle', or 'fault'.

        The 'fault' target is used by ControlLoop to inject a fault from an
        alarm_manager protection-severity event. It is accepted from any
        non-terminal state (i.e., not EMERGENCY, not already FAULT).

        Returns:
            (True, "") on acceptance or (False, error_message) on rejection.
        """
        # Fault injection — handled before other guards (protection alarms take priority)
        if target == "fault":
            if self._state in (STATE_EMERGENCY, STATE_FAULT, STATE_MAINTENANCE):
                return False, f"Fault inject rejected: already in {_STATE_NAMES.get(self._state, str(self._state))}"
            self._pending_fault_inject = True
            return True, ""

        # Reject in EMERGENCY
        if self._state == STATE_EMERGENCY:
            return False, "Command rejected: system in EMERGENCY state"

        # Reject in MAINTENANCE
        if self._state == STATE_MAINTENANCE:
            return False, "Command rejected: system in MAINTENANCE state"

        # Reject during sub-state transitions
        if self._sub_state != _SubState.NONE:
            sub_name: str = self._sub_state.name
            return False, f"Command rejected: PCS {sub_name} in progress, up to {self._pcs_startup_timeout_s:.0f}s remaining"

        if target == "standby":
            if self._state == STATE_IDLE:
                self._pending_target_state = STATE_STANDBY
                return True, ""
            elif self._state in (STATE_CHARGING, STATE_DISCHARGING):
                # Go back to standby (zero setpoint, no PCS OFF)
                self._pending_target_state = STATE_STANDBY
                return True, ""
            else:
                return False, f"Cannot enter standby from {_STATE_NAMES.get(self._state, str(self._state))}"

        elif target == "idle":
            if self._state in (STATE_STANDBY, STATE_CHARGING, STATE_DISCHARGING):
                self._pending_target_state = STATE_IDLE
                return True, ""
            elif self._state == STATE_IDLE:
                return False, "Already in IDLE state"
            else:
                return False, f"Cannot enter idle from {_STATE_NAMES.get(self._state, str(self._state))}"

        return False, f"Unknown target mode: {target!r}"

    def request_manual_setpoint(self, power_kw: float) -> tuple[bool, str]:
        """Request a manual power setpoint change.

        Only valid in STANDBY, CHARGING, or DISCHARGING.
        Direction changes (CHARGING->negative or DISCHARGING->positive) are rejected.

        Args:
            power_kw: Desired power. Positive = charging, negative = discharging.

        Returns:
            (True, "") on acceptance or (False, error_message) on rejection.
        """
        if self._state == STATE_EMERGENCY:
            return False, "Command rejected: system in EMERGENCY state"

        if self._state == STATE_MAINTENANCE:
            return False, "Command rejected: system in MAINTENANCE state"

        if self._sub_state != _SubState.NONE:
            return False, f"Command rejected: PCS {self._sub_state.name} in progress"

        # Reject direction changes that skip STANDBY
        if self._state == STATE_CHARGING and power_kw < 0.0:
            return False, "Direction change rejected: go to STANDBY first before discharging"
        if self._state == STATE_DISCHARGING and power_kw > 0.0:
            return False, "Direction change rejected: go to STANDBY first before charging"

        if self._state in (STATE_STANDBY, STATE_CHARGING, STATE_DISCHARGING):
            self._pending_setpoint_kw = power_kw
            return True, ""

        return False, f"Setpoint rejected: not in STANDBY/CHARGING/DISCHARGING (currently {_STATE_NAMES.get(self._state, str(self._state))})"

    def request_fault_reset(self) -> tuple[bool, str]:
        """Request a fault reset. Only valid in FAULT state.

        Cancels retry countdown immediately (operator override).

        Returns:
            (True, "") on acceptance or (False, error_message) on rejection.
        """
        if self._state == STATE_EMERGENCY:
            return False, "Command rejected: system in EMERGENCY state"

        if self._state != STATE_FAULT:
            return False, f"Fault reset rejected: not in FAULT state (currently {_STATE_NAMES.get(self._state, str(self._state))})"

        self._fault_reset_requested = True
        return True, ""

    def request_maintenance_enter(self) -> tuple[bool, str]:
        """Request entry into MAINTENANCE mode from any non-EMERGENCY state.

        Returns:
            (True, "") on acceptance or (False, error_message) on rejection.
        """
        if self._state == STATE_EMERGENCY:
            return False, "Command rejected: cannot enter maintenance during EMERGENCY"

        self._pending_maintenance_enter = True
        return True, ""

    def request_maintenance_exit(self) -> tuple[bool, str]:
        """Request exit from MAINTENANCE mode back to IDLE.

        Returns:
            (True, "") on acceptance or (False, error_message) on rejection.
        """
        if self._state != STATE_MAINTENANCE:
            return False, f"Maintenance exit rejected: not in MAINTENANCE state (currently {_STATE_NAMES.get(self._state, str(self._state))})"

        self._pending_maintenance_exit = True
        return True, ""

    # ------------------------------------------------------------------
    # Core tick method
    # ------------------------------------------------------------------

    def tick(
        self,
        now_s: float,
        pcs_state: int,
        pcs_fault_code: int,
        safety_emergency: bool,
        current_rtdb_state: int,
    ) -> TickResult:
        """Execute one 1Hz control cycle.

        Priority order (highest to lowest):
          1. EMERGENCY check (overrides everything)
          2. PCS fault check (overrides normal transitions)
          3. Sub-state timers (STARTING, STOPPING)
          4. Pending commands (maintenance, mode changes, setpoints)
          5. INIT bootstrap (first tick)

        Args:
            now_s: Current monotonic time in seconds.
            pcs_state: Current PCS state from RTDB (ems_pcs_state_t int).
            pcs_fault_code: Non-zero if PCS has an active fault.
            safety_emergency: True if safety manager asserts an emergency condition.
            current_rtdb_state: Current control_state in RTDB (used for INIT persistence).

        Returns:
            TickResult with all transition outputs.
        """
        prev_state: int = self._state
        pcs_command: int = PCS_CMD_NONE
        pcs_command_changed: bool = False
        error_msg: str = ""

        # ----------------------------------------------------------------
        # Priority 1: EMERGENCY (highest priority — overrides everything)
        # ----------------------------------------------------------------
        if safety_emergency and self._state != STATE_EMERGENCY:
            self._state = STATE_EMERGENCY
            self._sub_state = _SubState.NONE
            self._pending_target_state = None
            self._pending_setpoint_kw = None
            self._pending_maintenance_enter = False
            # No PCS command during emergency — hardware safety handles PCS stop
            pcs_command = PCS_CMD_NONE
            pcs_command_changed = False
            return self._make_result(
                prev_state=prev_state,
                pcs_command=pcs_command,
                pcs_command_changed=pcs_command_changed,
                error_msg=error_msg,
            )

        # Emergency cleared — return to IDLE
        if not safety_emergency and self._state == STATE_EMERGENCY:
            self._state = STATE_IDLE
            self._sub_state = _SubState.NONE
            return self._make_result(
                prev_state=prev_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )

        # ----------------------------------------------------------------
        # Priority 1b: Alarm protection fault inject (just below EMERGENCY)
        # Set by request_mode_change("fault") when alarm_manager publishes
        # a protection-severity event.
        # ----------------------------------------------------------------
        if self._pending_fault_inject and self._state not in (
            STATE_FAULT, STATE_EMERGENCY, STATE_MAINTENANCE, STATE_INIT
        ):
            self._pending_fault_inject = False
            self._state = STATE_FAULT
            self._sub_state = _SubState.NONE
            self._pending_target_state = None
            self._pending_setpoint_kw = None
            self._fault_retries_done = 0
            self._fault_reset_requested = False
            self._fault_retry_timer_s = now_s
            self._current_setpoint_kw = 0.0
            return self._make_result(
                prev_state=prev_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )
        # Clear stale pending_fault_inject if we're already in a terminal state
        self._pending_fault_inject = False

        # ----------------------------------------------------------------
        # Priority 2: PCS fault (second priority — overrides normal ops)
        # ----------------------------------------------------------------
        if pcs_fault_code != 0 and self._state not in (
            STATE_FAULT, STATE_EMERGENCY, STATE_MAINTENANCE, STATE_INIT
        ):
            self._state = STATE_FAULT
            self._sub_state = _SubState.NONE
            self._pending_target_state = None
            self._pending_setpoint_kw = None
            self._fault_retries_done = 0
            self._fault_reset_requested = False
            self._fault_retry_timer_s = now_s
            self._current_setpoint_kw = 0.0
            return self._make_result(
                prev_state=prev_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )

        # ----------------------------------------------------------------
        # Handle FAULT state (auto-retry and manual reset)
        # ----------------------------------------------------------------
        if self._state == STATE_FAULT:
            pcs_command, pcs_command_changed = self._handle_fault_state(
                now_s=now_s,
                pcs_fault_code=pcs_fault_code,
                pcs_state=pcs_state,
            )
            return self._make_result(
                prev_state=prev_state,
                pcs_command=pcs_command,
                pcs_command_changed=pcs_command_changed,
                error_msg="",
            )

        # ----------------------------------------------------------------
        # Handle MAINTENANCE state
        # ----------------------------------------------------------------
        if self._state == STATE_MAINTENANCE:
            if self._pending_maintenance_exit:
                self._pending_maintenance_exit = False
                self._state = STATE_IDLE
                self._sub_state = _SubState.NONE
                return self._make_result(
                    prev_state=prev_state,
                    pcs_command=PCS_CMD_NONE,
                    pcs_command_changed=False,
                    error_msg="",
                )
            return self._make_result(
                prev_state=prev_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )

        # ----------------------------------------------------------------
        # Priority 3: Sub-state timers (STARTING, STOPPING)
        # ----------------------------------------------------------------
        if self._sub_state == _SubState.STARTING:
            result = self._handle_starting_substate(
                now_s=now_s, pcs_state=pcs_state, prev_state=prev_state
            )
            if result is not None:
                return result

        if self._sub_state == _SubState.STOPPING:
            result = self._handle_stopping_substate(
                now_s=now_s, pcs_state=pcs_state, prev_state=prev_state
            )
            if result is not None:
                return result

        # ----------------------------------------------------------------
        # Priority 4a: INIT bootstrap — first tick
        # ----------------------------------------------------------------
        if self._state == STATE_INIT:
            if current_rtdb_state == STATE_MAINTENANCE:
                self._state = STATE_MAINTENANCE
                self._initialized = True
                return self._make_result(
                    prev_state=prev_state,
                    pcs_command=PCS_CMD_NONE,
                    pcs_command_changed=False,
                    error_msg="",
                )
            # Normal boot: go to IDLE
            self._state = STATE_IDLE
            self._initialized = True
            return self._make_result(
                prev_state=prev_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )

        # ----------------------------------------------------------------
        # Priority 4b: Pending maintenance enter
        # ----------------------------------------------------------------
        if self._pending_maintenance_enter:
            self._pending_maintenance_enter = False
            self._pending_target_state = None
            self._pending_setpoint_kw = None
            prev: int = self._state
            self._state = STATE_MAINTENANCE
            self._sub_state = _SubState.NONE
            self._current_setpoint_kw = 0.0
            cmd: int = PCS_CMD_NONE
            cmd_changed: bool = False
            if pcs_state not in (PCS_STATE_OFF,):
                cmd = PCS_CMD_OFF
                cmd_changed = True
            return self._make_result(
                prev_state=prev,
                pcs_command=cmd,
                pcs_command_changed=cmd_changed,
                error_msg="",
            )

        # ----------------------------------------------------------------
        # Priority 4c: Pending mode change
        # ----------------------------------------------------------------
        if self._pending_target_state is not None:
            target: int = self._pending_target_state
            self._pending_target_state = None

            if target == STATE_STANDBY:
                if self._state == STATE_IDLE:
                    # IDLE -> STANDBY: start PCS ON sequencing.
                    # State stays IDLE while STARTING; transitions to STANDBY when PCS confirms.
                    self._sub_state = _SubState.STARTING
                    self._transition_start_s = now_s
                    # _state remains STATE_IDLE — _handle_starting_substate will move to STANDBY
                    return self._make_result(
                        prev_state=prev_state,
                        pcs_command=PCS_CMD_ON,
                        pcs_command_changed=True,
                        error_msg="",
                    )
                elif self._state in (STATE_CHARGING, STATE_DISCHARGING):
                    # CHARGING/DISCHARGING -> STANDBY: zero setpoint, no PCS OFF
                    self._current_setpoint_kw = 0.0
                    self._pending_setpoint_kw = None
                    self._state = STATE_STANDBY
                    return self._make_result(
                        prev_state=prev_state,
                        pcs_command=PCS_CMD_NONE,
                        pcs_command_changed=False,
                        error_msg="",
                    )

            elif target == STATE_IDLE:
                # STANDBY/CHARGING/DISCHARGING -> IDLE: STOPPING sub-state
                self._current_setpoint_kw = 0.0
                self._pending_setpoint_kw = None
                self._sub_state = _SubState.STOPPING
                self._transition_start_s = now_s
                # Stay in current state until STOPPING completes
                return self._make_result(
                    prev_state=prev_state,
                    pcs_command=PCS_CMD_NONE,
                    pcs_command_changed=False,
                    error_msg="",
                )

        # ----------------------------------------------------------------
        # Priority 4d: Pending setpoint
        # ----------------------------------------------------------------
        if self._pending_setpoint_kw is not None:
            sp: float = self._pending_setpoint_kw
            self._pending_setpoint_kw = None
            prev_sp_state: int = self._state

            if sp > 0.0:
                self._state = STATE_CHARGING
            elif sp < 0.0:
                self._state = STATE_DISCHARGING
            else:
                self._state = STATE_STANDBY

            self._current_setpoint_kw = sp
            return self._make_result(
                prev_state=prev_sp_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )

        # ----------------------------------------------------------------
        # No transition — steady state
        # ----------------------------------------------------------------
        return self._make_result(
            prev_state=prev_state,
            pcs_command=PCS_CMD_NONE,
            pcs_command_changed=False,
            error_msg=error_msg,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_fault_state(
        self,
        now_s: float,
        pcs_fault_code: int,
        pcs_state: int,
    ) -> tuple[int, bool]:
        """Handle FAULT state auto-retry and manual reset logic.

        Returns:
            (pcs_command, pcs_command_changed)
        """
        # Manual reset overrides auto-retry countdown immediately
        if self._fault_reset_requested:
            self._fault_reset_requested = False
            # Send FAULT_RESET command
            return PCS_CMD_FAULT_RESET, True

        # Check if PCS recovered (fault cleared, PCS back to normal)
        if pcs_fault_code == 0 and pcs_state in (PCS_STATE_OFF, PCS_STATE_STANDBY, PCS_STATE_RUNNING):
            self._state = STATE_IDLE
            self._fault_retries_done = 0
            self._sub_state = _SubState.NONE
            return PCS_CMD_NONE, False

        # Auto-retry: send FAULT_RESET up to fault_retry_count times
        elapsed: float = now_s - self._fault_retry_timer_s
        if elapsed >= self._pcs_startup_timeout_s:
            if self._fault_retries_done < self._fault_retry_count:
                self._fault_retries_done += 1
                self._fault_retry_timer_s = now_s
                return PCS_CMD_FAULT_RESET, True

        # Retries exhausted or not yet time — no command
        return PCS_CMD_NONE, False

    def _handle_starting_substate(
        self,
        now_s: float,
        pcs_state: int,
        prev_state: int,
    ) -> Optional[TickResult]:
        """Handle STARTING sub-state: await PCS RUNNING or timeout to FAULT.

        Returns:
            TickResult if transition occurred, None to continue normal processing.
        """
        elapsed: float = now_s - self._transition_start_s

        if pcs_state == PCS_STATE_RUNNING:
            # PCS confirmed running — transition to STANDBY now
            starting_state: int = self._state
            self._sub_state = _SubState.NONE
            self._state = STATE_STANDBY
            return self._make_result(
                prev_state=starting_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )

        if elapsed >= self._pcs_startup_timeout_s:
            # Timeout — enter FAULT
            old_state: int = self._state
            self._state = STATE_FAULT
            self._sub_state = _SubState.NONE
            self._fault_retries_done = 0
            self._fault_reset_requested = False
            self._fault_retry_timer_s = now_s
            return self._make_result(
                prev_state=old_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="PCS startup timeout",
            )

        # Still waiting — return current state with no command
        return self._make_result(
            prev_state=prev_state,
            pcs_command=PCS_CMD_NONE,
            pcs_command_changed=False,
            error_msg="",
        )

    def _handle_stopping_substate(
        self,
        now_s: float,
        pcs_state: int,
        prev_state: int,
    ) -> Optional[TickResult]:
        """Handle STOPPING sub-state for STANDBY->IDLE transition.

        Phase 14 ramp shortcut:
          - t=0: Enter STOPPING (setpoint already zeroed)
          - t>=2s: Send PCS OFF
          - After PCS OFF + pcs_shutdown_timeout_s: Enter IDLE

        Returns:
            TickResult if transition occurred, None to continue normal processing.
        """
        _RAMP_HOLD_S: float = 2.0
        elapsed: float = now_s - self._transition_start_s

        if elapsed < _RAMP_HOLD_S:
            # Still in ramp-hold period — zero setpoint, wait
            return self._make_result(
                prev_state=prev_state,
                pcs_command=PCS_CMD_NONE,
                pcs_command_changed=False,
                error_msg="",
            )

        # Ramp hold done — PCS OFF if not already off, otherwise complete transition
        if pcs_state != PCS_STATE_OFF:
            return self._make_result(
                prev_state=prev_state,
                pcs_command=PCS_CMD_OFF,
                pcs_command_changed=True,
                error_msg="",
            )

        # PCS is OFF — shutdown confirmed, enter IDLE
        self._state = STATE_IDLE
        self._sub_state = _SubState.NONE
        return self._make_result(
            prev_state=prev_state,
            pcs_command=PCS_CMD_NONE,
            pcs_command_changed=False,
            error_msg="",
        )

    def _make_result(
        self,
        prev_state: int,
        pcs_command: int,
        pcs_command_changed: bool,
        error_msg: str,
    ) -> TickResult:
        """Build a TickResult from current state.

        Args:
            prev_state: State before this tick (for state_changed detection).
            pcs_command: PCS command to issue.
            pcs_command_changed: Whether pcs_command_seq should be incremented.
            error_msg: Error description if a command was rejected.

        Returns:
            Populated TickResult dataclass.
        """
        state_changed: bool = self._state != prev_state
        return TickResult(
            state=self._state,
            setpoint_kw=self._current_setpoint_kw,
            pcs_command=pcs_command,
            pcs_command_changed=pcs_command_changed,
            state_changed=state_changed,
            prev_state=prev_state if state_changed else None,
            error_msg=error_msg,
        )
