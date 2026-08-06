"""Tests for DeviceHealth state machine."""

from __future__ import annotations

import pytest

from ems_comm_manager.health import DeviceHealth


class TestDeviceHealthInitialState:
    """Verify initial state: device starts offline (unknown)."""

    def test_initial_online_is_false(self) -> None:
        dh = DeviceHealth("pcs")
        assert dh.online is False

    def test_initial_consecutive_failures_zero(self) -> None:
        dh = DeviceHealth("pcs")
        assert dh.consecutive_failures == 0

    def test_initial_backoff_is_min(self) -> None:
        dh = DeviceHealth("pcs")
        assert dh.backoff_s == DeviceHealth.BACKOFF_MIN_S


class TestRecordSuccess:
    """record_success transitions and reset behavior."""

    def test_success_on_online_device_returns_none(self) -> None:
        """record_success on online device returns None (no state change)."""
        dh = DeviceHealth("pcs")
        # Bring online first
        dh.record_success(1000)
        # Now already online -- no state change
        result = dh.record_success(2000)
        assert result is None

    def test_success_on_offline_device_returns_recovery_dict(self) -> None:
        """record_success on offline device returns recovery event data."""
        dh = DeviceHealth("pcs")
        # Device starts offline -- first success is a recovery
        result = dh.record_success(5000)
        assert result is not None
        assert result["device_id"] == "pcs"
        assert "offline_duration_ms" in result

    def test_success_sets_online_true(self) -> None:
        dh = DeviceHealth("pcs")
        dh.record_success(1000)
        assert dh.online is True

    def test_success_resets_backoff(self) -> None:
        dh = DeviceHealth("pcs")
        # Force offline with failures, then recover
        dh.online = True
        for i in range(3):
            dh.record_failure(1000 + i)
        assert dh.online is False
        dh.record_success(5000)
        assert dh.backoff_s == DeviceHealth.BACKOFF_MIN_S

    def test_success_resets_consecutive_failures(self) -> None:
        dh = DeviceHealth("pcs")
        dh.online = True
        dh.record_failure(1000)
        dh.record_failure(2000)
        dh.record_success(3000)
        assert dh.consecutive_failures == 0

    def test_recovery_includes_offline_duration(self) -> None:
        """Recovery event data includes offline_duration_ms."""
        dh = DeviceHealth("pcs")
        dh.online = True
        dh.record_success(1000)  # set last_success_ms
        # Go offline
        for i in range(3):
            dh.record_failure(2000 + i)
        assert dh.online is False
        # Recover at time 10000 -- was offline since ~2002
        result = dh.record_success(10000)
        assert result is not None
        assert result["offline_duration_ms"] > 0


class TestRecordFailure:
    """record_failure transitions and backoff behavior."""

    def test_failure_below_threshold_returns_none(self) -> None:
        """record_failure below threshold returns None (no state change)."""
        dh = DeviceHealth("pcs", offline_threshold=3)
        dh.online = True
        result = dh.record_failure(1000)
        assert result is None
        assert dh.consecutive_failures == 1

    def test_failure_increments_consecutive_failures(self) -> None:
        dh = DeviceHealth("pcs", offline_threshold=3)
        dh.online = True
        dh.record_failure(1000)
        dh.record_failure(2000)
        assert dh.consecutive_failures == 2

    def test_failure_at_threshold_returns_fault_dict(self) -> None:
        """record_failure at threshold returns fault event data, sets online=False."""
        dh = DeviceHealth("pcs", offline_threshold=3)
        dh.online = True
        dh.record_failure(1000)
        dh.record_failure(2000)
        result = dh.record_failure(3000)
        assert result is not None
        assert result["device_id"] == "pcs"
        assert result["consecutive_failures"] == 3
        assert dh.online is False

    def test_failure_while_offline_returns_none(self) -> None:
        """record_failure while already offline returns None."""
        dh = DeviceHealth("pcs", offline_threshold=3)
        dh.online = True
        # Cross threshold
        for i in range(3):
            dh.record_failure(1000 + i)
        assert dh.online is False
        # Further failures while offline
        result = dh.record_failure(5000)
        assert result is None

    def test_backoff_doubles_on_offline_failures(self) -> None:
        """Backoff doubles: 1->2->4->8->16->30->30."""
        dh = DeviceHealth("pcs", offline_threshold=3)
        dh.online = True
        # Go offline
        for i in range(3):
            dh.record_failure(1000 + i)
        assert dh.backoff_s == DeviceHealth.BACKOFF_MIN_S
        # Each subsequent failure while offline doubles backoff
        dh.record_failure(2000)
        assert dh.backoff_s == 2.0
        dh.record_failure(3000)
        assert dh.backoff_s == 4.0
        dh.record_failure(4000)
        assert dh.backoff_s == 8.0
        dh.record_failure(5000)
        assert dh.backoff_s == 16.0
        dh.record_failure(6000)
        assert dh.backoff_s == 30.0
        # Capped at 30
        dh.record_failure(7000)
        assert dh.backoff_s == 30.0


class TestShouldPoll:
    """should_poll timing behavior."""

    def test_should_poll_true_when_online_interval_elapsed(self) -> None:
        dh = DeviceHealth("pcs", poll_interval_ms=1000)
        dh.online = True
        dh.record_poll(0)
        assert dh.should_poll(1001) is True

    def test_should_poll_false_when_online_interval_not_elapsed(self) -> None:
        dh = DeviceHealth("pcs", poll_interval_ms=1000)
        dh.online = True
        dh.record_poll(0)
        assert dh.should_poll(500) is False

    def test_should_poll_uses_backoff_when_offline(self) -> None:
        dh = DeviceHealth("pcs", poll_interval_ms=500)
        dh.online = True
        # Go offline
        for i in range(3):
            dh.record_failure(1000 + i)
        dh.record_poll(5000)
        # Backoff is 1s = 1000ms
        assert dh.should_poll(5500) is False
        assert dh.should_poll(6001) is True

    def test_should_poll_true_immediately_when_never_polled(self) -> None:
        """If never polled, should_poll returns True."""
        dh = DeviceHealth("pcs")
        assert dh.should_poll(0) is True


class TestRapidToggling:
    """Edge cases: rapid success/failure toggling."""

    def test_rapid_toggle_online_offline_online(self) -> None:
        dh = DeviceHealth("pcs", offline_threshold=2)
        # Start -> online
        result1 = dh.record_success(1000)
        assert result1 is not None  # recovery from initial offline
        assert dh.online is True
        # Go offline
        dh.record_failure(2000)
        result2 = dh.record_failure(3000)
        assert result2 is not None  # fault event
        assert dh.online is False
        # Back online
        result3 = dh.record_success(4000)
        assert result3 is not None  # recovery event
        assert dh.online is True
        assert dh.consecutive_failures == 0
        assert dh.backoff_s == DeviceHealth.BACKOFF_MIN_S
