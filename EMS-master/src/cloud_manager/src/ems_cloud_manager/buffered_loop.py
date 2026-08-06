"""BufferedCloudLoop: CloudLoop subclass with offline buffer and replay (CLOUD-05).

Overrides the two publish hook points (_do_publish_telemetry and
_do_publish_event) to route messages to a BufferManager when MQTT is offline.
Also overrides _periodic_publish and _zmq_event_forwarder to remove their
connection-guard checks so data actually reaches the buffer when offline.

Adds a sixth async task (_buffer_replay_task) that drains the buffer FIFO at
10 msg/s (configurable via _replay_rate) whenever the MQTT connection is live.

Buffer progress (files_remaining, mb_remaining) is published to the ZMQ cloud
PUB socket after each replay cycle via _publish_buffer_status().
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import zmq

from ems_cloud_manager.buffer import BufferManager
from ems_cloud_manager.loop import CloudLoop
from ems_common.ipc import (
    TOPIC_ALARM,
    TOPIC_COMM_FAULT,
    TOPIC_STATE_CHANGE,
    encode_event,
    encode_telemetry,
)

try:
    import msgpack
except ImportError:  # pragma: no cover
    msgpack = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from ems_cloud_manager.publisher import MqttPublisher

logger: logging.Logger = logging.getLogger(__name__)


class BufferedCloudLoop(CloudLoop):
    """CloudLoop subclass that buffers telemetry/events offline and replays on reconnect.

    Adds a replay task that drains the BufferManager FIFO at a configurable rate
    (default 10 msg/s) whenever MQTT is connected. Live publishes run concurrently
    with replay via the existing asyncio task structure — live data is never delayed
    by the replay drain.

    Constructor:
        config: Validated cloud_config dict.
        publisher: MqttPublisher instance.
        buffer_manager: BufferManager instance (pre-configured directory/retention).
        **kwargs: Forwarded to CloudLoop.__init__ (endpoints, intervals, etc.)
    """

    def __init__(
        self,
        config: dict[str, Any],
        publisher: "MqttPublisher",
        buffer_manager: BufferManager,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, publisher, **kwargs)
        self._buffer: BufferManager = buffer_manager
        self._replay_rate: float = 10.0  # msg/s

    # -----------------------------------------------------------------------
    # Phase 23 hook overrides
    # -----------------------------------------------------------------------

    def _do_publish_telemetry(self, payload: dict[str, Any]) -> None:
        """Route telemetry to MQTT when connected, or to the offline buffer.

        Args:
            payload: Consolidated telemetry dict with 'ts' and 'data' keys.
        """
        if self._publisher.connected:
            self._publisher.publish_telemetry(payload)
        else:
            self._buffer.write("telemetry", "telemetry", payload)

    def _do_publish_event(self, topic: str, payload: dict[str, Any]) -> None:
        """Route event to MQTT when connected, or to the offline buffer.

        Args:
            topic: Event topic suffix (e.g. "alarm", "state_change").
            payload: Event payload dict with 'ts', 'event_type', 'data' keys.
        """
        if self._publisher.connected:
            self._publisher.publish_event(topic, payload)
        else:
            self._buffer.write("event", topic, payload)

    # -----------------------------------------------------------------------
    # Override _periodic_publish: remove connection guard
    # -----------------------------------------------------------------------

    async def _periodic_publish(self) -> None:
        """Publish consolidated telemetry snapshot at the configured interval.

        Critical override: removes the ``if not self._publisher.connected: continue``
        guard present in the base class. BufferedCloudLoop always processes the
        snapshot and delegates the routing decision to _do_publish_telemetry(),
        which writes to the buffer when offline.

        The ``if not self._telemetry_snapshot: continue`` guard is kept — no point
        serialising an empty snapshot.
        """
        while not self._stop_event.is_set():
            await asyncio.sleep(self._interval_s)

            if not self._telemetry_snapshot:
                continue

            # NOTE: connection guard intentionally removed — _do_publish_telemetry
            # decides whether to publish to MQTT or write to the offline buffer.
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

    # -----------------------------------------------------------------------
    # Override _zmq_event_forwarder: remove connection guard
    # -----------------------------------------------------------------------

    async def _zmq_event_forwarder(self) -> None:
        """Poll the alarm SUB socket and forward events (or buffer them offline).

        Critical override: removes the ``if self._publisher.connected:`` guard
        around the _do_publish_event() call. BufferedCloudLoop always calls the
        hook so events are buffered when offline. The logger PUSH (audit trail)
        logic is preserved unchanged.
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

                # NOTE: connection guard intentionally removed — _do_publish_event
                # decides whether to publish to MQTT or write to the offline buffer.
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

    # -----------------------------------------------------------------------
    # Override run: add replay task
    # -----------------------------------------------------------------------

    async def run(self) -> None:
        """Start the publisher and run all six async tasks until stopped.

        Adds _buffer_replay_task as a sixth coroutine alongside the five from
        the base CloudLoop. Call cleanup() after this coroutine returns.
        """
        self._publisher.start()

        await asyncio.gather(
            self._zmq_telemetry_collector(),
            self._periodic_publish(),
            self._zmq_event_forwarder(),
            self._heartbeat_publisher(),
            self._command_dispatcher_loop(),
            self._buffer_replay_task(),
        )

    # -----------------------------------------------------------------------
    # Replay task
    # -----------------------------------------------------------------------

    async def _buffer_replay_task(self) -> None:
        """Drain the offline buffer FIFO at _replay_rate msg/s whenever connected.

        Behaviour:
        - Waits while MQTT is disconnected (polls every 1s).
        - On reconnect: calls buffer.flush() to make the current write file
          readable, then drains oldest-first.
        - Each message is published and the task sleeps 1/_replay_rate seconds
          (default 0.1s for 10 msg/s throttle).
        - If MQTT disconnects mid-drain, stops and waits for next reconnect.
        - Files are deleted only after all their records are successfully published.
        - After each replay cycle, publishes buffer stats to ZMQ cloud status.
        """
        _replay_interval: float = 1.0 / self._replay_rate  # 0.1s at default rate

        while not self._stop_event.is_set():
            if not self._publisher.connected:
                await asyncio.sleep(1.0)
                continue

            # Flush the current write file so drain() can read it
            self._buffer.flush()

            replayed_this_cycle: int = 0

            for file_path, records in self._buffer.drain():
                if not self._publisher.connected:
                    break  # stop mid-replay; resume on next reconnect

                all_published: bool = True
                for record in records:
                    if not self._publisher.connected:
                        all_published = False
                        break

                    msg_type: str = record.get("type", "telemetry")
                    topic: str = record.get("topic", "telemetry")
                    payload: dict[str, Any] = record.get("payload", {})

                    try:
                        if msg_type == "telemetry":
                            self._publisher.publish_telemetry(payload)
                        else:
                            self._publisher.publish_event(topic, payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Replay publish failed: %s", exc)
                        all_published = False
                        break

                    replayed_this_cycle += 1
                    # Recalculate interval in case _replay_rate was changed
                    _replay_interval = 1.0 / self._replay_rate
                    await asyncio.sleep(_replay_interval)

                if all_published:
                    file_path.unlink(missing_ok=True)
                    self._buffer._remove_empty_parents(file_path)
                    logger.debug("Replay deleted buffer file: %s", file_path)
                else:
                    break  # connection dropped mid-file; keep file for next reconnect

            if replayed_this_cycle == 0:
                # Nothing to replay — back off before checking again
                await asyncio.sleep(5.0)

            self._publish_buffer_status()

    # -----------------------------------------------------------------------
    # Buffer status publishing
    # -----------------------------------------------------------------------

    def _publish_buffer_status(self) -> None:
        """Publish buffer statistics to the ZMQ cloud PUB socket.

        Appends ``buffer_files_remaining`` and ``buffer_mb_remaining`` to the
        standard cloud telemetry payload so the HMI and cloud can monitor buffer
        backlog. Uses the same encode_telemetry/SOCK_CLOUD_PUB path as
        MqttPublisher._publish_cloud_status().
        """
        stats: dict[str, Any] = self._buffer.stats
        payload: dict[str, Any] = {
            "ts": int(time.time() * 1000),
            "buffer_files_remaining": stats["files_remaining"],
            "buffer_mb_remaining": stats["mb_remaining"],
        }
        raw: bytes = encode_telemetry(
            timestamp_ms=payload["ts"],
            seq=0,
            source="cloud_manager",
            topic="cloud_buffer",
            payload=payload,
        )
        try:
            self._publisher._cloud_pub.send_string(
                "cloud_buffer", zmq.SNDMORE | zmq.NOBLOCK
            )
            self._publisher._cloud_pub.send(raw, zmq.NOBLOCK)
        except zmq.ZMQError as exc:
            logger.debug("Buffer status ZMQ publish failed: %s", exc)
        except AttributeError:
            # In tests, publisher._cloud_pub may not exist (mocked publisher)
            pass
