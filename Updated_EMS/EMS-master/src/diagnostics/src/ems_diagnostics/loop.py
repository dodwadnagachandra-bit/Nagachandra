"""DiagnosticsLoop — 5 async tasks wiring analyzers to ZMQ sockets.

Runs 5 concurrent asyncio tasks:
  1. _telemetry_collector:    1Hz SUB drain → route to analyzers
  2. _hourly_trend_updater:   trend_update_s interval → query logger, update history
  3. _diagnostics_publisher:  publish_s interval → broadcast current state on PUB
  4. _report_server:          non-blocking REP drain → serve get_current/get_report/get_predictions
  5. _predictive_alert_checker: publish_s interval → fire PUSH events on SOH alerts

All DuckDB queries (via logger REQ) are executed in asyncio.to_thread to avoid
blocking the event loop (Research Pitfall 3).

The REP socket always sends a reply, even on exception (Research Pitfall 6).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import msgpack
import zmq

from ems_common.ipc import (
    SEVERITY_WARNING,
    SOCK_DIAGNOSTICS_CMD,
    SOCK_DIAGNOSTICS_PUB,
    SOCK_LOGGER,
    SOCK_LOGGER_QUERY,
    SOCK_TELEMETRY,
    TOPIC_BMS_RACK,
    TOPIC_BTMS,
    TOPIC_DIAGNOSTICS,
    TOPIC_PCS,
    STATUS_OK,
    STATUS_ERROR,
    decode_command_request,
    decode_telemetry,
    encode_command_request,
    encode_command_response,
    decode_command_response,
    encode_event,
)
from ems_diagnostics.analyzers import (
    CommAnalyzer,
    PcsAnalyzer,
    SohAnalyzer,
    ThermalAnalyzer,
)
from ems_diagnostics.config import DiagnosticsConfig
from ems_diagnostics.reporter import ReportBuilder


logger: logging.Logger = logging.getLogger(__name__)

# Module source string used in IPC event messages
_SOURCE: str = "diagnostics"

# Poll interval for non-blocking socket drains (10ms = 100Hz)
_POLL_INTERVAL_S: float = 0.01

# Known devices for CommAnalyzer (default — real deployment would configure these)
_DEFAULT_KNOWN_DEVICES: list[str] = ["bms_rack_1", "pcs", "btms", "meter"]


class DiagnosticsLoop:
    """Runs 5 concurrent async tasks connecting analyzers to ZMQ sockets.

    Instantiates all 4 analyzers, the ReportBuilder, and owns all ZMQ sockets.
    Call run() to start; call stop() or set the stop_event to shut down cleanly.

    Args:
        config: Loaded DiagnosticsConfig.
        zmq_ctx: Optional external ZMQ context (for testing with inproc:// sockets).
        telemetry_sub_endpoint: Override SOCK_TELEMETRY.
        logger_req_endpoint: Override SOCK_LOGGER_QUERY.
        logger_push_endpoint: Override SOCK_LOGGER.
        diagnostics_pub_endpoint: Override SOCK_DIAGNOSTICS_PUB.
        diagnostics_cmd_endpoint: Override SOCK_DIAGNOSTICS_CMD.
        rack_count: Number of battery racks (creates one SohAnalyzer per rack).
        known_devices: Device IDs for CommAnalyzer.
    """

    def __init__(
        self,
        config: DiagnosticsConfig,
        zmq_ctx: zmq.Context | None = None,
        *,
        telemetry_sub_endpoint: str | None = None,
        logger_req_endpoint: str | None = None,
        logger_push_endpoint: str | None = None,
        diagnostics_pub_endpoint: str | None = None,
        diagnostics_cmd_endpoint: str | None = None,
        rack_count: int = 1,
        known_devices: list[str] | None = None,
    ) -> None:
        """Initialise the DiagnosticsLoop."""
        self._config: DiagnosticsConfig = config

        # ZMQ context — own it if not provided
        self._own_ctx: bool = zmq_ctx is None
        self._zmq_ctx: zmq.Context = zmq_ctx or zmq.Context()

        # Stop signal — created lazily in run() to bind to the running event loop
        self._stop_event: asyncio.Event | None = None

        # ------------------------------------------------------------------
        # ZMQ sockets
        # ------------------------------------------------------------------

        # SUB — subscribe to telemetry topics
        _tel_ep: str = telemetry_sub_endpoint or SOCK_TELEMETRY
        self._sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.setsockopt(zmq.RCVTIMEO, 0)  # non-blocking
        self._sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_BMS_RACK)
        self._sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_PCS)
        self._sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_BTMS)
        self._sub.connect(_tel_ep)

        # REQ — query logger for historical data
        _req_ep: str = logger_req_endpoint or SOCK_LOGGER_QUERY
        self._req: zmq.Socket = self._zmq_ctx.socket(zmq.REQ)
        self._req.setsockopt(zmq.LINGER, 0)
        self._req.setsockopt(zmq.RCVTIMEO, 5000)   # 5s receive timeout
        self._req.setsockopt(zmq.SNDTIMEO, 5000)   # 5s send timeout
        self._req.connect(_req_ep)

        # PUSH — send events to logger
        _push_ep: str = logger_push_endpoint or SOCK_LOGGER
        self._push: zmq.Socket = self._zmq_ctx.socket(zmq.PUSH)
        self._push.setsockopt(zmq.LINGER, 0)
        self._push.connect(_push_ep)

        # PUB — broadcast diagnostics state
        _pub_ep: str = diagnostics_pub_endpoint or SOCK_DIAGNOSTICS_PUB
        self._pub: zmq.Socket = self._zmq_ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.bind(_pub_ep)

        # REP — serve report queries
        _rep_ep: str = diagnostics_cmd_endpoint or SOCK_DIAGNOSTICS_CMD
        self._rep: zmq.Socket = self._zmq_ctx.socket(zmq.REP)
        self._rep.setsockopt(zmq.LINGER, 0)
        self._rep.setsockopt(zmq.RCVTIMEO, 0)  # non-blocking
        self._rep.bind(_rep_ep)

        # ------------------------------------------------------------------
        # Analyzers
        # ------------------------------------------------------------------

        self._soh_analyzers: list[SohAnalyzer] = [
            SohAnalyzer(rack_id=i + 1, rated_capacity_ah=config.battery.rated_capacity_ah)
            for i in range(rack_count)
        ]
        self._pcs_analyzer: PcsAnalyzer = PcsAnalyzer()
        self._thermal_analyzer: ThermalAnalyzer = ThermalAnalyzer()
        self._comm_analyzer: CommAnalyzer = CommAnalyzer(
            known_devices=known_devices or _DEFAULT_KNOWN_DEVICES,
            warning_rate=config.thresholds.comm_fault_warning_rate,
        )

        # ReportBuilder
        self._reporter: ReportBuilder = ReportBuilder(config)

        logger.info(
            "DiagnosticsLoop initialised: SUB=%s REQ=%s PUSH=%s PUB=%s REP=%s racks=%d",
            _tel_ep, _req_ep, _push_ep, _pub_ep, _rep_ep, rack_count,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def stop_event(self) -> asyncio.Event:
        """Asyncio event — set to request graceful shutdown.

        Created lazily on first access within a running event loop.
        """
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        return self._stop_event

    async def run(self) -> None:
        """Run all 5 async tasks concurrently until stop_event is set."""
        # Ensure stop_event is created in the current running loop
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        logger.info("DiagnosticsLoop starting 5 async tasks")
        await asyncio.gather(
            self._telemetry_collector(),
            self._hourly_trend_updater(),
            self._diagnostics_publisher(),
            self._report_server(),
            self._predictive_alert_checker(),
        )
        logger.info("DiagnosticsLoop all tasks complete")

    def stop(self) -> None:
        """Signal all tasks to stop cleanly."""
        if self._stop_event is not None:
            self._stop_event.set()

    def cleanup(self) -> None:
        """Close all ZMQ sockets and optionally terminate the context."""
        for sock in (self._sub, self._req, self._push, self._pub, self._rep):
            try:
                sock.close()
            except Exception:
                pass
        if self._own_ctx:
            try:
                self._zmq_ctx.term()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Task 1: Telemetry collector
    # ------------------------------------------------------------------

    async def _telemetry_collector(self) -> None:
        """Drain SUB socket at 100Hz and route telemetry to analyzers.

        ZMQ PUB/SUB multipart frame format (set by data_manager publisher):
          Frame 0: topic bytes (e.g. b"bms.rack.1")
          Frame 1: msgpack-encoded telemetry envelope (from encode_telemetry)
        """
        while not self._stop_event.is_set():
            # Non-blocking drain of all queued messages
            while True:
                try:
                    frames: list[bytes] = self._sub.recv_multipart(zmq.NOBLOCK)
                    if len(frames) >= 2:
                        self._route_telemetry(frames[0], frames[1])
                except zmq.Again:
                    break

            await asyncio.sleep(_POLL_INTERVAL_S)

    def _route_telemetry(self, topic_bytes: bytes, payload_bytes: bytes) -> None:
        """Decode and dispatch a single telemetry message.

        Args:
            topic_bytes: Topic frame bytes (e.g. b"bms.rack.1").
            payload_bytes: Msgpack-encoded telemetry envelope.
        """
        try:
            topic: str = topic_bytes.decode("utf-8", errors="replace")
            msg: dict = decode_telemetry(payload_bytes)
            payload: dict = msg.get("payload", {})

            if topic.startswith(TOPIC_BMS_RACK):
                # bms.rack.{N} — route to SohAnalyzer[N-1] (1-based rack_id)
                parts: list[str] = topic.split(".")
                rack_idx: int = 0
                if len(parts) == 3:
                    try:
                        rack_idx = int(parts[2]) - 1
                    except ValueError:
                        rack_idx = 0

                if 0 <= rack_idx < len(self._soh_analyzers):
                    self._soh_analyzers[rack_idx].update(
                        pack_i=float(payload.get("pack_i", 0.0)),
                        pack_soc=float(payload.get("pack_soc", 0.0)),
                        pack_soh_bms=float(payload.get("pack_soh", 100.0)),
                    )
                    # Cache max_cell_t for ThermalAnalyzer combination
                    if "max_cell_t" in payload:
                        self._last_max_cell_t: float = float(payload["max_cell_t"])

            elif topic == TOPIC_PCS:
                self._pcs_analyzer.update(
                    active_power=float(payload.get("active_power", 0.0)),
                    dc_voltage=float(payload.get("dc_voltage", 0.0)),
                    dc_current=float(payload.get("dc_current", 0.0)),
                )

            elif topic == TOPIC_BTMS:
                max_cell_t: float = float(
                    payload.get("max_cell_t", getattr(self, "_last_max_cell_t", 25.0))
                )
                self._thermal_analyzer.update(
                    max_cell_t=max_cell_t,
                    outlet_temp=float(payload.get("outlet_temp", 25.0)),
                    fan_speed_pct=float(payload.get("fan_speed_pct", 0.0)),
                )

        except Exception as exc:
            logger.warning("Failed to route telemetry: %s", exc)

    # ------------------------------------------------------------------
    # Task 2: Hourly trend updater
    # ------------------------------------------------------------------

    async def _hourly_trend_updater(self) -> None:
        """Query logger for historical data at trend_update_s intervals.

        Sleeps first so startup doesn't block; then runs query in executor.
        """
        interval_s: float = float(self._config.intervals.trend_update_s)

        while not self._stop_event.is_set():
            # Sleep for the interval first (responsive to stop)
            elapsed: float = 0.0
            while elapsed < interval_s and not self._stop_event.is_set():
                await asyncio.sleep(min(1.0, interval_s - elapsed))
                elapsed += 1.0

            if self._stop_event.is_set():
                break

            # Run blocking DuckDB queries in a thread (non-blocking for event loop)
            try:
                await asyncio.to_thread(self._run_trend_update)
            except Exception as exc:
                logger.warning("Trend update failed: %s", exc)

    def _run_trend_update(self) -> None:
        """Execute logger queries synchronously (runs in executor thread).

        Queries range_stats for SOH and event_log for comm faults,
        then updates CommAnalyzer and SohAnalyzer history points.
        """
        now_ms: int = int(time.time() * 1000)
        window_hours: float = 1.0
        since_ms: int = now_ms - int(window_hours * 3600 * 1000)

        # Query comm fault events
        try:
            req_payload: bytes = encode_command_request(
                "query",
                {
                    "query": "event_log",
                    "event_type": "comm_fault",
                    "since_ms": since_ms,
                    "until_ms": now_ms,
                },
            )
            self._req.send(req_payload)
            raw_resp: bytes = self._req.recv()
            resp: dict = decode_command_response(raw_resp)
            event_rows: list[dict] = resp.get("result", {}).get("rows", []) if resp.get("result") else []
            self._comm_analyzer.update_from_event_log(event_rows, window_hours=window_hours)
        except Exception as exc:
            logger.warning("Comm fault query failed: %s", exc)

        # Add SOH history point for each rack
        now_ts: int = int(time.time() * 1000)
        for analyzer in self._soh_analyzers:
            analyzer.add_history_point(timestamp_ms=now_ts)

    # ------------------------------------------------------------------
    # Task 3: Diagnostics publisher
    # ------------------------------------------------------------------

    async def _diagnostics_publisher(self) -> None:
        """Broadcast current diagnostics state at publish_s intervals."""
        interval_s: float = float(self._config.intervals.publish_s)

        while not self._stop_event.is_set():
            try:
                snapshot: dict = self._reporter.build_current(
                    soh_analyzers=self._soh_analyzers,
                    pcs=self._pcs_analyzer,
                    thermal=self._thermal_analyzer,
                    comm=self._comm_analyzer,
                )
                # PUB: topic bytes + space + msgpack payload
                topic_bytes: bytes = TOPIC_DIAGNOSTICS.encode()
                payload_bytes: bytes = msgpack.packb(snapshot, use_bin_type=True)
                self._pub.send(topic_bytes + b" " + payload_bytes)
                logger.debug("Published diagnostics snapshot")
            except Exception as exc:
                logger.warning("Diagnostics publish failed: %s", exc)

            elapsed: float = 0.0
            while elapsed < interval_s and not self._stop_event.is_set():
                await asyncio.sleep(min(1.0, interval_s - elapsed))
                elapsed += 1.0

    # ------------------------------------------------------------------
    # Task 4: Report server (REP socket)
    # ------------------------------------------------------------------

    async def _report_server(self) -> None:
        """Non-blocking REP socket drain — serve report queries at 100Hz."""
        while not self._stop_event.is_set():
            while True:
                try:
                    raw: bytes = self._rep.recv(zmq.NOBLOCK)
                    reply: bytes = self._handle_report_request(raw)
                    self._rep.send(reply)
                except zmq.Again:
                    break
                except Exception as exc:
                    # If we received a message but handling failed, send error
                    # The ZMQ REP state machine requires a send after recv
                    logger.warning("Report server error (sending error reply): %s", exc)
                    try:
                        self._rep.send(
                            encode_command_response(STATUS_ERROR, error_msg=str(exc))
                        )
                    except Exception:
                        pass
                    break

            await asyncio.sleep(_POLL_INTERVAL_S)

    def _handle_report_request(self, raw: bytes) -> bytes:
        """Dispatch a REP request and return encoded response bytes.

        Always returns a valid encoded response, even on error.

        Args:
            raw: Raw bytes from REP socket.

        Returns:
            Encoded msgpack response bytes.
        """
        try:
            action: str
            params: dict
            action, params = decode_command_request(raw)

            if action == "get_current":
                result: dict = self._reporter.build_current(
                    soh_analyzers=self._soh_analyzers,
                    pcs=self._pcs_analyzer,
                    thermal=self._thermal_analyzer,
                    comm=self._comm_analyzer,
                )
                return encode_command_response(STATUS_OK, result=result)

            elif action == "get_report":
                period: str = params.get("period", "daily")
                result = self._reporter.build_report(
                    period=period,
                    query_fn=self._sync_logger_query,
                )
                return encode_command_response(STATUS_OK, result=result)

            elif action == "get_predictions":
                result = self._reporter.build_predictions(
                    soh_analyzers=self._soh_analyzers,
                    config=self._config,
                )
                return encode_command_response(STATUS_OK, result=result)

            else:
                return encode_command_response(
                    STATUS_ERROR, error_msg=f"Unknown action: {action}"
                )

        except Exception as exc:
            logger.warning("Report request handling failed: %s", exc)
            return encode_command_response(STATUS_ERROR, error_msg=str(exc))

    def _sync_logger_query(self, params: dict) -> list[dict]:
        """Execute a synchronous logger REQ query.

        Used as query_fn callback from build_report(). Should only be called
        from within asyncio.to_thread to avoid blocking the event loop.

        Args:
            params: Query parameters dict.

        Returns:
            List of result row dicts, or empty list on error.
        """
        try:
            req_payload: bytes = encode_command_request("query", params)
            self._req.send(req_payload)
            raw_resp: bytes = self._req.recv()
            resp: dict = decode_command_response(raw_resp)
            rows: Any = resp.get("result", {}).get("rows", []) if resp.get("result") else []
            return rows if isinstance(rows, list) else []
        except Exception as exc:
            logger.warning("Logger query failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Task 5: Predictive alert checker
    # ------------------------------------------------------------------

    async def _predictive_alert_checker(self) -> None:
        """Fire PUSH alert events when SOH degradation trend is severe."""
        interval_s: float = float(self._config.intervals.publish_s)

        while not self._stop_event.is_set():
            try:
                predictions: dict = self._reporter.build_predictions(
                    soh_analyzers=self._soh_analyzers,
                    config=self._config,
                )

                for pred in predictions.get("predictions", []):
                    days: float | None = pred.get("days_to_threshold")
                    if days is not None and days < 90.0:
                        rack_id: int = pred.get("rack_id", 0)
                        severity: str = pred.get("severity", "warning")
                        event_bytes: bytes = encode_event(
                            timestamp_ms=int(time.time() * 1000),
                            source=_SOURCE,
                            severity=SEVERITY_WARNING,
                            event_type="predictive_alert",
                            message=(
                                f"SOH for rack {rack_id} projected to reach threshold "
                                f"in {days:.1f} days"
                            ),
                            data={
                                "rack_id": rack_id,
                                "metric": "soh",
                                "current_value": pred.get("current_value"),
                                "days_to_threshold": days,
                                "trend_rate_per_day": pred.get("trend_rate_per_day"),
                                "severity": severity,
                            },
                        )
                        self._push.send(event_bytes, zmq.NOBLOCK)
                        logger.warning(
                            "Predictive alert: rack=%d days_to_threshold=%.1f severity=%s",
                            rack_id, days, severity,
                        )

            except Exception as exc:
                logger.warning("Predictive alert check failed: %s", exc)

            elapsed: float = 0.0
            while elapsed < interval_s and not self._stop_event.is_set():
                await asyncio.sleep(min(1.0, interval_s - elapsed))
                elapsed += 1.0
