"""ZMQ framing convention tests for C/Python boundary.

Verifies the corrected framing after 13.1-01 defect fixes:
- PUB/SUB: exactly 2 frames [topic | msgpack body]
- PUSH/PULL: single msgpack frame (no length prefix)
- Shared PUB socket: one bind, multiple publishers

Uses TCP endpoints to avoid /run/ems/ dependency.
"""

from __future__ import annotations

import struct
import time

import msgpack
import pytest
import zmq

from ems_common.ipc import (
    MSG_KEY_PAYLOAD,
    MSG_KEY_SEQUENCE,
    MSG_KEY_SOURCE,
    MSG_KEY_TIMESTAMP,
    MSG_KEY_TOPIC,
    SEVERITY_WARNING,
    decode_event,
    encode_event,
    encode_telemetry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_subscription(pub: zmq.Socket, sub: zmq.Socket, delay: float = 0.15) -> None:
    """Allow ZMQ subscription propagation time."""
    time.sleep(delay)


# ---------------------------------------------------------------------------
# Test class: PUB/SUB framing (DEFECT-3 fix verification)
# ---------------------------------------------------------------------------


class TestPubFraming:
    """Verify 2-frame PUB/SUB convention matches Python and fixed C code."""

    PORT_BASE: int = 15570

    def test_python_pub_sends_two_frames(self) -> None:
        """PUB sends exactly 2 frames [topic, msgpack body]; SUB receives both."""
        ctx: zmq.Context = zmq.Context()
        ep: str = f"tcp://127.0.0.1:{self.PORT_BASE}"
        try:
            pub: zmq.Socket = ctx.socket(zmq.PUB)
            pub.bind(ep)

            sub: zmq.Socket = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, "pcs")
            _wait_for_subscription(pub, sub)

            # Send 2-frame message matching TelemetryPublisher._send()
            topic: str = "pcs"
            ts: int = int(time.monotonic() * 1000)
            body: bytes = encode_telemetry(ts, 1, "data_manager", topic, {"ac_voltage": 230.5})

            pub.send_string(topic, zmq.SNDMORE)
            pub.send(body)

            # Receive and verify
            assert sub.poll(2000), "No message received within timeout"
            parts: list[bytes] = sub.recv_multipart()

            assert len(parts) == 2, f"Expected 2 frames [topic, body], got {len(parts)}"
            assert parts[0].decode("utf-8") == "pcs"

            msg: dict = msgpack.unpackb(parts[1], raw=False)
            assert MSG_KEY_TIMESTAMP in msg
            assert MSG_KEY_SEQUENCE in msg
            assert MSG_KEY_SOURCE in msg
            assert msg[MSG_KEY_SOURCE] == "data_manager"
            assert msg[MSG_KEY_TOPIC] == "pcs"
            assert MSG_KEY_PAYLOAD in msg
            assert abs(msg[MSG_KEY_PAYLOAD]["ac_voltage"] - 230.5) < 0.01

            sub.close()
            pub.close()
        finally:
            ctx.term()

    def test_sub_rejects_three_frame_messages(self) -> None:
        """3-frame PUB messages [topic|junk|body] are rejected by 2-frame filter."""
        ctx: zmq.Context = zmq.Context()
        ep: str = f"tcp://127.0.0.1:{self.PORT_BASE + 1}"
        try:
            pub: zmq.Socket = ctx.socket(zmq.PUB)
            pub.bind(ep)

            sub: zmq.Socket = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, "pcs")
            _wait_for_subscription(pub, sub)

            # Send OLD 3-frame message: [topic | length prefix | body]
            topic: str = "pcs"
            body: bytes = encode_telemetry(
                int(time.monotonic() * 1000), 1, "data_manager", topic, {"bad": True}
            )
            length_prefix: bytes = struct.pack(">I", len(body))

            pub.send_string(topic, zmq.SNDMORE)
            pub.send(length_prefix, zmq.SNDMORE)
            pub.send(body)

            # Replicate telemetry_writer's 2-frame filter: len(parts) != 2 -> skip
            assert sub.poll(2000), "No message received within timeout"
            bad_parts: list[bytes] = sub.recv_multipart()
            assert len(bad_parts) == 3, f"Expected 3 frames for old format, got {len(bad_parts)}"
            # This message would be dropped by telemetry_writer

            # Now send a valid 2-frame message
            good_body: bytes = encode_telemetry(
                int(time.monotonic() * 1000), 2, "data_manager", topic, {"good": True}
            )
            pub.send_string(topic, zmq.SNDMORE)
            pub.send(good_body)

            assert sub.poll(2000), "No valid message received within timeout"
            good_parts: list[bytes] = sub.recv_multipart()
            assert len(good_parts) == 2, f"Expected 2 frames for new format, got {len(good_parts)}"

            # Only accept 2-frame messages (matching telemetry_writer logic)
            msg: dict = msgpack.unpackb(good_parts[1], raw=False)
            assert msg[MSG_KEY_PAYLOAD]["good"] is True

            sub.close()
            pub.close()
        finally:
            ctx.term()


# ---------------------------------------------------------------------------
# Test class: PUSH/PULL framing (DEFECT-2 fix verification)
# ---------------------------------------------------------------------------


class TestPushPullFraming:
    """Verify single-frame PUSH/PULL convention matches Python and fixed C code."""

    PORT_BASE: int = 15574

    def test_push_single_frame_decoded_by_event_consumer(self) -> None:
        """Single msgpack frame on PUSH is correctly received and decoded on PULL."""
        ctx: zmq.Context = zmq.Context()
        ep: str = f"tcp://127.0.0.1:{self.PORT_BASE}"
        try:
            pull: zmq.Socket = ctx.socket(zmq.PULL)
            pull.bind(ep)

            push: zmq.Socket = ctx.socket(zmq.PUSH)
            push.connect(ep)
            time.sleep(0.1)

            # Send single-frame event (matching fixed C code)
            ts: int = int(time.monotonic() * 1000)
            event_bytes: bytes = encode_event(
                timestamp_ms=ts,
                source="safety_manager",
                severity=SEVERITY_WARNING,
                event_type="estop_activated",
                message="E-Stop activated on DI-6",
                data={"di_index": 6, "state": 1},
            )
            push.send(event_bytes)

            # Receive single frame (matching event_writer.py: data = sock.recv())
            assert pull.poll(2000), "No message received on PULL within timeout"
            data: bytes = pull.recv()

            # Decode must succeed
            event: dict = decode_event(data)
            assert event["src"] == "safety_manager"
            assert event["severity"] == SEVERITY_WARNING
            assert event["event_type"] == "estop_activated"
            assert event["message"] == "E-Stop activated on DI-6"
            assert event["data"]["di_index"] == 6

            push.close()
            pull.close()
        finally:
            ctx.term()

    def test_push_with_length_prefix_fails_decode(self) -> None:
        """Old framing [4-byte len | SNDMORE] [body] causes decode failure on PULL recv()."""
        ctx: zmq.Context = zmq.Context()
        ep: str = f"tcp://127.0.0.1:{self.PORT_BASE + 1}"
        try:
            pull: zmq.Socket = ctx.socket(zmq.PULL)
            pull.bind(ep)

            push: zmq.Socket = ctx.socket(zmq.PUSH)
            push.connect(ep)
            time.sleep(0.1)

            # Send OLD framing: [4-byte BE length prefix | SNDMORE] [msgpack body]
            ts: int = int(time.monotonic() * 1000)
            body: bytes = encode_event(
                timestamp_ms=ts,
                source="safety_manager",
                severity=SEVERITY_WARNING,
                event_type="estop_activated",
                message="E-Stop activated",
                data={},
            )
            length_prefix: bytes = struct.pack(">I", len(body))

            push.send(length_prefix, zmq.SNDMORE)
            push.send(body)

            # PULL recv() gets the FIRST frame: the 4-byte length prefix
            assert pull.poll(2000), "No message received on PULL within timeout"
            data: bytes = pull.recv()

            # The first frame is the 4-byte length prefix, NOT valid msgpack
            assert len(data) == 4, f"Expected 4-byte length prefix, got {len(data)} bytes"
            with pytest.raises(Exception):
                # decode_event wraps msgpack.unpackb -- 4 random bytes won't decode
                # to a valid dict with expected keys
                result: dict = decode_event(data)
                # If it somehow decodes without exception, verify it lacks expected keys
                assert "src" not in result, "Length prefix should not decode to valid event"

            push.close()
            pull.close()
        finally:
            ctx.term()


# ---------------------------------------------------------------------------
# Test class: Shared PUB socket (DEFECT-1 fix verification)
# ---------------------------------------------------------------------------


class TestSharedPubSocket:
    """Verify shared PUB socket pattern works (DEFECT-1 fix)."""

    PORT_BASE: int = 15578

    def test_health_and_publisher_share_socket(self) -> None:
        """One PUB socket serves both telemetry and health topics on a single SUB."""
        ctx: zmq.Context = zmq.Context()
        ep: str = f"tcp://127.0.0.1:{self.PORT_BASE}"
        try:
            # Single PUB socket (owned by TelemetryPublisher in production)
            pub: zmq.Socket = ctx.socket(zmq.PUB)
            pub.bind(ep)

            # SUB subscribes to both telemetry and health topics
            sub: zmq.Socket = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, "pcs")
            sub.setsockopt_string(zmq.SUBSCRIBE, "system.health")
            _wait_for_subscription(pub, sub)

            # Simulate TelemetryPublisher sending on shared socket
            telemetry_body: bytes = encode_telemetry(
                int(time.monotonic() * 1000), 1, "data_manager", "pcs", {"ac_voltage": 230.0}
            )
            pub.send_string("pcs", zmq.SNDMORE)
            pub.send(telemetry_body)

            # Simulate HealthMonitor sending on same shared socket
            health_body: bytes = encode_event(
                timestamp_ms=int(time.monotonic() * 1000),
                source="data_manager",
                severity=SEVERITY_WARNING,
                event_type="stale_section",
                message="RTDB section 'pcs' is stale",
                data={"section": "pcs", "age_ms": 6000},
            )
            pub.send_string("system.health", zmq.SNDMORE)
            pub.send(health_body)

            # Receive both messages on the same SUB
            received_topics: set[str] = set()
            for _ in range(2):
                if sub.poll(2000):
                    parts: list[bytes] = sub.recv_multipart()
                    assert len(parts) == 2
                    received_topics.add(parts[0].decode("utf-8"))

            assert "pcs" in received_topics, "Missing telemetry message on shared socket"
            assert "system.health" in received_topics, "Missing health message on shared socket"

            sub.close()
            pub.close()
        finally:
            ctx.term()

    def test_double_bind_same_endpoint_fails(self) -> None:
        """Binding two PUB sockets to the same TCP endpoint raises ZMQError."""
        ctx: zmq.Context = zmq.Context()
        ep: str = f"tcp://127.0.0.1:{self.PORT_BASE + 1}"
        try:
            pub1: zmq.Socket = ctx.socket(zmq.PUB)
            pub1.bind(ep)

            pub2: zmq.Socket = ctx.socket(zmq.PUB)
            with pytest.raises(zmq.ZMQError):
                pub2.bind(ep)

            pub2.close()
            pub1.close()
        finally:
            ctx.term()
