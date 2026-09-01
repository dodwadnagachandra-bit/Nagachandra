"""WebSocket telemetry bridge -- ZMQ SUB fan-out to browser clients.

Bridges 1Hz ZMQ PUB telemetry from data_manager to WebSocket clients.
Also bridges cloud and OTA status from cloud_manager and ota_manager.

Each client gets an asyncio.Queue; the bridge task broadcasts decoded
JSON messages to all connected clients using a zmq.asyncio.Poller
across up to 3 ZMQ SUB sockets.
"""

from __future__ import annotations

import asyncio
import logging

import zmq
import zmq.asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ems_common.ipc import (
    TOPIC_BMS_RACK,
    TOPIC_BTMS,
    TOPIC_CLOUD,
    TOPIC_GPIO,
    TOPIC_METER,
    TOPIC_OTA,
    TOPIC_PCS,
    TOPIC_SYSTEM,
    decode_telemetry,
)

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter()

# All telemetry topic prefixes to subscribe to on the main telemetry socket
_TELEMETRY_TOPICS: list[str] = [
    TOPIC_BMS_RACK,
    TOPIC_PCS,
    TOPIC_GPIO,
    TOPIC_METER,
    TOPIC_BTMS,
    TOPIC_SYSTEM,
]


class ClientManager:
    """Manages per-client asyncio.Queue instances for WebSocket fan-out.

    Each connected WebSocket client gets a Queue(maxsize=100).
    On broadcast, messages are pushed to all queues. If a queue is full,
    the oldest message is dropped before adding the new one.
    """

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue] = set()

    def add_client(self) -> asyncio.Queue:
        """Create a new client queue and add it to the set."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.clients.add(queue)
        return queue

    def remove_client(self, queue: asyncio.Queue) -> None:
        """Remove a client queue from the set."""
        self.clients.discard(queue)

    def broadcast(self, message: dict) -> None:
        """Send a message to all connected WebSocket clients.

        If a client's queue is full, the oldest message is dropped
        to make room for the new message.
        """
        for queue in self.clients:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

                queue.put_nowait(message)


async def telemetry_bridge(
    zmq_ctx: zmq.asyncio.Context,
    client_manager: ClientManager,
    socket_path: str,
    cloud_socket_path: str = "",
    ota_socket_path: str = "",
) -> None:
    """Background task: subscribe to ZMQ PUB sources and broadcast to clients.

    Telemetry messages come from:
        ipc:///run/ems/telemetry.sock

    The ZMQ message consists of two parts:

        parts[0] -> topic
        parts[1] -> MessagePack envelope

    Normal telemetry envelopes contain:
        ts, seq, src, topic, payload

    System/health messages may contain:
        ts, src, severity, event_type, message, data
    """

    poller: zmq.asyncio.Poller = zmq.asyncio.Poller()

    # ------------------------------------------------------------------
    # Main telemetry socket
    # ------------------------------------------------------------------

    sub_telemetry: zmq.asyncio.Socket = zmq_ctx.socket(zmq.SUB)

    for topic in _TELEMETRY_TOPICS:
        sub_telemetry.setsockopt_string(zmq.SUBSCRIBE, topic)

    sub_telemetry.connect(socket_path)
    poller.register(sub_telemetry, zmq.POLLIN)

    logger.info(
        "Telemetry bridge connected to %s",
        socket_path,
    )

    # ------------------------------------------------------------------
    # Cloud socket
    # ------------------------------------------------------------------

    sub_cloud: zmq.asyncio.Socket | None = None

    if cloud_socket_path:
        sub_cloud = zmq_ctx.socket(zmq.SUB)
        sub_cloud.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CLOUD)
        sub_cloud.connect(cloud_socket_path)
        poller.register(sub_cloud, zmq.POLLIN)

        logger.info(
            "Cloud bridge connected to %s",
            cloud_socket_path,
        )

    # ------------------------------------------------------------------
    # OTA socket
    # ------------------------------------------------------------------

    sub_ota: zmq.asyncio.Socket | None = None

    if ota_socket_path:
        sub_ota = zmq_ctx.socket(zmq.SUB)
        sub_ota.setsockopt_string(zmq.SUBSCRIBE, TOPIC_OTA)
        sub_ota.connect(ota_socket_path)
        poller.register(sub_ota, zmq.POLLIN)

        logger.info(
            "OTA bridge connected to %s",
            ota_socket_path,
        )

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    try:
        while True:

            socks: dict = dict(
                await poller.poll()
            )

            for sock in (
                sub_telemetry,
                sub_cloud,
                sub_ota,
            ):

                if sock is None:
                    continue

                if sock not in socks:
                    continue

                parts: list[bytes] = await sock.recv_multipart()

                if len(parts) < 2:
                    logger.warning(
                        "Received malformed ZMQ message with %d parts",
                        len(parts),
                    )
                    continue

                # ------------------------------------------------------
                # IMPORTANT:
                #
                # The actual ZMQ topic is parts[0].
                # Do NOT assume envelope["topic"] exists because
                # system.health messages don't contain that key.
                # ------------------------------------------------------

                topic = parts[0].decode(
                    "utf-8",
                    errors="replace",
                )

                try:
                    envelope: dict = decode_telemetry(parts[1])
                except Exception:
                    logger.exception(
                        "Failed to decode telemetry message: topic=%s",
                        topic,
                    )
                    continue

                # ------------------------------------------------------
                # Normal telemetry:
                #
                # envelope["payload"]
                #
                # Health/event messages:
                #
                # envelope["data"]
                # ------------------------------------------------------

                if "payload" in envelope:
                    data = envelope["payload"]

                elif "data" in envelope:
                    data = envelope["data"]

                else:
                    data = {}

                    logger.warning(
                        "Telemetry message has neither payload nor data: "
                        "topic=%s envelope=%s",
                        topic,
                        envelope,
                    )

                # ------------------------------------------------------
                # BMS debug logging
                # ------------------------------------------------------

                if topic.startswith(TOPIC_BMS_RACK):
                    logger.warning(
                        "[WS BMS] topic=%s data=%s",
                        topic,
                        data,
                    )

                # ------------------------------------------------------
                # Message sent to browser
                # ------------------------------------------------------

                message: dict = {
                    "topic": topic,
                    "data": data,
                    "ts": envelope.get("ts", 0),
                }

                client_manager.broadcast(message)

    except asyncio.CancelledError:
        logger.info("Telemetry bridge cancelled")

    except Exception:
        logger.exception(
            "Telemetry bridge stopped because of an unexpected error"
        )
        raise

    finally:
        sub_telemetry.close()

        if sub_cloud is not None:
            sub_cloud.close()

        if sub_ota is not None:
            sub_ota.close()


@router.websocket("/ws/telemetry")
async def websocket_endpoint(
    websocket: WebSocket,
) -> None:
    """WebSocket endpoint for streaming telemetry to browser clients."""

    await websocket.accept()

    client_manager: ClientManager = (
        websocket.app.state.client_manager
    )

    queue: asyncio.Queue = (
        client_manager.add_client()
    )

    try:
        while True:
            message: dict = await queue.get()

            await websocket.send_json(message)

    except WebSocketDisconnect:
        pass

    except Exception:
        logger.exception(
            "WebSocket connection error"
        )

    finally:
        client_manager.remove_client(queue)
