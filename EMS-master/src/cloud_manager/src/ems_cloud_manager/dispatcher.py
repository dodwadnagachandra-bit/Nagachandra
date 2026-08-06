"""MQTT command dispatcher for EMS cloud_manager.

Receives MQTTMessage payloads from the cloud, validates the command structure,
applies a sliding-window rate limiter, then forwards valid commands to the
control_manager or alarm_manager via ZMQ REQ sockets.

Command routing table (from CONTEXT.md locked decisions):
    mode_change  -> control_cmd / mode_change
    setpoint     -> control_cmd / manual_setpoint
    priority     -> control_cmd / source_priority
    fault_reset  -> control_cmd / fault_reset
    maintenance  -> control_cmd / maintenance_enter|maintenance_exit (dynamic)
    alarm_ack    -> alarm_cmd   / acknowledge
    ota_update   -> ota_cmd    / start_update

All validation failures and downstream errors result in an error response
published back to the MQTT {prefix}/responses/{request_id} topic.
No new business logic is introduced here — this is a thin validation proxy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import zmq

from ems_common.ipc import (
    SOCK_ALARM_CMD,
    SOCK_CONTROL_CMD,
    SOCK_OTA_CMD,
    STATUS_ERROR,
    STATUS_OK,
    decode_command_response,
    encode_command_request,
)

if TYPE_CHECKING:
    from ems_cloud_manager.publisher import MqttPublisher

logger: logging.Logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple sliding-window rate limiter.

    Tracks request timestamps within a rolling window and returns False once
    the maximum count is reached.  Thread-safe for single-threaded asyncio use.
    """

    def __init__(self, max_commands: int = 10, window_s: float = 60.0) -> None:
        """Initialise the rate limiter.

        Args:
            max_commands: Maximum number of commands allowed per window.
            window_s: Length of the sliding window in seconds.
        """
        self._max_commands: int = max_commands
        self._window_s: float = window_s
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        """Return True if the request is within the limit, False if exceeded.

        Removes expired timestamps before checking the count so the window
        slides correctly across time.
        """
        now: float = time.monotonic()
        cutoff: float = now - self._window_s
        # Discard timestamps outside the window
        self._timestamps = [t for t in self._timestamps if t >= cutoff]
        if len(self._timestamps) >= self._max_commands:
            return False
        self._timestamps.append(now)
        return True


class CommandDispatcher:
    """MQTT command -> validation -> ZMQ REQ proxy.

    Receives MQTTMessage objects (from the paho command_queue), parses and
    validates the JSON payload, rate-limits, then forwards to the correct
    ZMQ REQ endpoint.  Reuses the same ZMQ REQ API as HMI and scheduler —
    no new business logic.

    ZMQ sockets are persistent (created at startup, one per target), matching
    the HMI pattern.  Blocking send/recv is dispatched to a thread executor
    to keep the asyncio event loop free.
    """

    # -----------------------------------------------------------------------
    # Command routing table (from CONTEXT.md locked decisions)
    # -----------------------------------------------------------------------

    COMMAND_ROUTES: dict[str, dict[str, Any]] = {
        "mode_change": {"target": "control_cmd", "action": "mode_change"},
        "setpoint": {"target": "control_cmd", "action": "manual_setpoint"},
        "priority": {"target": "control_cmd", "action": "source_priority"},
        "fault_reset": {"target": "control_cmd", "action": "fault_reset"},
        "maintenance": {"target": "control_cmd", "action": None},  # dynamic
        "alarm_ack": {"target": "alarm_cmd", "action": "acknowledge"},
        "ota_update": {"target": "ota_cmd", "action": "start_update"},
    }

    def __init__(
        self,
        publisher: MqttPublisher,
        *,
        control_cmd_endpoint: str | None = None,
        alarm_cmd_endpoint: str | None = None,
        ota_cmd_endpoint: str | None = None,
    ) -> None:
        """Create and connect ZMQ REQ sockets for command forwarding.

        Args:
            publisher: MqttPublisher instance used to publish command responses.
            control_cmd_endpoint: ZMQ REQ address for control_manager commands.
                Defaults to SOCK_CONTROL_CMD.
            alarm_cmd_endpoint: ZMQ REQ address for alarm_manager commands.
                Defaults to SOCK_ALARM_CMD.
        """
        self._publisher: MqttPublisher = publisher
        self._rate_limiter: RateLimiter = RateLimiter(max_commands=10, window_s=60.0)

        self._zmq_ctx: zmq.Context = zmq.Context()

        # Control command REQ socket
        self._control_sock: zmq.Socket = self._zmq_ctx.socket(zmq.REQ)
        self._control_sock.setsockopt(zmq.LINGER, 0)
        self._control_sock.setsockopt(zmq.RCVTIMEO, 5000)
        control_ep: str = control_cmd_endpoint or SOCK_CONTROL_CMD
        self._control_sock.connect(control_ep)

        # Alarm command REQ socket
        self._alarm_sock: zmq.Socket = self._zmq_ctx.socket(zmq.REQ)
        self._alarm_sock.setsockopt(zmq.LINGER, 0)
        self._alarm_sock.setsockopt(zmq.RCVTIMEO, 5000)
        alarm_ep: str = alarm_cmd_endpoint or SOCK_ALARM_CMD
        self._alarm_sock.connect(alarm_ep)

        # OTA command REQ socket
        self._ota_sock: zmq.Socket = self._zmq_ctx.socket(zmq.REQ)
        self._ota_sock.setsockopt(zmq.LINGER, 0)
        self._ota_sock.setsockopt(zmq.RCVTIMEO, 30000)  # OTA operations can be slow
        ota_ep: str = ota_cmd_endpoint or SOCK_OTA_CMD
        self._ota_sock.connect(ota_ep)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def dispatch(self, mqtt_message: Any) -> None:
        """Parse, validate, rate-limit, and forward an MQTT command.

        Validation failures and downstream errors result in an error response
        published back to MQTT.  On success, the downstream response is
        forwarded to the MQTT response topic.

        Args:
            mqtt_message: paho MQTTMessage with a .payload bytes attribute.
        """
        # 1. Parse JSON payload
        try:
            data: dict[str, Any] = json.loads(mqtt_message.payload.decode("utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse command JSON: %s", exc)
            self._publisher.publish_response(
                request_id="unknown",
                status=STATUS_ERROR,
                result=None,
                error_msg=f"Invalid JSON payload: {exc}",
            )
            return

        # 2. Extract required fields
        command: str | None = data.get("command")
        params: dict[str, Any] = data.get("params", {})
        request_id: str | None = data.get("request_id")

        if not command or not request_id:
            missing: str = "command" if not command else "request_id"
            logger.warning("Command message missing required field: %s", missing)
            self._publisher.publish_response(
                request_id=request_id or "unknown",
                status=STATUS_ERROR,
                result=None,
                error_msg=f"Missing required field: {missing}",
            )
            return

        # 3. Rate limit check
        if not self._rate_limiter.allow():
            logger.warning("Rate limit exceeded for command '%s'", command)
            self._publisher.publish_response(
                request_id=request_id,
                status=STATUS_ERROR,
                result=None,
                error_msg="Rate limit exceeded: max 10 commands per 60 seconds",
            )
            return

        # 4. Validate command name
        route: dict[str, Any] | None = self.COMMAND_ROUTES.get(command)
        if route is None:
            logger.warning("Unknown command: %s", command)
            self._publisher.publish_response(
                request_id=request_id,
                status=STATUS_ERROR,
                result=None,
                error_msg=f"Unknown command: {command}",
            )
            return

        # 5. Validate params per command type
        validation_error: str | None = self._validate_params(command, params)
        if validation_error:
            logger.warning("Command '%s' param validation failed: %s", command, validation_error)
            self._publisher.publish_response(
                request_id=request_id,
                status=STATUS_ERROR,
                result=None,
                error_msg=validation_error,
            )
            return

        # 6. Build ZMQ action (special case: maintenance -> maintenance_enter/exit)
        if command == "maintenance":
            action: str = f"maintenance_{params['action']}"
        else:
            action = route["action"]

        # 7. Encode command request
        encoded: bytes = encode_command_request(action, params)

        # 8. Select target socket
        target: str = route["target"]
        target_sockets: dict[str, zmq.Socket] = {
            "control_cmd": self._control_sock,
            "alarm_cmd": self._alarm_sock,
            "ota_cmd": self._ota_sock,
        }
        sock: zmq.Socket = target_sockets[target]

        # 9. Send via ZMQ REQ (blocking — run in executor to avoid blocking event loop)
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        try:
            response_bytes: bytes = await loop.run_in_executor(
                None, self._send_recv, sock, encoded
            )
            response: dict[str, Any] = decode_command_response(response_bytes)
        except Exception as exc:
            logger.error("ZMQ command forward failed: %s", exc)
            self._publisher.publish_response(
                request_id=request_id,
                status=STATUS_ERROR,
                result=None,
                error_msg=f"Downstream command failed: {exc}",
            )
            return

        # 10. Publish MQTT response
        self._publisher.publish_response(
            request_id=request_id,
            status=response.get("status", STATUS_ERROR),
            result=response.get("result"),
            error_msg=response.get("error_msg"),
        )

    def cleanup(self) -> None:
        """Close ZMQ sockets and terminate the context."""
        try:
            self._control_sock.close(linger=0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._alarm_sock.close(linger=0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._ota_sock.close(linger=0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._zmq_ctx.term()
        except Exception:  # noqa: BLE001
            pass

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _validate_params(self, command: str, params: dict[str, Any]) -> str | None:
        """Validate command-specific params.

        Returns an error message string if validation fails, or None if valid.

        Args:
            command: Command name string.
            params: Command params dict from the MQTT payload.

        Returns:
            None if valid, or a human-readable error string.
        """
        if command == "mode_change":
            valid_states: list[str] = ["idle", "standby"]
            target_state: Any = params.get("target_state")
            if target_state not in valid_states:
                return f"mode_change: target_state must be one of {valid_states}, got {target_state!r}"

        elif command == "setpoint":
            power_kw: Any = params.get("power_kw")
            if not isinstance(power_kw, (int, float)):
                return f"setpoint: power_kw must be numeric, got {type(power_kw).__name__}"

        elif command == "priority":
            valid_modes: list[str] = ["day", "night", "manual"]
            mode: Any = params.get("mode")
            if mode not in valid_modes:
                return f"priority: mode must be one of {valid_modes}, got {mode!r}"

        elif command == "maintenance":
            valid_actions: list[str] = ["enter", "exit"]
            action: Any = params.get("action")
            if action not in valid_actions:
                return f"maintenance: action must be one of {valid_actions}, got {action!r}"

        elif command == "alarm_ack":
            alarm_id: Any = params.get("alarm_id")
            if not isinstance(alarm_id, str) or not alarm_id:
                return "alarm_ack: alarm_id must be a non-empty string"

        # fault_reset: no params required
        return None

    def _send_recv(self, sock: zmq.Socket, encoded: bytes) -> bytes:
        """Blocking ZMQ REQ send + recv (called via run_in_executor).

        Args:
            sock: ZMQ REQ socket to use.
            encoded: Encoded command request bytes.

        Returns:
            Raw response bytes from the REP server.

        Raises:
            zmq.ZMQError: On socket error or timeout.
        """
        sock.send(encoded)
        return sock.recv()
