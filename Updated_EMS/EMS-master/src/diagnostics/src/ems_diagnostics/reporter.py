"""ReportBuilder — aggregates analyzer snapshots and generates SOH predictions.

Provides three query types used by the DiagnosticsLoop report server:
- build_current(): live snapshot from all 4 analyzers
- build_report(): historical summary from logger DuckDB queries
- build_predictions(): SOH linear-regression projections to threshold

Uses statistics.linear_regression (Python 3.12+) for trend projection.
DuckDB queries are run via a provided query_fn callback so the caller
(DiagnosticsLoop) can execute them in an asyncio executor.
"""

from __future__ import annotations

import statistics
import time
from typing import Callable

from ems_diagnostics.analyzers import CommAnalyzer, PcsAnalyzer, SohAnalyzer, ThermalAnalyzer
from ems_diagnostics.config import DiagnosticsConfig


# ---------------------------------------------------------------------------
# Severity thresholds (days to SOH threshold)
# ---------------------------------------------------------------------------

_SEVERITY_CRITICAL_DAYS: float = 30.0
_SEVERITY_WARNING_DAYS: float = 90.0


class ReportBuilder:
    """Builds diagnostic reports and predictions from analyzer state.

    Holds a reference to DiagnosticsConfig for threshold values.  Analyzer
    instances are passed per-call so the same ReportBuilder works regardless
    of rack count.
    """

    def __init__(self, config: DiagnosticsConfig) -> None:
        """Initialise ReportBuilder with configuration.

        Args:
            config: Loaded DiagnosticsConfig with threshold/prediction settings.
        """
        self._config: DiagnosticsConfig = config

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def build_current(
        self,
        soh_analyzers: list[SohAnalyzer],
        pcs: PcsAnalyzer,
        thermal: ThermalAnalyzer,
        comm: CommAnalyzer,
    ) -> dict:
        """Aggregate current snapshots from all 4 analyzers.

        Args:
            soh_analyzers: One SohAnalyzer per rack.
            pcs: Single PcsAnalyzer instance.
            thermal: Single ThermalAnalyzer instance.
            comm: Single CommAnalyzer instance.

        Returns:
            Dict with keys: soh (list), pcs_efficiency, thermal, comm.
        """
        return {
            "soh": [a.get_current() for a in soh_analyzers],
            "pcs_efficiency": pcs.get_current(),
            "thermal": thermal.get_current(),
            "comm": comm.get_current(),
        }

    def build_report(self, period: str, query_fn: Callable) -> dict:
        """Build a historical summary by querying the logger.

        Args:
            period: "daily" for last 24 h, "weekly" for last 168 h.
            query_fn: Callable(params: dict) -> list[dict] — executes a logger
                      query and returns row dicts.  Invoked synchronously here;
                      callers that need async should run this in an executor.

        Returns:
            Dict with keys: period, generated_at, soh_trend, efficiency_trend,
            comm_summary, alerts.
        """
        window_hours: float = 24.0 if period == "daily" else 168.0
        now_ms: int = int(time.time() * 1000)
        since_ms: int = now_ms - int(window_hours * 3600 * 1000)

        # Query logger for SOH range statistics
        soh_trend: list[dict] = query_fn({
            "query": "range_stats",
            "metric": "pack_soh_bms",
            "since_ms": since_ms,
            "until_ms": now_ms,
        })

        # Query logger for PCS efficiency statistics
        efficiency_trend: list[dict] = query_fn({
            "query": "range_stats",
            "metric": "pcs_efficiency",
            "since_ms": since_ms,
            "until_ms": now_ms,
        })

        # Query logger for comm fault events
        comm_summary: list[dict] = query_fn({
            "query": "event_log",
            "event_type": "comm_fault",
            "since_ms": since_ms,
            "until_ms": now_ms,
        })

        return {
            "period": period,
            "generated_at": now_ms,
            "soh_trend": soh_trend if soh_trend is not None else [],
            "efficiency_trend": efficiency_trend if efficiency_trend is not None else [],
            "comm_summary": comm_summary if comm_summary is not None else [],
            "alerts": [],
        }

    def build_predictions(
        self,
        soh_analyzers: list[SohAnalyzer],
        config: DiagnosticsConfig,
    ) -> dict:
        """Project SOH degradation trend for each rack.

        Uses statistics.linear_regression over SOH history points. Only
        produces a projection if the analyzer has >= min_history_days data
        points and the slope is negative (degrading).

        Args:
            soh_analyzers: One SohAnalyzer per rack.
            config: DiagnosticsConfig for prediction and threshold settings.

        Returns:
            Dict with key "predictions": list of prediction dicts, one per
            rack that has sufficient history and a negative slope.
            Empty list if no rack has >= min_history_days points.
        """
        min_points: int = config.prediction.min_history_days
        critical_threshold: float = config.thresholds.soh_critical_pct
        predictions: list[dict] = []

        for analyzer in soh_analyzers:
            history: list[tuple[int, float]] = analyzer.get_history()
            if len(history) < min_points:
                # Insufficient history — skip rack (no projection possible)
                continue

            # Convert history to (x_days, y_soh) for linear regression
            t0_ms: int = history[0][0]
            ms_per_day: float = 86400.0 * 1000.0
            x_days: list[float] = [
                (ts - t0_ms) / ms_per_day for ts, _ in history
            ]
            y_soh: list[float] = [soh for _, soh in history]

            try:
                lr: statistics.LinearRegression = statistics.linear_regression(
                    x_days, y_soh
                )
                slope: float = lr.slope
            except statistics.StatisticsError:
                slope = 0.0

            current_snap = analyzer.get_current()
            current_soh: float = current_snap["soh_pct"]

            # Only project if degrading (negative slope)
            days_to_threshold: float | None = None
            if slope < 0.0:
                gap: float = critical_threshold - current_soh
                if gap < 0:
                    # Already below threshold — project immediate
                    days_to_threshold = 0.0
                else:
                    days_to_threshold = gap / slope  # slope < 0, gap >= 0 → result < 0 → abs
                    days_to_threshold = abs(days_to_threshold)

            predictions.append({
                "metric": "soh",
                "rack_id": current_snap["rack_id"],
                "current_value": current_soh,
                "trend_rate_per_day": slope,
                "days_to_threshold": days_to_threshold,
                "severity": self._classify_severity(days_to_threshold),
            })

        return {"predictions": predictions}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_severity(self, days_to_threshold: float | None) -> str:
        """Classify alert severity based on days to SOH threshold.

        Args:
            days_to_threshold: Projected days until SOH hits critical threshold,
                               or None if not degrading / insufficient history.

        Returns:
            "critical" if days < 30, "warning" if days < 90, "info" otherwise.
        """
        if days_to_threshold is None:
            return "info"
        if days_to_threshold < _SEVERITY_CRITICAL_DAYS:
            return "critical"
        if days_to_threshold < _SEVERITY_WARNING_DAYS:
            return "warning"
        return "info"
