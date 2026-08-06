"""OtaManager async engine — ZMQ PUB status, REP commands, MQTT notify subscription.

Bridges the OTA state machine to the rest of the EMS system:
- Binds SOCK_OTA_PUB (PUB): publishes state change telemetry at each transition
- Binds SOCK_OTA_CMD (REP): handles get_version, rollback, start_update commands
- Checks pending_health_check boot flag on startup

Design mirrors cloud_manager/loop.py: NOBLOCK recv + asyncio.sleep(0.05) poll loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import zmq
import zmq.asyncio

from ems_common.ipc import (
    SOCK_OTA_CMD,
    SOCK_OTA_PUB,
    TOPIC_OTA,
    STATUS_OK,
    STATUS_ERROR,
    decode_command_request,
    encode_command_response,
    encode_telemetry,
)
from ems_ota_manager.state_machine import OtaState, OtaStateMachine

if TYPE_CHECKING:
    from ems_ota_manager.partition import PartitionBackend

logger: logging.Logger = logging.getLogger(__name__)

# Interval between status heartbeats during active update states (seconds)
_DEFAULT_STATUS_INTERVAL_S: float = 5.0

# Command loop poll sleep
_CMD_POLL_SLEEP_S: float = 0.05


class OtaManager:
    """Async engine that exposes OTA state machine via ZMQ PUB/REP.

    Lifecycle:
    1. On startup: check boot flag for pending_health_check.
    2. Publish initial IDLE status.
    3. Run concurrent tasks: _command_loop, _status_publish_loop.
    4. On stop_event: cancel tasks, cleanup ZMQ sockets.
    """

    def __init__(
        self,
        config: dict[str, Any],
        state_machine: OtaStateMachine,
        partition: "PartitionBackend",
        ota_pub_endpoint: str | None = None,
        ota_cmd_endpoint: str | None = None,
        *,
        ota_pub_socket: zmq.asyncio.Socket | None = None,
        ota_rep_socket: zmq.asyncio.Socket | None = None,
    ) -> None:
        """Initialise OtaManager.

        Args:
            config: Validated OTA config dict.
            state_machine: Wired OtaStateMachine to drive.
            partition: PartitionBackend for boot flag reads.
            ota_pub_endpoint: ZMQ PUB bind address (env override or default
                SOCK_OTA_PUB). Ignored if ota_pub_socket is provided.
            ota_cmd_endpoint: ZMQ REP bind address (env override or default
                SOCK_OTA_CMD). Ignored if ota_rep_socket is provided.
            ota_pub_socket: Pre-built PUB socket (for testing).
            ota_rep_socket: Pre-built REP socket (for testing).
        """
        self._config: dict[str, Any] = config
        self._state_machine: OtaStateMachine = state_machine
        self._partition: "PartitionBackend" = partition
        self._ota_pub_endpoint: str = ota_pub_endpoint or SOCK_OTA_PUB
        self._ota_cmd_endpoint: str = ota_cmd_endpoint or SOCK_OTA_CMD

        # ZMQ — may be pre-injected for tests
        self._owns_context: bool = ota_pub_socket is None and ota_rep_socket is None
        if self._owns_context:
            self._zmq_ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        else:
            self._zmq_ctx = None  # type: ignore[assignment]

        self._ota_pub: zmq.asyncio.Socket | None = ota_pub_socket
        self._ota_rep: zmq.asyncio.Socket | None = ota_rep_socket
        self._pub_owned: bool = ota_pub_socket is None
        self._rep_owned: bool = ota_rep_socket is None

        self._seq: int = 0
        self.stop_event: asyncio.Event = asyncio.Event()

        # Wire ourselves as the state machine's callback
        self._state_machine._on_state_change_cb = self._on_state_change

        # Status publish interval from config
        download_cfg: dict[str, Any] = self._config.get("download", {})
        self._status_interval_s: float = float(
            download_cfg.get("progress_interval_s", _DEFAULT_STATUS_INTERVAL_S)
        )

    # ------------------------------------------------------------------
    # ZMQ socket setup
    # ------------------------------------------------------------------

    def _ensure_pub_socket(self) -> zmq.asyncio.Socket:
        """Return or create and bind the PUB socket."""
        if self._ota_pub is None:
            ctx: zmq.asyncio.Context = self._zmq_ctx
            self._ota_pub = ctx.socket(zmq.PUB)
            self._ota_pub.bind(self._ota_pub_endpoint)
            logger.info("OTA PUB bound to %s", self._ota_pub_endpoint)
        return self._ota_pub

    def _ensure_rep_socket(self) -> zmq.asyncio.Socket:
        """Return or create and bind the REP socket."""
        if self._ota_rep is None:
            ctx: zmq.asyncio.Context = self._zmq_ctx
            self._ota_rep = ctx.socket(zmq.REP)
            self._ota_rep.bind(self._ota_cmd_endpoint)
            logger.info("OTA REP bound to %s", self._ota_cmd_endpoint)
        return self._ota_rep

    # ------------------------------------------------------------------
    # State change callback (wired into state machine)
    # ------------------------------------------------------------------

    def _on_state_change(
        self, new_state: OtaState, detail: dict[str, Any] | None = None
    ) -> None:
        """Publish a ZMQ telemetry message on every state transition.

        This is a sync method (called from OtaStateMachine) that uses
        zmq.NOBLOCK to send without blocking. Silently drops if PUB is
        not yet ready (slow-start race during tests).

        Args:
            new_state: The new OTA pipeline state.
            detail: Optional extra context about the transition.
        """
        try:
            pub: zmq.asyncio.Socket = self._ensure_pub_socket()
            ts: int = int(time.time() * 1000)
            self._seq += 1
            vs = self._state_machine.version_state
            payload: dict[str, Any] = {
                "state": new_state.value,
                "version_current": vs.current,
                "version_previous": vs.previous,
                "detail": detail,
                "ts": ts,
            }
            body: bytes = encode_telemetry(ts, self._seq, "ota_manager", TOPIC_OTA, payload)
            pub.send_multipart(
                [TOPIC_OTA.encode(), body], flags=zmq.NOBLOCK
            )
            logger.debug("OTA status published: state=%s", new_state.value)
        except zmq.Again:
            pass  # No subscribers — drop silently
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to publish OTA status: %s", exc)

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    async def _handle_one_command(self) -> None:
        """Receive and handle one command from the REP socket.

        Uses poll(timeout=50ms) to check for pending messages without
        blocking indefinitely. Returns immediately if no message arrives
        within 50 ms so the command_loop can re-check stop_event.

        Decodes action from the REP frame and dispatches:
        - get_version: returns current + previous from version_state
        - rollback: schedules do_manual_rollback as a background task
        - start_update: schedules start_update as a background task
        - unknown: returns error response
        """
        rep: zmq.asyncio.Socket = self._ensure_rep_socket()
        # Poll briefly so we can wake up for stop_event checks
        events: int = await rep.poll(timeout=50, flags=zmq.POLLIN)
        if not events:
            return
        raw: bytes = await rep.recv()

        try:
            action: str
            params: dict[str, Any]
            action, params = decode_command_request(raw)
        except Exception as exc:  # noqa: BLE001
            error_resp: bytes = encode_command_response(STATUS_ERROR, error_msg=f"Bad request: {exc}")
            await rep.send(error_resp)
            return

        try:
            if action == "get_version":
                vs = self._state_machine.version_state
                result: dict[str, Any] = {
                    "current": vs.current,
                    "previous": vs.previous,
                }
                await rep.send(encode_command_response(STATUS_OK, result=result))

            elif action == "rollback":
                asyncio.create_task(self._state_machine.do_manual_rollback())
                await rep.send(encode_command_response(STATUS_OK, result={"queued": True}))

            elif action == "start_update":
                asyncio.create_task(self._state_machine.start_update(params))
                await rep.send(encode_command_response(STATUS_OK, result={"queued": True}))

            else:
                await rep.send(
                    encode_command_response(
                        STATUS_ERROR, error_msg=f"Unknown action: {action}"
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("Error handling OTA command '%s': %s", action, exc)
            try:
                await rep.send(
                    encode_command_response(STATUS_ERROR, error_msg=str(exc))
                )
            except zmq.ZMQError:
                pass

    async def _command_loop(self) -> None:
        """Continuously poll REP socket for commands until stop_event is set."""
        while not self.stop_event.is_set():
            await self._handle_one_command()
            await asyncio.sleep(_CMD_POLL_SLEEP_S)

    # ------------------------------------------------------------------
    # Status heartbeat loop
    # ------------------------------------------------------------------

    async def _status_publish_loop(self) -> None:
        """Periodically re-publish current state during active update states."""
        while not self.stop_event.is_set():
            state: OtaState = self._state_machine.state
            if state in (OtaState.DOWNLOADING, OtaState.VERIFYING, OtaState.APPLYING):
                self._on_state_change(state, {"heartbeat": True})
            await asyncio.sleep(self._status_interval_s)

    # ------------------------------------------------------------------
    # Post-boot health check helper
    # ------------------------------------------------------------------

    async def _maybe_run_post_boot_health(self) -> None:
        """Check boot flag; run post-boot health check if pending."""
        try:
            flag = self._partition.read_boot_flag()
            if flag.pending_health_check:
                logger.info("Pending health check detected — running post-boot check")
                await self._state_machine.check_post_boot_health()
        except FileNotFoundError:
            logger.debug("No boot flag found — skipping post-boot health check")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error reading boot flag on startup: %s", exc)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the OTA manager main loop.

        1. Check pending health check from boot flag.
        2. Publish initial IDLE status.
        3. Start concurrent command_loop and status_publish_loop tasks.
        4. Wait for stop_event.
        5. Cancel tasks and cleanup.
        """
        logger.info("OTA manager starting")

        # Ensure sockets are ready
        self._ensure_pub_socket()
        self._ensure_rep_socket()

        await self._maybe_run_post_boot_health()

        # Publish initial idle status
        self._on_state_change(OtaState.IDLE, {"startup": True})

        cmd_task: asyncio.Task = asyncio.create_task(self._command_loop())
        status_task: asyncio.Task = asyncio.create_task(self._status_publish_loop())

        try:
            await self.stop_event.wait()
        finally:
            cmd_task.cancel()
            status_task.cancel()
            await asyncio.gather(cmd_task, status_task, return_exceptions=True)
            logger.info("OTA manager stopped")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Close ZMQ sockets and terminate context (if owned)."""
        if self._ota_pub is not None and self._pub_owned:
            self._ota_pub.close(linger=0)
        if self._ota_rep is not None and self._rep_owned:
            self._ota_rep.close(linger=0)
        if self._owns_context and self._zmq_ctx is not None:
            self._zmq_ctx.term()
