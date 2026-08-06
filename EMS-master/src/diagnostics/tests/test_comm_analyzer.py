"""Unit tests for CommAnalyzer — device fault frequency scoring.

Tests verify:
- Empty event log means all known devices healthy (fault_rate=0)
- Fault counting per device_id
- Fault rate calculation = faults / window_hours
- Status classification: healthy / degraded / unhealthy
- Unknown devices (in events but not known_devices) are included
- get_current() returns list of dicts with correct structure
- Consecutive updates reset counts (no accumulation across updates)
"""

from __future__ import annotations

from ems_diagnostics.analyzers.comm import CommAnalyzer


KNOWN_DEVICES: list[str] = ["bms_rack_0", "pcs", "btms", "meter"]


def _make_fault_row(device_id: str, fault_type: str = "timeout") -> dict:
    """Helper to build a comm_fault event row matching logger format."""
    return {
        "ts": 1700000000000,
        "source": "comm_manager",
        "event_type": "comm_fault",
        "data": {"device_id": device_id, "fault_type": fault_type},
    }


def _make_non_fault_row(event_type: str = "state_change") -> dict:
    """Helper to build a non-comm_fault event row (should be ignored)."""
    return {
        "ts": 1700000000001,
        "source": "comm_manager",
        "event_type": event_type,
        "data": {},
    }


class TestCommAnalyzerInitialState:
    """Tests for initial state and structure."""

    def test_no_faults_all_healthy(self) -> None:
        """Empty event rows should give all known devices fault_rate=0 and status='healthy'."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=KNOWN_DEVICES)
        analyzer.update_from_event_log(event_rows=[], window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        assert len(results) == len(KNOWN_DEVICES)
        for r in results:
            assert r["fault_count"] == 0
            assert r["fault_rate_per_hour"] == 0.0
            assert r["status"] == "healthy"

    def test_get_current_structure(self) -> None:
        """Each result dict must contain device_id, fault_count, fault_rate_per_hour, status."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=KNOWN_DEVICES)
        analyzer.update_from_event_log(event_rows=[], window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        for r in results:
            assert "device_id" in r
            assert "fault_count" in r
            assert "fault_rate_per_hour" in r
            assert "status" in r
            assert len(r) == 4

    def test_before_first_update_returns_empty(self) -> None:
        """Before update_from_event_log is called, get_current() returns empty list."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=KNOWN_DEVICES)
        results: list[dict] = analyzer.get_current()
        assert results == []


class TestCommAnalyzerFaultCounting:
    """Tests for fault counting per device."""

    def test_fault_counting(self) -> None:
        """3 pcs faults and 1 btms fault should produce correct per-device counts."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=KNOWN_DEVICES)
        rows: list[dict] = [
            _make_fault_row("pcs"),
            _make_fault_row("pcs"),
            _make_fault_row("pcs"),
            _make_fault_row("btms"),
        ]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()

        pcs_result: dict = next(r for r in results if r["device_id"] == "pcs")
        btms_result: dict = next(r for r in results if r["device_id"] == "btms")
        bms_result: dict = next(r for r in results if r["device_id"] == "bms_rack_0")

        assert pcs_result["fault_count"] == 3
        assert btms_result["fault_count"] == 1
        assert bms_result["fault_count"] == 0

    def test_non_fault_events_ignored(self) -> None:
        """Events with event_type != 'comm_fault' must not increment fault counts."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=KNOWN_DEVICES)
        rows: list[dict] = [
            _make_non_fault_row("state_change"),
            _make_non_fault_row("alarm"),
            _make_fault_row("pcs"),
        ]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        pcs_result: dict = next(r for r in results if r["device_id"] == "pcs")
        assert pcs_result["fault_count"] == 1


class TestCommAnalyzerFaultRate:
    """Tests for fault rate calculation."""

    def test_fault_rate_calculation_one_hour(self) -> None:
        """6 faults in 1-hour window -> fault_rate_per_hour = 6.0."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=KNOWN_DEVICES)
        rows: list[dict] = [_make_fault_row("pcs") for _ in range(6)]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        pcs_result: dict = next(r for r in results if r["device_id"] == "pcs")
        assert pcs_result["fault_rate_per_hour"] == 6.0

    def test_fault_rate_calculation_two_hour_window(self) -> None:
        """6 faults in 2-hour window -> fault_rate_per_hour = 3.0."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=KNOWN_DEVICES)
        rows: list[dict] = [_make_fault_row("pcs") for _ in range(6)]
        analyzer.update_from_event_log(event_rows=rows, window_hours=2.0)
        results: list[dict] = analyzer.get_current()
        pcs_result: dict = next(r for r in results if r["device_id"] == "pcs")
        assert pcs_result["fault_rate_per_hour"] == 3.0


class TestCommAnalyzerStatusThresholds:
    """Tests for status classification thresholds."""

    def test_status_healthy_zero_faults(self) -> None:
        """0 faults -> status='healthy'."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=["pcs"], warning_rate=5)
        analyzer.update_from_event_log(event_rows=[], window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        assert results[0]["status"] == "healthy"

    def test_status_degraded_below_warning(self) -> None:
        """3 faults/hour with warning_rate=5 -> status='degraded'."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=["pcs"], warning_rate=5)
        rows: list[dict] = [_make_fault_row("pcs") for _ in range(3)]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        assert results[0]["status"] == "degraded"

    def test_status_unhealthy_at_warning_rate(self) -> None:
        """5 faults/hour with warning_rate=5 -> status='unhealthy' (>= threshold)."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=["pcs"], warning_rate=5)
        rows: list[dict] = [_make_fault_row("pcs") for _ in range(5)]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        assert results[0]["status"] == "unhealthy"

    def test_status_unhealthy_above_warning_rate(self) -> None:
        """10 faults/hour with warning_rate=5 -> status='unhealthy' (> threshold)."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=["pcs"], warning_rate=5)
        rows: list[dict] = [_make_fault_row("pcs") for _ in range(10)]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        assert results[0]["status"] == "unhealthy"


class TestCommAnalyzerEdgeCases:
    """Edge case and integration tests."""

    def test_unknown_device_included(self) -> None:
        """Device not in known_devices but in event rows must appear in results."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=["pcs"], warning_rate=5)
        rows: list[dict] = [_make_fault_row("mystery_device")]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        device_ids: list[str] = [r["device_id"] for r in results]
        assert "mystery_device" in device_ids
        mystery: dict = next(r for r in results if r["device_id"] == "mystery_device")
        assert mystery["fault_count"] == 1

    def test_update_resets_counts(self) -> None:
        """Second update_from_event_log call replaces first — no accumulation."""
        analyzer: CommAnalyzer = CommAnalyzer(known_devices=["pcs"], warning_rate=5)
        first_rows: list[dict] = [_make_fault_row("pcs") for _ in range(10)]
        analyzer.update_from_event_log(event_rows=first_rows, window_hours=1.0)

        second_rows: list[dict] = [_make_fault_row("pcs") for _ in range(2)]
        analyzer.update_from_event_log(event_rows=second_rows, window_hours=1.0)

        results: list[dict] = analyzer.get_current()
        pcs_result: dict = next(r for r in results if r["device_id"] == "pcs")
        assert pcs_result["fault_count"] == 2

    def test_multiple_devices_independent_counts(self) -> None:
        """Faults for device A should not affect count for device B."""
        analyzer: CommAnalyzer = CommAnalyzer(
            known_devices=["pcs", "btms", "meter"], warning_rate=5
        )
        rows: list[dict] = [_make_fault_row("pcs") for _ in range(4)]
        analyzer.update_from_event_log(event_rows=rows, window_hours=1.0)
        results: list[dict] = analyzer.get_current()
        btms_result: dict = next(r for r in results if r["device_id"] == "btms")
        meter_result: dict = next(r for r in results if r["device_id"] == "meter")
        assert btms_result["fault_count"] == 0
        assert meter_result["fault_count"] == 0
