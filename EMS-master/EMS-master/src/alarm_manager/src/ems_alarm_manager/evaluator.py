"""AlarmInstance dataclass, IEC 62682 lifecycle state constants, and AlarmEvaluator.

This module defines:
- Lifecycle state constants (plain strings matching IEC 62682 names)
- AlarmInstance dataclass with pre-computed hysteresis clear thresholds
- build_alarm_instances() to construct per-rule instances from config
- AlarmEvaluator: core state-machine evaluation engine (pure logic, no I/O)
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any

_log: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IEC 62682 alarm lifecycle state constants (plain strings, not Enum)
# ---------------------------------------------------------------------------

STATE_NORMAL: str = "NORMAL"
"""Alarm is not active — signal within normal operating range."""

STATE_ACTIVE_UNACKED: str = "ACTIVE_UNACKED"
"""Alarm condition present and not yet acknowledged by operator."""

STATE_ACTIVE_ACKED: str = "ACTIVE_ACKED"
"""Alarm condition present and acknowledged by operator."""

STATE_CLEARED_UNACKED: str = "CLEARED_UNACKED"
"""Alarm condition cleared but operator has not yet acknowledged the RTN."""

STATE_RTN: str = "RTN"
"""Return-to-normal: alarm cleared and acknowledged. Transitions back to NORMAL."""


# ---------------------------------------------------------------------------
# AlarmInstance dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AlarmInstance:
    """Per-alarm-rule lifecycle state.

    Holds the static configuration (thresholds, severity, hysteresis) and
    runtime state (current lifecycle state, timestamps) for a single alarm
    rule. Pre-computes clear thresholds in __post_init__ to avoid repeated
    arithmetic in the hot evaluation loop.

    Attributes:
        alarm_id: Unique identifier for this alarm rule (matches config key).
        signal: RTDB signal path (e.g. "bms.cell_voltage_max").
        severity: IEC 62682 severity ("warning" | "action" | "protection").
        high_threshold: Signal value above which alarm activates, or None.
        low_threshold: Signal value below which alarm activates, or None.
        hysteresis_pct: Hysteresis band as % of threshold (prevents chattering).
        delay_ms: Activation delay in ms (signal must exceed threshold this long).
        enabled: Whether this alarm rule is active.
        high_clear: Pre-computed clear threshold for high alarms (below this = cleared).
        low_clear: Pre-computed clear threshold for low alarms (above this = cleared).
        state: Current IEC 62682 lifecycle state constant.
        exceeded_since_ms: Monotonic ms timestamp when threshold was first exceeded, or None.
        current_value: Most recent resolved signal value (0.0 if never resolved).
        activated_at: Wall-clock ms when alarm entered ACTIVE state (0 = not yet).
        acknowledged_at: Wall-clock ms when alarm was acknowledged (0 = not yet).
        cleared_at: Wall-clock ms when alarm condition cleared (0 = not yet).
        rtn_at: Wall-clock ms when alarm entered RTN state (0 = not yet).
    """

    alarm_id: str
    signal: str
    severity: str  # "warning" | "action" | "protection"
    high_threshold: float | None
    low_threshold: float | None
    hysteresis_pct: float
    delay_ms: int
    enabled: bool

    # Pre-computed clear thresholds (set in __post_init__)
    high_clear: float | None = None
    low_clear: float | None = None

    # Runtime state
    state: str = STATE_NORMAL
    exceeded_since_ms: float | None = None
    current_value: float = 0.0

    # Timestamps (wall-clock ms, 0 = not yet set)
    activated_at: int = 0
    acknowledged_at: int = 0
    cleared_at: int = 0
    rtn_at: int = 0

    def __post_init__(self) -> None:
        """Pre-compute hysteresis clear thresholds from configured percentages."""
        if self.high_threshold is not None:
            self.high_clear = (
                self.high_threshold - abs(self.high_threshold) * self.hysteresis_pct / 100.0
            )
        if self.low_threshold is not None:
            self.low_clear = (
                self.low_threshold + abs(self.low_threshold) * self.hysteresis_pct / 100.0
            )


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def build_alarm_instances(config: dict[str, Any]) -> dict[str, AlarmInstance]:
    """Create AlarmInstance objects from a loaded alarm config dict.

    Iterates config["rules"] and creates one AlarmInstance per rule.
    Per-rule hysteresis_pct and delay_ms override defaults if present.

    Args:
        config: Validated alarm config dict from load_alarm_config().

    Returns:
        Dict keyed by alarm_id (rule name), values are AlarmInstance objects.
    """
    defaults: dict[str, Any] = config.get("defaults", {})
    default_hysteresis_pct: float = float(defaults.get("hysteresis_pct", 2.0))
    default_delay_ms: int = int(defaults.get("delay_ms", 5000))

    rules: dict[str, Any] = config.get("rules", {})
    instances: dict[str, AlarmInstance] = {}

    for alarm_id, rule in rules.items():
        high_threshold: float | None = rule.get("high_threshold")
        low_threshold: float | None = rule.get("low_threshold")

        if high_threshold is None and low_threshold is None:
            _log.warning(
                "Alarm rule '%s' has neither high_threshold nor low_threshold — "
                "this alarm will never activate",
                alarm_id,
            )

        # Per-rule overrides take precedence over defaults
        hysteresis_pct: float = float(rule.get("hysteresis", default_hysteresis_pct))
        delay_ms: int = int(rule.get("delay_ms", default_delay_ms))

        instances[alarm_id] = AlarmInstance(
            alarm_id=alarm_id,
            signal=rule["signal"],
            severity=rule["severity"],
            high_threshold=float(high_threshold) if high_threshold is not None else None,
            low_threshold=float(low_threshold) if low_threshold is not None else None,
            hysteresis_pct=hysteresis_pct,
            delay_ms=delay_ms,
            enabled=bool(rule.get("enabled", True)),
        )

    return instances


# ---------------------------------------------------------------------------
# Threshold helpers (pure functions)
# ---------------------------------------------------------------------------


def _check_exceeds(inst: AlarmInstance, value: float) -> bool:
    """Return True if value exceeds the alarm's configured threshold(s)."""
    if inst.high_threshold is not None and value > inst.high_threshold:
        return True
    if inst.low_threshold is not None and value < inst.low_threshold:
        return True
    return False


def _check_cleared(inst: AlarmInstance, value: float) -> bool:
    """Return True if value has cleared back past the hysteresis band.

    For a high threshold alarm: cleared when value < high_clear.
    For a low threshold alarm: cleared when value > low_clear.
    An alarm with both thresholds is only cleared when BOTH conditions clear.
    """
    if inst.high_threshold is not None and inst.high_clear is not None:
        if value >= inst.high_clear:
            return False  # still above clear threshold
    if inst.low_threshold is not None and inst.low_clear is not None:
        if value <= inst.low_clear:
            return False  # still below clear threshold
    return True


# ---------------------------------------------------------------------------
# AlarmEvaluator — IEC 62682 state machine
# ---------------------------------------------------------------------------


class AlarmEvaluator:
    """Evaluate all alarm rules each tick and drive IEC 62682 lifecycle transitions.

    This class is pure logic — no ZMQ, no RTDB reads, no file I/O. The caller
    provides resolved signal values and a monotonic timestamp; the evaluator
    updates AlarmInstance state in-place and returns event dicts.

    Args:
        instances: Dict of alarm_id -> AlarmInstance, as built by build_alarm_instances().
    """

    def __init__(self, instances: dict[str, AlarmInstance]) -> None:
        self._instances: dict[str, AlarmInstance] = instances

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_tick(
        self,
        values: dict[str, float | None],
        now_ms: float,
    ) -> list[dict]:
        """Evaluate all alarm rules for one tick.

        Args:
            values: Dict of signal_path -> resolved value (None = unavailable).
            now_ms: Monotonic timestamp in milliseconds for delay calculations.

        Returns:
            List of event dicts generated this tick (may be empty).
        """
        events: list[dict] = []
        for alarm_id, inst in self._instances.items():
            if not inst.enabled:
                continue
            value: float | None = values.get(inst.signal)
            tick_events: list[dict] = self._evaluate_one(inst, value, now_ms)
            events.extend(tick_events)
        return events

    def acknowledge(self, alarm_id: str) -> dict:
        """Acknowledge an alarm by ID.

        Valid in states: ACTIVE_UNACKED (-> ACTIVE_ACKED) and CLEARED_UNACKED (-> RTN).

        Args:
            alarm_id: Alarm identifier matching an AlarmInstance key.

        Returns:
            Result dict with keys: status ("ok"|"error"), from_state, to_state, message.
        """
        inst: AlarmInstance | None = self._instances.get(alarm_id)
        if inst is None:
            return {
                "status": "error",
                "from_state": None,
                "to_state": None,
                "message": f"Unknown alarm_id: {alarm_id!r}",
            }

        now_wall_ms: int = int(time.monotonic() * 1000)
        from_state: str = inst.state

        if inst.state == STATE_ACTIVE_UNACKED:
            inst.state = STATE_ACTIVE_ACKED
            inst.acknowledged_at = now_wall_ms
            return {
                "status": "ok",
                "from_state": from_state,
                "to_state": STATE_ACTIVE_ACKED,
                "message": "acknowledged",
            }

        if inst.state == STATE_CLEARED_UNACKED:
            inst.state = STATE_RTN
            inst.acknowledged_at = now_wall_ms
            inst.rtn_at = now_wall_ms
            return {
                "status": "ok",
                "from_state": from_state,
                "to_state": STATE_RTN,
                "message": "acknowledged — RTN",
            }

        return {
            "status": "error",
            "from_state": from_state,
            "to_state": from_state,
            "message": f"Cannot acknowledge alarm in state {inst.state!r}",
        }

    def get_active_alarms(self) -> list[dict]:
        """Return all non-NORMAL alarm instances as summary dicts.

        Returns:
            List of dicts with alarm_id, state, severity, signal, current_value.
        """
        result: list[dict] = []
        for alarm_id, inst in self._instances.items():
            if inst.state != STATE_NORMAL:
                result.append(
                    {
                        "alarm_id": alarm_id,
                        "state": inst.state,
                        "severity": inst.severity,
                        "signal": inst.signal,
                        "current_value": inst.current_value,
                    }
                )
        return result

    def get_alarm_config(self) -> list[dict]:
        """Return all alarm rules as configuration summary dicts.

        Returns:
            List of dicts with alarm_id, signal, severity, high_threshold,
            low_threshold, hysteresis_pct, delay_ms, enabled.
        """
        result: list[dict] = []
        for alarm_id, inst in self._instances.items():
            result.append(
                {
                    "alarm_id": alarm_id,
                    "signal": inst.signal,
                    "severity": inst.severity,
                    "high_threshold": inst.high_threshold,
                    "low_threshold": inst.low_threshold,
                    "hysteresis_pct": inst.hysteresis_pct,
                    "delay_ms": inst.delay_ms,
                    "enabled": inst.enabled,
                }
            )
        return result

    # ------------------------------------------------------------------
    # Internal per-instance evaluation
    # ------------------------------------------------------------------

    def _evaluate_one(
        self,
        inst: AlarmInstance,
        value: float | None,
        now_ms: float,
    ) -> list[dict]:
        """Drive one AlarmInstance through one tick of the IEC 62682 state machine.

        Args:
            inst: AlarmInstance to evaluate (mutated in-place).
            value: Resolved signal value, or None if unavailable.
            now_ms: Monotonic timestamp in milliseconds.

        Returns:
            List of event dicts (0 or 1 per tick for normal transitions).
        """
        # None value — keep NORMAL, clear delay timer
        if value is None:
            inst.exceeded_since_ms = None
            return []

        inst.current_value = value

        # RTN auto-transitions to NORMAL before any other processing
        if inst.state == STATE_RTN:
            inst.state = STATE_NORMAL
            inst.activated_at = 0
            inst.acknowledged_at = 0
            inst.cleared_at = 0
            inst.rtn_at = 0
            inst.exceeded_since_ms = None
            # Fall through: re-evaluate as NORMAL for this tick

        if inst.state == STATE_NORMAL:
            return self._handle_normal(inst, value, now_ms)

        if inst.state == STATE_ACTIVE_UNACKED:
            return self._handle_active_unacked(inst, value, now_ms)

        if inst.state == STATE_ACTIVE_ACKED:
            return self._handle_active_acked(inst, value, now_ms)

        if inst.state == STATE_CLEARED_UNACKED:
            return self._handle_cleared_unacked(inst, value, now_ms)

        # RTN is fully handled above — should not reach here
        return []

    def _handle_normal(
        self,
        inst: AlarmInstance,
        value: float,
        now_ms: float,
    ) -> list[dict]:
        """Handle NORMAL state: start/continue delay timer, transition to ACTIVE_UNACKED."""
        if not _check_exceeds(inst, value):
            # Signal within normal range — reset delay timer
            inst.exceeded_since_ms = None
            return []

        # Signal exceeds threshold
        if inst.exceeded_since_ms is None:
            inst.exceeded_since_ms = now_ms

        elapsed_ms: float = now_ms - inst.exceeded_since_ms
        if elapsed_ms < inst.delay_ms:
            # Delay not yet expired
            return []

        # Delay expired — activate alarm
        inst.state = STATE_ACTIVE_UNACKED
        inst.activated_at = int(now_ms)
        inst.exceeded_since_ms = None
        return [self._make_event("alarm_activated", inst)]

    def _handle_active_unacked(
        self,
        inst: AlarmInstance,
        value: float,
        now_ms: float,
    ) -> list[dict]:
        """Handle ACTIVE_UNACKED state: check for signal clear or stay active."""
        if not _check_cleared(inst, value):
            # Signal still in alarm band — no change
            return []

        # Signal has cleared
        inst.state = STATE_CLEARED_UNACKED
        inst.cleared_at = int(now_ms)
        return [self._make_event("alarm_cleared", inst)]

    def _handle_active_acked(
        self,
        inst: AlarmInstance,
        value: float,
        now_ms: float,
    ) -> list[dict]:
        """Handle ACTIVE_ACKED state: check for signal clear (RTN path)."""
        if not _check_cleared(inst, value):
            return []

        inst.state = STATE_RTN
        inst.cleared_at = int(now_ms)
        inst.rtn_at = int(now_ms)
        return [self._make_event("alarm_rtn", inst)]

    def _handle_cleared_unacked(
        self,
        inst: AlarmInstance,
        value: float,
        now_ms: float,
    ) -> list[dict]:
        """Handle CLEARED_UNACKED state: re-activation check or wait for ack."""
        if _check_exceeds(inst, value):
            # Signal re-exceeded — re-activate immediately (no delay needed)
            inst.state = STATE_ACTIVE_UNACKED
            inst.activated_at = int(now_ms)
            return [self._make_event("alarm_activated", inst)]

        # Signal still clear — wait for operator acknowledgement
        return []

    @staticmethod
    def _make_event(event_type: str, inst: AlarmInstance) -> dict:
        """Build an event dict for the given transition type and alarm instance.

        Args:
            event_type: One of alarm_activated, alarm_acknowledged, alarm_cleared, alarm_rtn.
            inst: AlarmInstance after state transition (state already updated).

        Returns:
            Event dict with all required fields.
        """
        threshold: float | None = (
            inst.high_threshold if inst.high_threshold is not None else inst.low_threshold
        )
        return {
            "event_type": event_type,
            "alarm_id": inst.alarm_id,
            "signal": inst.signal,
            "severity": inst.severity,
            "state": inst.state,
            "value": inst.current_value,
            "threshold": threshold,
        }
