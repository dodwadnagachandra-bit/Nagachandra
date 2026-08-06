"""CloudLoop async engine for EMS cloud_manager.

Implements five concurrent async tasks:
- _zmq_telemetry_collector: subscribes to SOCK_TELEMETRY (all topics) at ~100Hz
  poll rate; stores latest-value per topic in self._telemetry_snapshot.
- _periodic_publish: fires at telemetry.interval_s, builds a consolidated JSON
  payload from the snapshot, clears the snapshot, then calls
  publisher.publish_telemetry(). Skips publish if snapshot is empty or
  publisher is not connected.
- _zmq_event_forwarder: subscribes to SOCK_ALARM_PUB for alarm/state_change/
  comm_fault events; forwards to publisher.publish_event() when connected.
- _heartbeat_publisher: publishes device status to {prefix}/status every
  heartbeat_interval seconds when MQTT is connected (CLOUD-07).
- _command_dispatcher_loop: polls publisher.command_queue and routes commands
  to the CommandDispatcher when one is provided (CLOUD-06).

Design notes:
- Latest-value-wins: only the most recent message per topic is kept.
- Single consolidated MQTT message per interval (not per-topic).
- JSON payload (not MessagePack) for MQTT cloud side.
- Missing topics (no data since last publish) are omitted -- never null.
- QoS 0 for telemetry (periodic, replaceable), QoS 1 for events (critical).
- Phase 23 hook points: _do_publish_telemetry / _do_publish_event can be
  overridden/wrapped by the offline buffer layer.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from typing import TYPE_CHECKING, Any

import zmq

from ems_cloud_manager import __version__
from ems_common.ipc import (
    SOCK_ALARM_PUB,
    SOCK_LOGGER,
    SOCK_TELEMETRY,
    TOPIC_ALARM,
    TOPIC_COMM_FAULT,
    TOPIC_STATE_CHANGE,
    decode_telemetry,
    encode_event,
)

try:
    import msgpack
except ImportError:  # pragma: no cover
    msgpack = None  # type: ignore[assignment]

from ems_cloud_manager.publisher import MqttPublisher

if TYPE_CHECKING:
    from ems_cloud_manager.dispatcher import CommandDispatcher

logger: logging.Logger = logging.getLogger(__name__)

# Default heartbeat interval in seconds (CLOUD-07)
_DEFAULT_HEARTBEAT_INTERVAL: float = 60.0


class CloudLoop:
    """Async engine that bridges local ZMQ telemetry/events to the MQTT cloud.

    Five async tasks run concurrently:
    1. _zmq_telemetry_collector -- receives 1Hz telemetry from data_manager.
    2. _periodic_publish -- sends consolidated snapshot to cloud at interval_s.
    3. _zmq_event_forwarder -- forwards alarms/state_changes to cloud with QoS 1.
    4. _heartbeat_publisher -- publishes device status every heartbeat_interval s.
    5. _command_dispatcher_loop -- routes MQTT commands to ZMQ via CommandDispatcher.

    Phase 23 (offline buffer) integration points:
    - Override _do_publish_telemetry(payload) to route through offline buffer.
    - Override _do_publish_event(topic, payload) to buffer events when offline.

    Usage:
        loop = CloudLoop(config, publisher)
        await loop.run()   # blocks until stop_event is set
        loop.cleanup()
    """

    def __init__(
        self,
        config: dict[str, Any],
        publisher: MqttPublisher,
        *,
        telemetry_sub_endpoint: str | None = None,
        alarm_sub_endpoint: str | None = None,
        logger_push_endpoint: str | None = None,
        heartbeat_interval: float | None = None,
        dispatcher: CommandDispatcher | None = None,
    ) -> None:
        """Initialise CloudLoop with config and a pre-built MqttPublisher.

        Args:
            config: Validated cloud_config dict (from load_cloud_config).
            publisher: MqttPublisher instance (start() not yet called).
            telemetry_sub_endpoint: ZMQ endpoint for telemetry SUB socket.
                Defaults to SOCK_TELEMETRY ("ipc:///run/ems/telemetry.sock").
            alarm_sub_endpoint: ZMQ endpoint for alarm/event SUB socket.
                Defaults to SOCK_ALARM_PUB ("ipc:///run/ems/alarm_pub.sock").
            logger_push_endpoint: ZMQ PUSH endpoint for event logging.
                Defaults to SOCK_LOGGER ("ipc:///run/ems/logger.sock").
            heartbeat_interval: Seconds between heartbeat publishes.
                Defaults to 60. Pass 0 in tests to fire immediately.
            dispatcher: Optional CommandDispatcher for MQTT->ZMQ command routing.
                When None, inbound MQTT commands are silently discarded.
        """
        self._config: dict[str, Any] = config
        self._publisher: MqttPublisher = publisher
        self._dispatcher: CommandDispatcher | None = dispatcher

        telemetry_cfg: dict[str, Any] = config["telemetry"]
        self._interval_s: float = float(telemetry_cfg["interval_s"])
        self._prefix: str = telemetry_cfg["topic_prefix"]

        # Heartbeat interval (CLOUD-07): 60s default, overridable for testing
        self._heartbeat_interval: float = (
            heartbeat_interval
            if heartbeat_interval is not None
            else _DEFAULT_HEARTBEAT_INTERVAL
        )

        # Latest-value-wins accumulator: topic -> payload dict
        self._telemetry_snapshot: dict[str, Any] = {}

        # Stop signal for all tasks
        self._stop_event: asyncio.Event = asyncio.Event()

        # ZMQ context and sockets
        self._zmq_ctx: zmq.Context = zmq.Context()

        # SUB socket for 1Hz telemetry from data_manager
        self._telemetry_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
        self._telemetry_sub.connect(telemetry_sub_endpoint or SOCK_TELEMETRY)
        self._telemetry_sub.setsockopt_string(zmq.SUBSCRIBE, "")  # all topics

        # SUB socket for alarm/event PUB from alarm_manager
        self._alarm_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
        self._alarm_sub.connect(alarm_sub_endpoint or SOCK_ALARM_PUB)
        for _topic in (TOPIC_ALARM, TOPIC_STATE_CHANGE, TOPIC_COMM_FAULT):
            self._alarm_sub.setsockopt_string(zmq.SUBSCRIBE, _topic)

        # PUSH socket for forwarding cloud events to logger
        self._logger_push: zmq.Socket = self._zmq_ctx.socket(zmq.PUSH)
        self._logger_push.connect(logger_push_endpoint or SOCK_LOGGER)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    @property
    def stop_event(self) -> asyncio.Event:
        """Asyncio event; set this to stop all running tasks."""
        return self._stop_event

    async def run(self) -> None:
        """Start the publisher and run all five async tasks until stopped.

        Call cleanup() after this coroutine returns to release ZMQ resources.
        """
        self._publisher.start()

        await asyncio.gather(
            self._zmq_telemetry_collector(),
            self._periodic_publish(),
            self._zmq_event_forwarder(),
            self._heartbeat_publisher(),
            self._command_dispatcher_loop(),
        )

    def cleanup(self) -> None:
        """Close ZMQ sockets and terminate the ZMQ context.

        Also calls publisher.cleanup() to stop the paho network loop.
        """
        for sock in (self._telemetry_sub, self._alarm_sub, self._logger_push):
            try:
                sock.close(linger=0)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._zmq_ctx.term()
        except Exception:  # noqa: BLE001
            pass
        self._publisher.cleanup()

    # -----------------------------------------------------------------------
    # Phase 23 hook points (override in subclass or monkeypatch)
    # -----------------------------------------------------------------------

    def _do_publish_telemetry(self, payload: dict[str, Any]) -> None:
        """Publish telemetry payload to MQTT.

        Phase 23 can wrap this method to route through an offline buffer
        when publisher.connected is False.

        Args:
            payload: Consolidated telemetry dict with 'ts' and 'data' keys.
        """
        self._publisher.publish_telemetry(payload)

    def _do_publish_event(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish an event to MQTT with QoS 1.

        Phase 23 can wrap this method to buffer events when offline.

        Args:
            topic: Event topic suffix (e.g. "alarm", "state_change").
            payload: Event payload dict with 'ts', 'event_type', 'data' keys.
        """
        self._publisher.publish_event(topic, payload)

    # -----------------------------------------------------------------------
    # Async tasks
    # -----------------------------------------------------------------------

    async def _zmq_telemetry_collector(self) -> None:
        """Poll the ZMQ telemetry SUB socket at ~100Hz and accumulate latest values.

        ZMQ frame format: [topic_bytes, msgpack_body_bytes]
        Body is a telemetry envelope with a "payload" key containing
        the actual telemetry data dict.

        Latest-value-wins: if two messages arrive for the same topic before
        the next periodic publish, only the second (newer) value is kept.
        """
        while not self._stop_event.is_set():
            try:
                frames: list[bytes] = self._telemetry_sub.recv_multipart(
                    flags=zmq.NOBLOCK
                )
                if len(frames) < 2:
                    await asyncio.sleep(0.01)
                    continue

                topic: str = frames[0].decode("utf-8", errors="replace")
                try:
                    msg: dict[str, Any] = decode_telemetry(frames[1])
                except Exception:  # noqa: BLE001
                    logger.debug("Failed to decode telemetry for topic %s", topic)
                    continue

                # Extract the nested payload (real data), fall back to full msg
                payload: Any = msg.get("payload", msg)
                self._telemetry_snapshot[topic] = payload

            except zmq.Again:
                await asyncio.sleep(0.01)  # no messages, yield to event loop

    async def _periodic_publish(self) -> None:
        """Publish consolidated telemetry snapshot at the configured interval.

        Sleeps for interval_s between publishes. Skips if snapshot is empty
        or publisher is not connected. After copying snapshot, clears it so
        topics with no new data since the last publish are omitted next cycle.

        Payload format: {"ts": epoch_ms, "data": {"pcs": {...}, ...}}
        """
        while not self._stop_event.is_set():
            await asyncio.sleep(self._interval_s)

            if not self._telemetry_snapshot:
                continue

            if not self._publisher.connected:
                # Don't waste CPU serialising when offline; Phase 23 handles buffering
                continue

            # Copy and clear atomically (single-threaded asyncio)
            snapshot_copy: dict[str, Any] = dict(self._telemetry_snapshot)
            self._telemetry_snapshot.clear()

            payload: dict[str, Any] = {
                "ts": int(time.time() * 1000),
                "data": snapshot_copy,
            }

            try:
                self._do_publish_telemetry(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telemetry publish failed: %s", exc)

    async def _zmq_event_forwarder(self) -> None:
        """Poll the alarm SUB socket and forward events to MQTT with QoS 1.

        Subscribed topics: alarm, state_change, comm_fault (from alarm_manager).
        ZMQ frame format: [topic_bytes, msgpack_body_bytes]

        Events are forwarded to publisher.publish_event() only when connected.
        All events are also forwarded to the logger PUSH socket for audit trail.

        Cloud event payload: {"ts": epoch_ms, "event_type": topic, "data": decoded_body}
        """
        while not self._stop_event.is_set():
            try:
                frames: list[bytes] = self._alarm_sub.recv_multipart(flags=zmq.NOBLOCK)
                if len(frames) < 2:
                    await asyncio.sleep(0.01)
                    continue

                topic: str = frames[0].decode("utf-8", errors="replace")
                try:
                    import msgpack as _msgpack

                    decoded_body: dict[str, Any] = _msgpack.unpackb(
                        frames[1], raw=False
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("Failed to decode event for topic %s", topic)
                    continue

                event_payload: dict[str, Any] = {
                    "ts": int(time.time() * 1000),
                    "event_type": topic,
                    "data": decoded_body,
                }

                if self._publisher.connected:
                    try:
                        self._do_publish_event(topic, event_payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Event publish failed: %s", exc)

                # Always log cloud events for audit (regardless of MQTT connectivity)
                try:
                    log_bytes: bytes = encode_event(
                        timestamp_ms=event_payload["ts"],
                        source="cloud_manager",
                        severity="info",
                        event_type=f"cloud_event.{topic}",
                        message=f"Cloud event forwarded: {topic}",
                        data=decoded_body,
                    )
                    self._logger_push.send(log_bytes, zmq.NOBLOCK)
                except zmq.ZMQError:
                    pass  # Logger not available; non-fatal

            except zmq.Again:
                await asyncio.sleep(0.01)  # no events, yield to event loop

    async def _heartbeat_publisher(self) -> None:
        """Publish device heartbeat to {prefix}/status at the configured interval.

        The heartbeat is only published when MQTT is connected. The uptime
        counter starts when this coroutine first runs (process uptime proxy).

        Payload: {device_id, uptime_s, version, connected, ts}
        """
        start_time: float = time.monotonic()
        while not self._stop_event.is_set():
            await asyncio.sleep(self._heartbeat_interval)
            if self._stop_event.is_set():
                break
            if not self._publisher.connected:
                continue

            # Extract device_id from topic prefix: "ems/RES-001" -> "RES-001"
            device_id: str = self._prefix.split("/")[-1]
            heartbeat_payload: dict[str, Any] = {
                "device_id": device_id,
                "uptime_s": int(time.monotonic() - start_time),
                "version": __version__,
                "connected": True,
                "ts": int(time.time() * 1000),
            }
            try:
                self._publisher.publish_heartbeat(heartbeat_payload)
                logger.debug(
                    "Heartbeat published: device=%s uptime=%ds",
                    device_id,
                    heartbeat_payload["uptime_s"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Heartbeat publish failed: %s", exc)

    async def _command_dispatcher_loop(self) -> None:
        """Poll the MQTT command queue and dispatch commands to ZMQ.

        Drains publisher.command_queue (thread-safe queue populated by the
        paho callback thread) and calls dispatcher.dispatch() for each message.
        When no dispatcher is configured, messages are discarded.
        """
        while not self._stop_event.is_set():
            try:
                msg: Any = self._publisher.command_queue.get_nowait()
                if self._dispatcher is not None:
                    try:
                        await self._dispatcher.dispatch(msg)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Command dispatch error: %s", exc)
            except queue.Empty:
                pass
            await asyncio.sleep(0.1)
