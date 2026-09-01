"""PCS state machine with configurable transition delays and power ramping."""

from __future__ import annotations

import logging
from enum import IntEnum

log: logging.Logger = logging.getLogger(__name__)


class PCSState(IntEnum):
    """PCS operating states matching register 0x0030 values."""

    STANDBY = 0
    STARTING = 1
    RUNNING = 2
    STOPPING = 3
    FAULT = 4


class PCSStateMachine:
    """Models PCS state transitions and power output ramping.

    States: STANDBY -> STARTING -> RUNNING -> STOPPING -> STANDBY
                                                    |-> FAULT

    Power ramps toward setpoint at a configurable rate (pct/s of rated power).
    """

    def __init__(
        self,
        rated_power_kw: float = 50.0,
        startup_delay_s: float = 2.0,
        shutdown_delay_s: float = 1.5,
        ramp_rate_pct_per_s: float = 10.0,
    ) -> None:
        self.state: PCSState = PCSState.STANDBY
        self.rated_power_kw: float = rated_power_kw
        self.startup_delay_s: float = startup_delay_s
        self.shutdown_delay_s: float = shutdown_delay_s
        self.ramp_rate_pct_per_s: float = ramp_rate_pct_per_s

        self._power_setpoint_kw: float = 0.0
        self._current_power_kw: float = 0.0
        self._transition_timer_s: float = 0.0
        self._fault_code: int = 0

    @property
    def power_setpoint_kw(self) -> float:
        """Current power setpoint in kW."""
        return self._power_setpoint_kw

    @property
    def current_power_kw(self) -> float:
        """Current actual output power in kW."""
        return self._current_power_kw

    @property
    def fault_code(self) -> int:
        """Active fault code (0 = no fault)."""
        return self._fault_code

    @property
    def load_factor(self) -> float:
        """Current load as fraction of rated power [0.0, 1.0]."""
        if self.rated_power_kw == 0.0:
            return 0.0
        return min(1.0, abs(self._current_power_kw) / self.rated_power_kw)

    def command_start(self) -> None:
        """Request PCS start. Only effective in STANDBY state."""
        if self.state == PCSState.STANDBY:
            log.info("PCS command: START (STANDBY -> STARTING)")
            self.state = PCSState.STARTING
            self._transition_timer_s = 0.0

    def command_stop(self) -> None:
        """Request PCS stop. Only effective in RUNNING state."""
        if self.state == PCSState.RUNNING:
            log.info("PCS command: STOP (RUNNING -> STOPPING)")
            self.state = PCSState.STOPPING
            self._transition_timer_s = 0.0

    def command_fault_reset(self) -> None:
        """Reset fault and return to STANDBY. Only effective in FAULT state."""
        if self.state == PCSState.FAULT:
            log.info("PCS command: FAULT RESET (FAULT -> STANDBY)")
            self.state = PCSState.STANDBY
            self._fault_code = 0
            self._current_power_kw = 0.0
            self._power_setpoint_kw = 0.0

    def set_fault(self, code: int = 1) -> None:
        """Force PCS into FAULT state with the given fault code."""
        log.warning("PCS FAULT: code=%d (from state %s)", code, self.state.name)
        self.state = PCSState.FAULT
        self._fault_code = code
        self._current_power_kw = 0.0

    def set_power_setpoint(self, raw_value: int) -> None:
        """Set power setpoint from a raw Modbus register value.

        Handles two's complement for signed 16-bit values:
        values > 32767 are interpreted as negative (discharge).

        Args:
            raw_value: Raw uint16 register value (0-65535).
        """
        if self.state != PCSState.RUNNING:
            log.debug(
                "Power setpoint ignored: PCS not in RUNNING state (state=%s)", self.state.name
            )
            return
        # Two's complement: values > 32767 are negative
        if raw_value > 32767:
            signed_value: int = raw_value - 65536
        else:
            signed_value = raw_value
        # Scale: raw = kW * 10
        self._power_setpoint_kw = signed_value / 10.0
        log.info("PCS power setpoint: %.1f kW (raw=%d)", self._power_setpoint_kw, raw_value)

    def tick(self, dt_s: float) -> None:
        """Advance state machine by dt_s seconds.

        Handles transition timers and power ramping.
        """
        if self.state == PCSState.STARTING:
            self._transition_timer_s += dt_s
            if self._transition_timer_s >= self.startup_delay_s:
                log.info("PCS transition: STARTING -> RUNNING")
                self.state = PCSState.RUNNING
                self._transition_timer_s = 0.0

        elif self.state == PCSState.STOPPING:
            self._transition_timer_s += dt_s
            # Ramp power toward zero during shutdown
            self._ramp_power(0.0, dt_s)
            if self._transition_timer_s >= self.shutdown_delay_s:
                log.info("PCS transition: STOPPING -> STANDBY")
                self.state = PCSState.STANDBY
                self._current_power_kw = 0.0
                self._power_setpoint_kw = 0.0
                self._transition_timer_s = 0.0

        elif self.state == PCSState.RUNNING:
            self._ramp_power(self._power_setpoint_kw, dt_s)

        elif self.state == PCSState.STANDBY:
            self._current_power_kw = 0.0

        elif self.state == PCSState.FAULT:
            self._current_power_kw = 0.0

    def _ramp_power(self, target_kw: float, dt_s: float) -> None:
        """Ramp current power toward target at configured rate."""
        max_delta: float = self.rated_power_kw * (self.ramp_rate_pct_per_s / 100.0) * dt_s
        diff: float = target_kw - self._current_power_kw
        if abs(diff) <= max_delta:
            self._current_power_kw = target_kw
        elif diff > 0:
            self._current_power_kw += max_delta
        else:
            self._current_power_kw -= max_delta
