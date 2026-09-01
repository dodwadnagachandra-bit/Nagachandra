"""SchedulerLoop -- 1Hz schedule evaluation, ZMQ command dispatch, telemetry PUB.

Evaluates schedule_config at 1Hz and sends setpoint commands to control_manager
via ZMQ REQ on state change only. Subscribes to config hot-reload events via
ZMQ SUB. Publishes schedule state telemetry via ZMQ PUB.

Architecture:
- _evaluate_tick(now): dispatch to evaluator functions, detect state changes, send commands
- _evaluate_day_night_tick(now): day/night switching (runs even in manual mode)
- _send_command(action, params): REQ with timeout, recreate socket on timeout
- _poll_config_reload(): SUB NOBLOCK, filter for schedule_config, re-read config
- _publish_telemetry(now): PUB multipart [TOPIC_SCHEDULE, encoded envelope]
- run(): 1Hz asyncio loop with timing-corrected sleep
- cleanup(): close all ZMQ sockets and context
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import msgpack
import zmq

from ems_common.ipc import (
    SOCK_CONTROL_CMD,
    SOCK_CONFIG_PUB,
    SOCK_SCHEDULER_PUB,
    TOPIC_CONFIG_RELOAD,
    TOPIC_SCHEDULE,
    decode_command_response,
    encode_command_request,
    encode_telemetry,
)
from ems_scheduler.config import load_schedule_config
from ems_scheduler.evaluator import (
    CurveResult,
    WindowResult,
    evaluate_curve,
    evaluate_day_night,
    evaluate_time_of_day,
)

logger: logging.Logger = logging.getLogger(__name__)

# Module source name for IPC telemetry messages
_SOURCE: str = "scheduler"

# Default REQ timeout in milliseconds
_REQ_TIMEOUT_MS: int = 5000


class SchedulerLoop:
    """1Hz schedule evaluation loop with ZMQ command dispatch.

    Evaluates schedule_config each tick, sends commands to control_manager
    on state change only, handles config hot-reload, and publishes telemetry.

    Args:
        config: Validated schedule config dict.
        config_path: Path to schedule_config.yaml for re-reading on hot-reload.
        req_endpoint: ZMQ REQ socket connect address. Default: SOCK_CONTROL_CMD.
        config_sub_endpoint: ZMQ SUB socket connect address for config_reload.
        pub_endpoint: ZMQ PUB socket bind address for telemetry.
        now_func: Callable returning current datetime. Default: datetime.now.
    """

    def __init__(
        self,
        config: dict[str, Any],
        config_path: Path | None = None,
        req_endpoint: str | None = None,
        config_sub_endpoint: str | None = None,
        pub_endpoint: str | None = None,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._config: dict[str, Any] = config
        self._config_path: Path | None = config_path
        self._now_func: Callable[[], datetime] = now_func or datetime.now

        # Schedule mode
        self._mode: str = config.get("mode", "manual")

        # State tracking for change detection
        self._last_window_result: WindowResult | None = None
        self._last_curve_index: int | None = None
        self._last_day_night: str | None = None

        # True when scheduler has set MANUAL source_priority (time_of_day/curve mode)
        self._schedule_owns_priority: bool = False

        # Graceful shutdown
        self._stop_event: asyncio.Event = asyncio.Event()

        # Telemetry sequence counter
        self._tel_seq: int = 0

        # ZMQ context and sockets
        self._zmq_ctx: zmq.Context = zmq.Context()

        # REQ socket -> control_manager command API
        self._req_endpoint: str = req_endpoint or SOCK_CONTROL_CMD
        self._req: zmq.Socket = self._zmq_ctx.socket(zmq.REQ)
        self._req.setsockopt(zmq.LINGER, 0)
        self._req.connect(self._req_endpoint)

        # SUB socket -> config_reload events from config_manager
        _config_ep: str = config_sub_endpoint or SOCK_CONFIG_PUB
        self._config_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
        self._config_sub.setsockopt(zmq.LINGER, 0)
        self._config_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CONFIG_RELOAD)
        self._config_sub.connect(_config_ep)

        # PUB socket -> schedule telemetry
        _pub_ep: str = pub_endpoint or SOCK_SCHEDULER_PUB
        self._pub: zmq.Socket = self._zmq_ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.bind(_pub_ep)

        logger.info(
            "SchedulerLoop initialised: REQ=%s CONFIG_SUB=%s PUB=%s mode=%s",
            self._req_endpoint,
            _config_ep,
            _pub_ep,
            self._mode,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def stop_event(self) -> asyncio.Event:
        """Asyncio event -- set this to request graceful shutdown."""
        return self._stop_event

    async def run(self) -> None:
        """1Hz schedule evaluation loop. Runs until stop_event is set.

        Timing-corrected: measures tick duration and subtracts from sleep
        interval to maintain accurate 1Hz cadence.
        """
        logger.info("SchedulerLoop running at 1Hz")

        while not self._stop_event.is_set():
            tick_start: float = time.monotonic()

            now: datetime = self._now_func()
            self._poll_config_reload()
            self._evaluate_tick(now)
            self._publish_telemetry(now)

            elapsed: float = time.monotonic() - tick_start
            sleep_s: float = max(0.0, 1.0 - elapsed)
            await asyncio.sleep(sleep_s)

        logger.info("SchedulerLoop stopped")

    def cleanup(self) -> None:
        """Close ZMQ sockets and terminate context.

        Safe to call multiple times.
        """
        if not self._req.closed:
            self._req.close()
        if not self._config_sub.closed:
            self._config_sub.close()
        if not self._pub.closed:
            self._pub.close()
        self._zmq_ctx.term()
        logger.info("SchedulerLoop cleaned up")

    # ------------------------------------------------------------------
    # Core tick evaluation
    # ------------------------------------------------------------------

    def _evaluate_tick(self, now: datetime) -> None:
        """Evaluate schedule and send commands on state change.

        Dispatch order:
        1. If mode changed from time_of_day/curve to manual, restore day/night
        2. Evaluate schedule mode (time_of_day or curve) -- may set _schedule_owns_priority
        3. Evaluate day/night (always runs, but only sends if not _schedule_owns_priority)

        Schedule mode is evaluated before day/night so that on the first tick
        in time_of_day/curve mode, _schedule_owns_priority is set before
        day/night tries to send source_priority.

        Args:
            now: Current datetime.
        """
        hour: int = now.hour
        minute: int = now.minute

        # Check if scheduler was owning priority but mode changed to manual
        if self._mode == "manual" and self._schedule_owns_priority:
            # Restore day/night source_priority
            dn: str = evaluate_day_night(hour, minute, self._config.get("day_night", {}))
            self._send_command("source_priority", {"mode": dn})
            self._last_day_night = dn
            self._schedule_owns_priority = False
            logger.info("Mode changed to manual -- restored %s source_priority", dn)

        # Schedule mode evaluation (runs first to set _schedule_owns_priority)
        if self._mode == "time_of_day":
            self._evaluate_time_of_day_tick(hour, minute)
        elif self._mode == "curve":
            self._evaluate_curve_tick(hour, minute)

        # Day/night evaluation (always runs, but only sends when scheduler
        # does NOT own priority -- i.e., in manual mode)
        self._evaluate_day_night_tick(hour, minute)
        # mode == "manual" -> no schedule commands

    def _evaluate_day_night_tick(self, hour: int, minute: int) -> None:
        """Evaluate day/night and send source_priority on transition.

        Only sends when scheduler does NOT own priority (i.e., manual mode).
        On first tick, always sends to establish initial state.

        Args:
            hour: Current hour (0-23).
            minute: Current minute (0-59).
        """
        day_night_cfg: dict[str, str] = self._config.get("day_night", {})
        if not day_night_cfg:
            return

        dn: str = evaluate_day_night(hour, minute, day_night_cfg)

        if dn != self._last_day_night:
            if not self._schedule_owns_priority:
                self._send_command("source_priority", {"mode": dn})
            self._last_day_night = dn

    def _evaluate_time_of_day_tick(self, hour: int, minute: int) -> None:
        """Evaluate time-of-day windows and send setpoint on change.

        On first evaluation or state change:
        1. Send source_priority MANUAL (if not already set)
        2. Send manual_setpoint with signed power

        Args:
            hour: Current hour (0-23).
            minute: Current minute (0-59).
        """
        windows: list[dict[str, Any]] = self._config.get("time_windows", [])
        wr: WindowResult = evaluate_time_of_day(hour, minute, windows)

        if wr == self._last_window_result:
            return  # No change

        self._last_window_result = wr

        # Set MANUAL source_priority if not already owning
        if not self._schedule_owns_priority:
            self._send_command("source_priority", {"mode": "manual"})
            self._schedule_owns_priority = True

        # Send setpoint
        self._send_command("manual_setpoint", {"power_kw": wr.power_kw})

    def _evaluate_curve_tick(self, hour: int, minute: int) -> None:
        """Evaluate 96-point curve and send setpoint on index change.

        Args:
            hour: Current hour (0-23).
            minute: Current minute (0-59).
        """
        power_curve: list[float] = self._config.get("power_curve", [0.0] * 96)
        cr: CurveResult = evaluate_curve(hour, minute, power_curve)

        if cr.index == self._last_curve_index:
            return  # No change

        self._last_curve_index = cr.index

        # Set MANUAL source_priority if not already owning
        if not self._schedule_owns_priority:
            self._send_command("source_priority", {"mode": "manual"})
            self._schedule_owns_priority = True

        # Send setpoint
        self._send_command("manual_setpoint", {"power_kw": cr.power_kw})

    # ------------------------------------------------------------------
    # ZMQ REQ command dispatch
    # ------------------------------------------------------------------

    def _send_command(
        self,
        action: str,
        params: dict[str, Any],
        timeout_ms: int = _REQ_TIMEOUT_MS,
    ) -> bool:
        """Send command to control_manager via ZMQ REQ. Returns True on success.

        If poll times out, the REQ socket is closed and recreated (ZMQ REQ/REP
        lockstep recovery).

        Args:
            action: Command action string.
            params: Command parameters dict.
            timeout_ms: Poll timeout in milliseconds.

        Returns:
            True if command accepted, False on timeout or rejection.
        """
        raw: bytes = encode_command_request(action, params)
        self._req.send(raw)

        if self._req.poll(timeout_ms):
            reply: dict[str, Any] = decode_command_response(self._req.recv())
            if reply.get("status") == "ok":
                logger.debug("Command %s accepted", action)
                return True
            logger.warning(
                "Command %s rejected: %s",
                action,
                reply.get("error_msg", "unknown"),
            )
            return False

        # Timeout -- must recreate socket (ZMQ REQ/REP lockstep violation)
        logger.warning("Command %s timed out (%dms)", action, timeout_ms)
        self._req.close()
        self._req = self._zmq_ctx.socket(zmq.REQ)
        self._req.setsockopt(zmq.LINGER, 0)
        self._req.connect(self._req_endpoint)
        return False

    # ------------------------------------------------------------------
    # Config hot-reload
    # ------------------------------------------------------------------

    def _poll_config_reload(self) -> None:
        """Drain config_reload SUB socket NOBLOCK.

        Filters for name="schedule_config". On match, re-reads config from
        disk and resets all tracking state.
        """
        while True:
            try:
                frames: list[bytes] = self._config_sub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break

            if len(frames) < 2:
                logger.warning("Config reload SUB received malformed message (< 2 frames)")
                continue

            try:
                data: dict[str, Any] = msgpack.unpackb(frames[1], raw=False)
                config_name: str = str(data.get("name", ""))
            except Exception as exc:
                logger.warning("Config reload SUB decode error: %s", exc)
                continue

            if config_name != "schedule_config":
                logger.debug("Config reload ignored for '%s' (not our config)", config_name)
                continue

            if self._config_path is None:
                logger.warning(
                    "Config reload event received but config_path not set -- skipping"
                )
                continue

            try:
                new_config: dict[str, Any] = load_schedule_config(self._config_path)
            except Exception as exc:
                logger.error(
                    "Config reload failed for schedule_config: %s -- keeping old config",
                    exc,
                )
                continue

            self._on_config_reloaded(new_config)
            logger.info(
                "Config reload applied for schedule_config from %s", self._config_path
            )

    def _on_config_reloaded(self, new_config: dict[str, Any]) -> None:
        """Apply new config and reset all tracking state.

        Args:
            new_config: Validated schedule config dict.
        """
        self._config = new_config
        self._mode = new_config.get("mode", "manual")

        # Reset tracking state to force re-evaluation on next tick
        self._last_window_result = None
        self._last_curve_index = None
        self._last_day_night = None
        self._schedule_owns_priority = False

    # ------------------------------------------------------------------
    # Telemetry publishing
    # ------------------------------------------------------------------

    def _publish_telemetry(self, now: datetime) -> None:
        """Publish schedule state on PUB socket.

        Multipart message: [TOPIC_SCHEDULE, encoded envelope].

        Args:
            now: Current datetime (for timestamp).
        """
        self._tel_seq += 1
        ts_ms: int = int(time.time() * 1000)

        # Build active_window payload
        active_window: dict[str, Any] | None = None
        if self._last_window_result is not None and self._last_window_result.index is not None:
            wr: WindowResult = self._last_window_result
            active_window = {
                "index": wr.index,
                "action": wr.action,
                "power_kw": wr.power_kw,
            }

        payload: dict[str, Any] = {
            "mode": self._mode,
            "active_window": active_window,
            "curve_index": self._last_curve_index,
            "day_night": self._last_day_night,
        }

        body: bytes = encode_telemetry(
            timestamp_ms=ts_ms,
            seq=self._tel_seq,
            source=_SOURCE,
            topic=TOPIC_SCHEDULE,
            payload=payload,
        )

        try:
            self._pub.send_string(TOPIC_SCHEDULE, zmq.SNDMORE | zmq.NOBLOCK)
            self._pub.send(body, zmq.NOBLOCK)
        except zmq.Again:
            logger.warning("PUB socket would block -- telemetry dropped")
