"""CommAnalyzer — device communication fault frequency scoring.

Processes logger event_log query results to score each connected device's
communication reliability. Fault data comes from the logger's event_log
(DuckDB/Parquet), NOT from a live ZMQ subscription.

Usage pattern (1-hour query window):
    analyzer = CommAnalyzer(known_devices=["bms_rack_0", "pcs", "btms", "meter"])
    rows = query_logger_event_log(window_hours=1.0)
    analyzer.update_from_event_log(rows, window_hours=1.0)
    results = analyzer.get_current()
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

_STATUS_HEALTHY: str = "healthy"
_STATUS_DEGRADED: str = "degraded"
_STATUS_UNHEALTHY: str = "unhealthy"


# ---------------------------------------------------------------------------
# CommAnalyzer
# ---------------------------------------------------------------------------


class CommAnalyzer:
    """Scores device communication reliability from logger event_log data.

    One CommAnalyzer instance spans all devices. Updated by passing logger
    query results (list of event row dicts) covering a sliding time window.

    Each update_from_event_log() call resets fault counts so that consecutive
    calls reflect only the most recent query window — there is no accumulation
    across calls.

    Devices not in known_devices but present in events are included in results
    (they may be newly discovered or misconfigured devices).

    Attributes:
        _known_devices: Set of expected device IDs.
        _warning_rate: Fault rate (per hour) at or above which status = unhealthy.
        _fault_counts: Per-device fault count from the most recent update.
        _window_hours: Time window (hours) from the most recent update.
    """

    def __init__(
        self,
        known_devices: list[str],
        warning_rate: int = 5,
    ) -> None:
        """Initialise the CommAnalyzer.

        Args:
            known_devices: List of expected device IDs (e.g. "bms_rack_0", "pcs").
            warning_rate: Fault rate per hour at/above which status becomes
                          "unhealthy". Devices below this (but > 0) are "degraded".
        """
        self._known_devices: set[str] = set(known_devices)
        self._warning_rate: int = warning_rate
        self._fault_counts: dict[str, int] | None = None
        self._window_hours: float = 1.0

    def update_from_event_log(
        self,
        event_rows: list[dict],
        window_hours: float = 1.0,
    ) -> None:
        """Process logger event_log query results and update fault counts.

        Resets fault counts to zero for all known devices before processing,
        so consecutive calls reflect only the given window, not accumulated
        history.

        Unexpected device IDs found in comm_fault events are added to the
        fault_counts dict alongside known devices.

        Args:
            event_rows: List of event row dicts from the logger event_log query.
                Each dict has keys: ts, source, event_type, data.
                data["device_id"] is used to identify the faulting device.
            window_hours: Duration of the query window (hours) used to compute
                          fault_rate_per_hour. Default 1.0.
        """
        self._window_hours = window_hours

        # Reset known-device counts to 0 (guarantees healthy baseline)
        fault_counts: dict[str, int] = {dev: 0 for dev in self._known_devices}

        for row in event_rows:
            if row.get("event_type") != "comm_fault":
                continue
            data: dict = row.get("data", {})
            device_id: str = data.get("device_id", "")
            if not device_id:
                continue
            # Known or unknown device — include in results
            fault_counts[device_id] = fault_counts.get(device_id, 0) + 1

        self._fault_counts = fault_counts

    def get_current(self) -> list[dict]:
        """Return per-device communication health snapshot.

        Returns:
            List of dicts, one per device (known + unexpected), sorted by
            device_id. Each dict has:
            - "device_id": str
            - "fault_count": int — raw fault count in the query window
            - "fault_rate_per_hour": float — fault_count / window_hours
            - "status": str — "healthy", "degraded", or "unhealthy"

            Returns an empty list if update_from_event_log has not yet been called.
        """
        if self._fault_counts is None:
            return []

        results: list[dict] = []
        for device_id, count in self._fault_counts.items():
            fault_rate: float = count / self._window_hours
            status: str = self._classify_status(fault_rate)
            results.append(
                {
                    "device_id": device_id,
                    "fault_count": count,
                    "fault_rate_per_hour": fault_rate,
                    "status": status,
                }
            )

        results.sort(key=lambda d: d["device_id"])
        return results

    def _classify_status(self, fault_rate_per_hour: float) -> str:
        """Determine device status based on fault rate vs warning threshold.

        Args:
            fault_rate_per_hour: Computed fault rate (faults / window_hours).

        Returns:
            "healthy" if rate == 0, "unhealthy" if rate >= warning_rate,
            "degraded" otherwise.
        """
        if fault_rate_per_hour == 0.0:
            return _STATUS_HEALTHY
        if fault_rate_per_hour >= self._warning_rate:
            return _STATUS_UNHEALTHY
        return _STATUS_DEGRADED
