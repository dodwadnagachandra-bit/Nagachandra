"""Tests for AlarmEvaluator — IEC 62682 lifecycle state machine.

TDD RED: All tests written against the interface spec before implementation.
"""

from __future__ import annotations

import pytest

from ems_alarm_manager.evaluator import (
    AlarmEvaluator,
    AlarmInstance,
    STATE_ACTIVE_ACKED,
    STATE_ACTIVE_UNACKED,
    STATE_CLEARED_UNACKED,
    STATE_NORMAL,
    STATE_RTN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_high_alarm(
    alarm_id: str = "test_high",
    signal: str = "bms.cell_voltage_max",
    severity: str = "warning",
    high_threshold: float = 3.65,
    hysteresis_pct: float = 2.0,
    delay_ms: int = 0,
    enabled: bool = True,
) -> AlarmInstance:
    """Create a high-threshold AlarmInstance for testing."""
    return AlarmInstance(
        alarm_id=alarm_id,
        signal=signal,
        severity=severity,
        high_threshold=high_threshold,
        low_threshold=None,
        hysteresis_pct=hysteresis_pct,
        delay_ms=delay_ms,
        enabled=enabled,
    )


def make_low_alarm(
    alarm_id: str = "test_low",
    signal: str = "bms.cell_voltage_min",
    severity: str = "protection",
    low_threshold: float = 2.8,
    hysteresis_pct: float = 2.0,
    delay_ms: int = 0,
    enabled: bool = True,
) -> AlarmInstance:
    """Create a low-threshold AlarmInstance for testing."""
    return AlarmInstance(
        alarm_id=alarm_id,
        signal=signal,
        severity=severity,
        high_threshold=None,
        low_threshold=low_threshold,
        hysteresis_pct=hysteresis_pct,
        delay_ms=delay_ms,
        enabled=enabled,
    )


def make_evaluator(*instances: AlarmInstance) -> AlarmEvaluator:
    """Build AlarmEvaluator from a list of AlarmInstance objects."""
    return AlarmEvaluator({inst.alarm_id: inst for inst in instances})


# ---------------------------------------------------------------------------
# NORMAL state + delay timer
# ---------------------------------------------------------------------------


class TestNormalState:
    def test_normal_no_exceed(self) -> None:
        """Value within limits stays NORMAL, produces no events."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.60}, now_ms=1000.0)
        assert events == []
        assert alarm.state == STATE_NORMAL

    def test_normal_exceed_starts_delay(self) -> None:
        """Value exceeds threshold — exceeded_since_ms is set, stays NORMAL (delay not expired)."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=5000)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert events == []
        assert alarm.state == STATE_NORMAL
        assert alarm.exceeded_since_ms == 1000.0

    def test_normal_exceed_delay_not_expired(self) -> None:
        """Value exceeds for less than delay_ms — stays NORMAL."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=5000)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=5999.0)
        assert events == []
        assert alarm.state == STATE_NORMAL

    def test_normal_exceed_delay_expired(self) -> None:
        """Value exceeds for >= delay_ms — transitions to ACTIVE_UNACKED, returns alarm_activated event."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=5000)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=6000.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "alarm_activated"
        assert alarm.state == STATE_ACTIVE_UNACKED
        assert alarm.activated_at == 6000

    def test_delay_timer_resets_on_recovery(self) -> None:
        """Signal recovers before delay expires — exceeded_since_ms resets, must wait full delay_ms again."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=5000)
        ev: AlarmEvaluator = make_evaluator(alarm)
        # Exceed for 2s
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=3000.0)
        # Recover to normal (well below high_clear = 3.65 - 2% = 3.577)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=3001.0)
        assert alarm.exceeded_since_ms is None
        # Exceed again — must wait full delay
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=3002.0)
        assert alarm.exceeded_since_ms == 3002.0
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=8002.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "alarm_activated"

    def test_normal_exceed_zero_delay(self) -> None:
        """delay_ms=0 means alarm activates immediately on first exceeding tick."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "alarm_activated"
        assert alarm.state == STATE_ACTIVE_UNACKED


# ---------------------------------------------------------------------------
# ACTIVE_UNACKED state
# ---------------------------------------------------------------------------


class TestActiveUnacked:
    def test_active_unacked_signal_still_exceeding(self) -> None:
        """Stays ACTIVE_UNACKED if signal still exceeds threshold, no new events."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert alarm.state == STATE_ACTIVE_UNACKED
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=2000.0)
        assert events == []
        assert alarm.state == STATE_ACTIVE_UNACKED

    def test_active_unacked_signal_clears(self) -> None:
        """Signal drops below clear threshold — transitions to CLEARED_UNACKED, returns alarm_cleared event."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0, hysteresis_pct=2.0)
        # high_threshold=3.65, high_clear = 3.65 - 0.02*3.65 = 3.577
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        # Drop below 3.577 to clear
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=2000.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "alarm_cleared"
        assert alarm.state == STATE_CLEARED_UNACKED
        assert alarm.cleared_at == 2000

    def test_active_unacked_acknowledge(self) -> None:
        """acknowledge() on ACTIVE_UNACKED transitions to ACTIVE_ACKED, returns alarm_acknowledged event."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        result: dict = ev.acknowledge("test_high")
        assert result["status"] == "ok"
        assert result["from_state"] == STATE_ACTIVE_UNACKED
        assert result["to_state"] == STATE_ACTIVE_ACKED
        assert alarm.state == STATE_ACTIVE_ACKED
        assert alarm.acknowledged_at > 0


# ---------------------------------------------------------------------------
# ACTIVE_ACKED state
# ---------------------------------------------------------------------------


class TestActiveAcked:
    def test_active_acked_signal_still_exceeding(self) -> None:
        """Stays ACTIVE_ACKED if signal still exceeds threshold, no new events."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        ev.acknowledge("test_high")
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=2000.0)
        assert events == []
        assert alarm.state == STATE_ACTIVE_ACKED

    def test_active_acked_signal_clears(self) -> None:
        """Signal drops below clear threshold — transitions to RTN, returns alarm_rtn event."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0, hysteresis_pct=2.0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        ev.acknowledge("test_high")
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=2000.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "alarm_rtn"
        assert alarm.state == STATE_RTN
        assert alarm.cleared_at == 2000
        assert alarm.rtn_at == 2000

    def test_active_acked_auto_rtn_to_normal(self) -> None:
        """After RTN transition, next evaluate_tick resets to NORMAL."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0, hysteresis_pct=2.0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        ev.acknowledge("test_high")
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=2000.0)
        assert alarm.state == STATE_RTN
        # Next tick auto-transitions RTN -> NORMAL
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=3000.0)
        assert alarm.state == STATE_NORMAL
        assert alarm.activated_at == 0
        assert alarm.acknowledged_at == 0
        assert alarm.cleared_at == 0
        assert alarm.rtn_at == 0


# ---------------------------------------------------------------------------
# CLEARED_UNACKED state
# ---------------------------------------------------------------------------


class TestClearedUnacked:
    def test_cleared_unacked_acknowledge(self) -> None:
        """acknowledge() on CLEARED_UNACKED transitions to RTN, returns alarm_rtn event."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0, hysteresis_pct=2.0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        # Signal clears before ack
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=2000.0)
        assert alarm.state == STATE_CLEARED_UNACKED
        result: dict = ev.acknowledge("test_high")
        assert result["status"] == "ok"
        assert result["from_state"] == STATE_CLEARED_UNACKED
        assert result["to_state"] == STATE_RTN
        assert alarm.state == STATE_RTN
        assert alarm.acknowledged_at > 0
        assert alarm.rtn_at > 0

    def test_cleared_unacked_signal_re_exceeds(self) -> None:
        """Signal re-exceeds threshold from CLEARED_UNACKED — transitions back to ACTIVE_UNACKED immediately (no delay)."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=5000, hysteresis_pct=2.0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        # Activate
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=0.0)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=5001.0)
        assert alarm.state == STATE_ACTIVE_UNACKED
        # Signal clears — transitions to CLEARED_UNACKED
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=5002.0)
        assert alarm.state == STATE_CLEARED_UNACKED
        # Signal re-exceeds without any delay
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=5003.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "alarm_activated"
        assert alarm.state == STATE_ACTIVE_UNACKED


# ---------------------------------------------------------------------------
# RTN state
# ---------------------------------------------------------------------------


class TestRtnState:
    def test_rtn_auto_normal(self) -> None:
        """RTN state auto-transitions to NORMAL on next evaluate_tick, all timestamps reset to 0."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0, hysteresis_pct=2.0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        ev.acknowledge("test_high")
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=2000.0)
        assert alarm.state == STATE_RTN
        # Auto-transition
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=3000.0)
        assert alarm.state == STATE_NORMAL
        assert alarm.activated_at == 0
        assert alarm.acknowledged_at == 0
        assert alarm.cleared_at == 0
        assert alarm.rtn_at == 0
        assert alarm.exceeded_since_ms is None


# ---------------------------------------------------------------------------
# Hysteresis
# ---------------------------------------------------------------------------


class TestHysteresis:
    def test_hysteresis_high_threshold(self) -> None:
        """high_threshold=3.65, hysteresis_pct=2 -> high_clear≈3.577. Value at 3.60 keeps alarm ACTIVE."""
        alarm: AlarmInstance = make_high_alarm(
            high_threshold=3.65, hysteresis_pct=2.0, delay_ms=0
        )
        # high_clear = 3.65 - 0.02*3.65 = 3.577
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert alarm.state == STATE_ACTIVE_UNACKED
        # 3.60 is below 3.65 but above 3.577 — should stay ACTIVE_UNACKED
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.60}, now_ms=2000.0)
        assert events == []
        assert alarm.state == STATE_ACTIVE_UNACKED

    def test_hysteresis_low_threshold(self) -> None:
        """low_threshold=2.8, hysteresis_pct=2 -> low_clear≈2.856. Value at 2.83 keeps alarm ACTIVE."""
        alarm: AlarmInstance = make_low_alarm(
            low_threshold=2.8, hysteresis_pct=2.0, delay_ms=0
        )
        # low_clear = 2.8 + 0.02*2.8 = 2.856
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_min": 2.70}, now_ms=1000.0)
        assert alarm.state == STATE_ACTIVE_UNACKED
        # 2.83 is above 2.8 but below 2.856 — should stay ACTIVE_UNACKED
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_min": 2.83}, now_ms=2000.0)
        assert events == []
        assert alarm.state == STATE_ACTIVE_UNACKED


# ---------------------------------------------------------------------------
# Disabled alarms
# ---------------------------------------------------------------------------


class TestDisabledAlarms:
    def test_disabled_alarm_stays_normal(self) -> None:
        """enabled=False, value exceeds threshold — stays NORMAL."""
        alarm: AlarmInstance = make_high_alarm(enabled=False, delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert events == []
        assert alarm.state == STATE_NORMAL

    def test_disabled_alarm_no_event(self) -> None:
        """Disabled alarms produce no events across multiple ticks."""
        alarm: AlarmInstance = make_high_alarm(enabled=False, delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        for t in [1000.0, 2000.0, 3000.0]:
            events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 4.0}, now_ms=t)
            assert events == []
        assert alarm.state == STATE_NORMAL


# ---------------------------------------------------------------------------
# None signal value
# ---------------------------------------------------------------------------


class TestNoneSignal:
    def test_none_value_stays_normal(self) -> None:
        """Resolver returns None — alarm stays NORMAL, no events, delay timer cleared."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=5000)
        ev: AlarmEvaluator = make_evaluator(alarm)
        # First start exceeding
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert alarm.exceeded_since_ms == 1000.0
        # Then signal resolves to None
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": None}, now_ms=2000.0)
        assert events == []
        assert alarm.state == STATE_NORMAL
        assert alarm.exceeded_since_ms is None

    def test_none_value_missing_key_stays_normal(self) -> None:
        """Signal key missing from values dict — alarm stays NORMAL (same as None)."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({}, now_ms=1000.0)
        assert events == []
        assert alarm.state == STATE_NORMAL


# ---------------------------------------------------------------------------
# Event dict content
# ---------------------------------------------------------------------------


class TestEventContent:
    def test_event_includes_severity(self) -> None:
        """alarm_activated event dict includes correct severity string."""
        alarm: AlarmInstance = make_high_alarm(severity="action", delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert events[0]["severity"] == "action"

    def test_event_includes_all_fields(self) -> None:
        """alarm_activated event dict has all required fields."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        event: dict = events[0]
        assert "event_type" in event
        assert "alarm_id" in event
        assert "signal" in event
        assert "severity" in event
        assert "state" in event
        assert "value" in event
        assert "threshold" in event

    def test_event_alarm_id_matches(self) -> None:
        """Event alarm_id matches AlarmInstance.alarm_id."""
        alarm: AlarmInstance = make_high_alarm(alarm_id="my_alarm", delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        events: list[dict] = ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        assert events[0]["alarm_id"] == "my_alarm"


# ---------------------------------------------------------------------------
# Acknowledge validation
# ---------------------------------------------------------------------------


class TestAcknowledgeValidation:
    def test_acknowledge_normal_rejected(self) -> None:
        """acknowledge() on NORMAL alarm returns error status."""
        alarm: AlarmInstance = make_high_alarm()
        ev: AlarmEvaluator = make_evaluator(alarm)
        result: dict = ev.acknowledge("test_high")
        assert result["status"] == "error"
        assert alarm.state == STATE_NORMAL

    def test_acknowledge_active_acked_rejected(self) -> None:
        """acknowledge() on ACTIVE_ACKED alarm returns error status."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        ev.acknowledge("test_high")
        assert alarm.state == STATE_ACTIVE_ACKED
        result: dict = ev.acknowledge("test_high")
        assert result["status"] == "error"

    def test_acknowledge_rtn_rejected(self) -> None:
        """acknowledge() on RTN alarm returns error status."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0, hysteresis_pct=2.0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        ev.acknowledge("test_high")
        ev.evaluate_tick({"bms.cell_voltage_max": 3.50}, now_ms=2000.0)
        assert alarm.state == STATE_RTN
        result: dict = ev.acknowledge("test_high")
        assert result["status"] == "error"

    def test_acknowledge_unknown_alarm_rejected(self) -> None:
        """acknowledge() with unknown alarm_id returns error status."""
        alarm: AlarmInstance = make_high_alarm()
        ev: AlarmEvaluator = make_evaluator(alarm)
        result: dict = ev.acknowledge("nonexistent")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# get_active_alarms / get_alarm_config
# ---------------------------------------------------------------------------


class TestQueryMethods:
    def test_get_active_alarms_empty_when_all_normal(self) -> None:
        """get_active_alarms() returns empty list when all alarms are NORMAL."""
        alarm: AlarmInstance = make_high_alarm()
        ev: AlarmEvaluator = make_evaluator(alarm)
        assert ev.get_active_alarms() == []

    def test_get_active_alarms_returns_active(self) -> None:
        """get_active_alarms() returns list of non-NORMAL alarm dicts."""
        alarm: AlarmInstance = make_high_alarm(delay_ms=0)
        ev: AlarmEvaluator = make_evaluator(alarm)
        ev.evaluate_tick({"bms.cell_voltage_max": 3.70}, now_ms=1000.0)
        active: list[dict] = ev.get_active_alarms()
        assert len(active) == 1
        assert active[0]["alarm_id"] == "test_high"
        assert active[0]["state"] == STATE_ACTIVE_UNACKED

    def test_get_alarm_config_returns_all(self) -> None:
        """get_alarm_config() returns list of all alarm rule dicts (regardless of state)."""
        alarm_a: AlarmInstance = make_high_alarm(alarm_id="a")
        alarm_b: AlarmInstance = make_low_alarm(alarm_id="b")
        ev: AlarmEvaluator = make_evaluator(alarm_a, alarm_b)
        config: list[dict] = ev.get_alarm_config()
        assert len(config) == 2
        alarm_ids: list[str] = [c["alarm_id"] for c in config]
        assert "a" in alarm_ids
        assert "b" in alarm_ids
