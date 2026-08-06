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
    the oldest message is dropped (get_nowait) before adding the new one.
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
        """Send a message to all connected clients.

        If a client's queue is full, the oldest message is dropped
        to make room for the new one (slow-client protection).
        """
        for queue in self.clients:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Drop oldest message, then add newest
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
    """Background task: subscribe to ZMQ PUB sources, broadcast to clients.

    Polls up to 3 ZMQ SUB sockets via zmq.asyncio.Poller:
      - telemetry socket: all 6 system telemetry topics
      - cloud socket (optional): TOPIC_CLOUD messages from cloud_manager
      - ota socket (optional): TOPIC_OTA messages from ota_manager

    Cloud and OTA sockets are only created when their paths are non-empty,
    preserving backward compatibility for tests that only provide telemetry.

    Args:
        zmq_ctx: Async ZMQ context for creating the SUB sockets.
        client_manager: ClientManager to broadcast decoded messages to.
        socket_path: ZMQ endpoint for the main telemetry SUB socket.
        cloud_socket_path: ZMQ endpoint for cloud status PUB. Empty = disabled.
        ota_socket_path: ZMQ endpoint for OTA status PUB. Empty = disabled.
    """
    poller: zmq.asyncio.Poller = zmq.asyncio.Poller()

    # Telemetry socket — always created
    sub_telemetry: zmq.asyncio.Socket = zmq_ctx.socket(zmq.SUB)
    for topic in _TELEMETRY_TOPICS:
        sub_telemetry.setsockopt_string(zmq.SUBSCRIBE, topic)
    sub_telemetry.connect(socket_path)
    poller.register(sub_telemetry, zmq.POLLIN)
    logger.info("Telemetry bridge connected to %s", socket_path)

    # Cloud socket — created only when path is provided
    sub_cloud: zmq.asyncio.Socket | None = None
    if cloud_socket_path:
        sub_cloud = zmq_ctx.socket(zmq.SUB)
        sub_cloud.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CLOUD)
        sub_cloud.connect(cloud_socket_path)
        poller.register(sub_cloud, zmq.POLLIN)
        logger.info("Cloud bridge connected to %s", cloud_socket_path)

    # OTA socket — created only when path is provided
    sub_ota: zmq.asyncio.Socket | None = None
    if ota_socket_path:
        sub_ota = zmq_ctx.socket(zmq.SUB)
        sub_ota.setsockopt_string(zmq.SUBSCRIBE, TOPIC_OTA)
        sub_ota.connect(ota_socket_path)
        poller.register(sub_ota, zmq.POLLIN)
        logger.info("OTA bridge connected to %s", ota_socket_path)

    try:
        while True:
            socks: dict = dict(await poller.poll())

            for sock in (sub_telemetry, sub_cloud, sub_ota):
                if sock is None:
                    continue
                if sock not in socks:
                    continue

                parts: list[bytes] = await sock.recv_multipart()
                if len(parts) < 2:
                    continue

                envelope: dict = decode_telemetry(parts[1])
                message: dict = {
                    "topic": envelope["topic"],
                    "data": envelope["payload"],
                    "ts": envelope["ts"],
                }
                client_manager.broadcast(message)

    except asyncio.CancelledError:
        logger.info("Telemetry bridge cancelled")
    finally:
        sub_telemetry.close()
        if sub_cloud is not None:
            sub_cloud.close()
        if sub_ota is not None:
            sub_ota.close()


@router.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for streaming telemetry to browser clients."""
    await websocket.accept()

    client_manager: ClientManager = websocket.app.state.client_manager
    queue: asyncio.Queue = client_manager.add_client()

    try:
        while True:
            message: dict = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        client_manager.remove_client(queue)
