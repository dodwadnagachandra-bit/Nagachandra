"""Tests for WebSocket telemetry bridge."""

from __future__ import annotations

import asyncio
import time

import pytest
import zmq
import zmq.asyncio

from ems_common.ipc import encode_telemetry
from ems_hmi_server.ws import ClientManager, telemetry_bridge


class TestClientManager:
    """Unit tests for ClientManager."""

    def test_add_client_returns_queue(self) -> None:
        """add_client returns an asyncio.Queue with maxsize=100."""
        cm = ClientManager()
        queue = cm.add_client()
        assert isinstance(queue, asyncio.Queue)
        assert queue.maxsize == 100

    def test_remove_client(self) -> None:
        """remove_client removes queue from client set."""
        cm = ClientManager()
        queue = cm.add_client()
        assert len(cm.clients) == 1
        cm.remove_client(queue)
        assert len(cm.clients) == 0

    def test_broadcast_to_multiple_clients(self) -> None:
        """broadcast sends message to all client queues."""
        cm = ClientManager()
        q1 = cm.add_client()
        q2 = cm.add_client()
        msg = {"topic": "pcs", "data": {"power": 100}, "ts": 123}
        cm.broadcast(msg)
        assert q1.get_nowait() == msg
        assert q2.get_nowait() == msg

    def test_broadcast_drops_oldest_on_overflow(self) -> None:
        """When queue is full, oldest message is dropped and newest added."""
        cm = ClientManager()
        queue = cm.add_client()
        # Fill the queue
        for i in range(100):
            cm.broadcast({"topic": "pcs", "data": {}, "ts": i})
        assert queue.qsize() == 100
        # One more should drop oldest (ts=0) and add newest (ts=100)
        cm.broadcast({"topic": "pcs", "data": {}, "ts": 100})
        assert queue.qsize() == 100
        # First item should now be ts=1 (0 was dropped)
        first = queue.get_nowait()
        assert first["ts"] == 1

    def test_remove_nonexistent_client_no_error(self) -> None:
        """remove_client with unknown queue doesn't raise."""
        cm = ClientManager()
        queue = asyncio.Queue()
        cm.remove_client(queue)  # Should not raise


class TestTelemetryBridge:
    """Integration tests for the telemetry_bridge function with real ZMQ."""

    async def test_bridge_receives_and_broadcasts(self) -> None:
        """Bridge receives ZMQ message and broadcasts to client queue."""
        ctx = zmq.asyncio.Context()
        pub = ctx.socket(zmq.PUB)
        port: int = pub.bind_to_random_port("tcp://127.0.0.1")
        address: str = f"tcp://127.0.0.1:{port}"

        cm = ClientManager()
        queue = cm.add_client()

        task = asyncio.create_task(telemetry_bridge(ctx, cm, address))
        await asyncio.sleep(0.1)

        ts: int = int(time.time() * 1000)
        envelope: bytes = encode_telemetry(ts, 1, "test", "pcs", {"active_power": 42.5})
        pub.send_string("pcs", zmq.SNDMORE)
        pub.send(envelope)

        msg: dict = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert msg["topic"] == "pcs"
        assert msg["data"]["active_power"] == 42.5
        assert msg["ts"] == ts

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        pub.close()
        ctx.term()

    async def test_message_format_keys(self) -> None:
        """Message has exactly {topic, data, ts} keys."""
        ctx = zmq.asyncio.Context()
        pub = ctx.socket(zmq.PUB)
        port = pub.bind_to_random_port("tcp://127.0.0.1")
        address = f"tcp://127.0.0.1:{port}"

        cm = ClientManager()
        queue = cm.add_client()
        task = asyncio.create_task(telemetry_bridge(ctx, cm, address))
        await asyncio.sleep(0.1)

        ts = int(time.time() * 1000)
        envelope = encode_telemetry(ts, 1, "test", "system", {"total_soc": 85.0})
        pub.send_string("system", zmq.SNDMORE)
        pub.send(envelope)

        msg = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert set(msg.keys()) == {"topic", "data", "ts"}

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        pub.close()
        ctx.term()

    async def test_two_clients_receive_same_message(self) -> None:
        """Two client queues both receive the same broadcast."""
        ctx = zmq.asyncio.Context()
        pub = ctx.socket(zmq.PUB)
        port = pub.bind_to_random_port("tcp://127.0.0.1")
        address = f"tcp://127.0.0.1:{port}"

        cm = ClientManager()
        q1 = cm.add_client()
        q2 = cm.add_client()
        task = asyncio.create_task(telemetry_bridge(ctx, cm, address))
        await asyncio.sleep(0.1)

        ts = int(time.time() * 1000)
        envelope = encode_telemetry(ts, 1, "test", "meter", {"voltage": 230.0})
        pub.send_string("meter", zmq.SNDMORE)
        pub.send(envelope)

        msg1 = await asyncio.wait_for(q1.get(), timeout=2.0)
        msg2 = await asyncio.wait_for(q2.get(), timeout=2.0)
        assert msg1["topic"] == "meter"
        assert msg2["topic"] == "meter"
        assert msg1["data"] == msg2["data"]

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        pub.close()
        ctx.term()

    async def test_client_disconnect_does_not_crash_bridge(self) -> None:
        """After removing a client, bridge continues for remaining clients."""
        ctx = zmq.asyncio.Context()
        pub = ctx.socket(zmq.PUB)
        port = pub.bind_to_random_port("tcp://127.0.0.1")
        address = f"tcp://127.0.0.1:{port}"

        cm = ClientManager()
        q1 = cm.add_client()
        q2 = cm.add_client()
        task = asyncio.create_task(telemetry_bridge(ctx, cm, address))
        await asyncio.sleep(0.1)

        # Remove first client (simulates disconnect)
        cm.remove_client(q1)

        ts = int(time.time() * 1000)
        envelope = encode_telemetry(ts, 1, "test", "btms", {"inlet_temp": 25.0})
        pub.send_string("btms", zmq.SNDMORE)
        pub.send(envelope)

        msg = await asyncio.wait_for(q2.get(), timeout=2.0)
        assert msg["topic"] == "btms"
        assert q1.empty()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        pub.close()
        ctx.term()

    async def test_subscribes_to_all_topics(self) -> None:
        """Bridge receives messages on all 6 telemetry topic prefixes."""
        ctx = zmq.asyncio.Context()
        pub = ctx.socket(zmq.PUB)
        port = pub.bind_to_random_port("tcp://127.0.0.1")
        address = f"tcp://127.0.0.1:{port}"

        cm = ClientManager()
        queue = cm.add_client()
        task = asyncio.create_task(telemetry_bridge(ctx, cm, address))
        await asyncio.sleep(0.1)

        topics = ["bms.rack.0.0", "pcs", "gpio", "meter", "btms", "system"]
        ts = int(time.time() * 1000)
        for i, topic in enumerate(topics):
            envelope = encode_telemetry(ts, i + 1, "test", topic, {"test": True})
            pub.send_string(topic, zmq.SNDMORE)
            pub.send(envelope)

        received_topics: set[str] = set()
        for _ in range(len(topics)):
            msg = await asyncio.wait_for(queue.get(), timeout=2.0)
            received_topics.add(msg["topic"])

        for topic in topics:
            assert topic in received_topics, f"Missing topic: {topic}"

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        pub.close()
        ctx.term()

    async def test_bridge_cancellation_closes_socket(self) -> None:
        """Cancelling the bridge task cleanly closes the ZMQ socket."""
        ctx = zmq.asyncio.Context()
        pub = ctx.socket(zmq.PUB)
        port = pub.bind_to_random_port("tcp://127.0.0.1")
        address = f"tcp://127.0.0.1:{port}"

        cm = ClientManager()
        task = asyncio.create_task(telemetry_bridge(ctx, cm, address))
        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Context should terminate without hanging (socket was closed)
        pub.close()
        ctx.term()


class TestMultiSourceBridge:
    """Integration tests for multi-source ZMQ poller bridge (telemetry + cloud + ota)."""

    async def test_bridge_receives_cloud_topic(self) -> None:
        """Bridge receives cloud topic from separate cloud socket and broadcasts it."""
        ctx = zmq.asyncio.Context()

        # Telemetry socket (required, but silent in this test)
        tel_pub = ctx.socket(zmq.PUB)
        tel_port: int = tel_pub.bind_to_random_port("tcp://127.0.0.1")
        tel_addr: str = f"tcp://127.0.0.1:{tel_port}"

        # Cloud socket
        cloud_pub = ctx.socket(zmq.PUB)
        cloud_port: int = cloud_pub.bind_to_random_port("tcp://127.0.0.1")
        cloud_addr: str = f"tcp://127.0.0.1:{cloud_port}"

        cm = ClientManager()
        queue = cm.add_client()

        task = asyncio.create_task(
            telemetry_bridge(ctx, cm, tel_addr, cloud_socket_path=cloud_addr)
        )
        await asyncio.sleep(0.1)

        ts: int = int(time.time() * 1000)
        envelope: bytes = encode_telemetry(ts, 1, "cloud_manager", "cloud", {"state": "connected", "broker": "test"})
        cloud_pub.send_string("cloud", zmq.SNDMORE)
        cloud_pub.send(envelope)

        msg: dict = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert msg["topic"] == "cloud"
        assert msg["data"]["state"] == "connected"
        assert msg["ts"] == ts

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        tel_pub.close()
        cloud_pub.close()
        ctx.term()

    async def test_bridge_receives_ota_topic(self) -> None:
        """Bridge receives ota topic from separate ota socket and broadcasts it."""
        ctx = zmq.asyncio.Context()

        tel_pub = ctx.socket(zmq.PUB)
        tel_port: int = tel_pub.bind_to_random_port("tcp://127.0.0.1")
        tel_addr: str = f"tcp://127.0.0.1:{tel_port}"

        ota_pub = ctx.socket(zmq.PUB)
        ota_port: int = ota_pub.bind_to_random_port("tcp://127.0.0.1")
        ota_addr: str = f"tcp://127.0.0.1:{ota_port}"

        cm = ClientManager()
        queue = cm.add_client()

        task = asyncio.create_task(
            telemetry_bridge(ctx, cm, tel_addr, ota_socket_path=ota_addr)
        )
        await asyncio.sleep(0.1)

        ts: int = int(time.time() * 1000)
        envelope: bytes = encode_telemetry(ts, 1, "ota_manager", "ota", {"state": "idle", "version": "1.0.0"})
        ota_pub.send_string("ota", zmq.SNDMORE)
        ota_pub.send(envelope)

        msg: dict = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert msg["topic"] == "ota"
        assert msg["data"]["state"] == "idle"
        assert msg["ts"] == ts

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        tel_pub.close()
        ota_pub.close()
        ctx.term()

    async def test_bridge_receives_all_sources(self) -> None:
        """Bridge receives messages from all 3 sources: telemetry, cloud, ota."""
        ctx = zmq.asyncio.Context()

        tel_pub = ctx.socket(zmq.PUB)
        tel_port: int = tel_pub.bind_to_random_port("tcp://127.0.0.1")
        tel_addr: str = f"tcp://127.0.0.1:{tel_port}"

        cloud_pub = ctx.socket(zmq.PUB)
        cloud_port: int = cloud_pub.bind_to_random_port("tcp://127.0.0.1")
        cloud_addr: str = f"tcp://127.0.0.1:{cloud_port}"

        ota_pub = ctx.socket(zmq.PUB)
        ota_port: int = ota_pub.bind_to_random_port("tcp://127.0.0.1")
        ota_addr: str = f"tcp://127.0.0.1:{ota_port}"

        cm = ClientManager()
        queue = cm.add_client()

        task = asyncio.create_task(
            telemetry_bridge(
                ctx, cm, tel_addr,
                cloud_socket_path=cloud_addr,
                ota_socket_path=ota_addr,
            )
        )
        await asyncio.sleep(0.1)

        ts: int = int(time.time() * 1000)

        # Send on telemetry socket
        tel_envelope: bytes = encode_telemetry(ts, 1, "data_manager", "pcs", {"active_power": 10.0})
        tel_pub.send_string("pcs", zmq.SNDMORE)
        tel_pub.send(tel_envelope)

        # Send on cloud socket
        cloud_envelope: bytes = encode_telemetry(ts, 2, "cloud_manager", "cloud", {"state": "connected"})
        cloud_pub.send_string("cloud", zmq.SNDMORE)
        cloud_pub.send(cloud_envelope)

        # Send on ota socket
        ota_envelope: bytes = encode_telemetry(ts, 3, "ota_manager", "ota", {"state": "idle"})
        ota_pub.send_string("ota", zmq.SNDMORE)
        ota_pub.send(ota_envelope)

        received_topics: set[str] = set()
        for _ in range(3):
            msg = await asyncio.wait_for(queue.get(), timeout=2.0)
            received_topics.add(msg["topic"])

        assert "pcs" in received_topics
        assert "cloud" in received_topics
        assert "ota" in received_topics

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        tel_pub.close()
        cloud_pub.close()
        ota_pub.close()
        ctx.term()

    async def test_bridge_cancellation_closes_all_sockets(self) -> None:
        """Cancelling the bridge closes all 3 SUB sockets cleanly."""
        ctx = zmq.asyncio.Context()

        tel_pub = ctx.socket(zmq.PUB)
        tel_port: int = tel_pub.bind_to_random_port("tcp://127.0.0.1")
        tel_addr: str = f"tcp://127.0.0.1:{tel_port}"

        cloud_pub = ctx.socket(zmq.PUB)
        cloud_port: int = cloud_pub.bind_to_random_port("tcp://127.0.0.1")
        cloud_addr: str = f"tcp://127.0.0.1:{cloud_port}"

        ota_pub = ctx.socket(zmq.PUB)
        ota_port: int = ota_pub.bind_to_random_port("tcp://127.0.0.1")
        ota_addr: str = f"tcp://127.0.0.1:{ota_port}"

        cm = ClientManager()
        task = asyncio.create_task(
            telemetry_bridge(
                ctx, cm, tel_addr,
                cloud_socket_path=cloud_addr,
                ota_socket_path=ota_addr,
            )
        )
        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Context should terminate without hanging (all sockets closed)
        tel_pub.close()
        cloud_pub.close()
        ota_pub.close()
        ctx.term()


class TestWebSocketRoute:
    """Verify WebSocket endpoint is registered in the app."""

    def test_ws_route_registered(self, test_app) -> None:
        """The /ws/telemetry route is registered in the app."""
        routes = [r.path for r in test_app.routes]
        assert "/ws/telemetry" in routes
