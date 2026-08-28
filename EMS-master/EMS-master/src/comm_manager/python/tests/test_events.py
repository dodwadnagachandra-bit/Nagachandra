"""Tests for comm event publisher functions."""

from __future__ import annotations

import time

import msgpack
import pytest
import zmq

from ems_comm_manager.events import (
    publish_comm_exception,
    publish_comm_fault,
    publish_comm_recovery,
)


@pytest.fixture()
def zmq_push_pull() -> tuple[zmq.Socket, zmq.Socket]:
    """Create an in-process PUSH/PULL pair for testing."""
    ctx = zmq.Context()
    pull = ctx.socket(zmq.PULL)
    push = ctx.socket(zmq.PUSH)
    addr = "inproc://test-events"
    pull.bind(addr)
    push.connect(addr)
    # Small wait for connect
    time.sleep(0.01)
    yield push, pull
    push.close(linger=0)
    pull.close(linger=0)
    ctx.term()


class TestPublishCommFault:
    """publish_comm_fault sends correct MessagePack envelope."""

    def test_fault_event_payload(self, zmq_push_pull: tuple[zmq.Socket, zmq.Socket]) -> None:
        push, pull = zmq_push_pull
        publish_comm_fault(
            push_socket=push,
            device_id="pcs",
            device_address=1,
            port="/dev/ttyRS485-1",
            fault_type="timeout",
            last_seen_ms=5000,
            consecutive_failures=3,
        )
        raw = pull.recv(zmq.NOBLOCK)
        msg = msgpack.unpackb(raw, raw=False)
        assert msg["severity"] == "error"
        assert msg["event_type"] == "comm_fault"
        assert msg["src"] == "comm_manager"
        data = msg["data"]
        assert data["device_id"] == "pcs"
        assert data["device_address"] == 1
        assert data["port"] == "/dev/ttyRS485-1"
        assert data["fault_type"] == "timeout"
        assert data["last_seen_ms"] == 5000
        assert data["consecutive_failures"] == 3


class TestPublishCommRecovery:
    """publish_comm_recovery includes offline_duration_ms."""

    def test_recovery_event_payload(self, zmq_push_pull: tuple[zmq.Socket, zmq.Socket]) -> None:
        push, pull = zmq_push_pull
        publish_comm_recovery(
            push_socket=push,
            device_id="meter",
            device_address=2,
            port="/dev/ttyRS485-2",
            offline_duration_ms=15000,
        )
        raw = pull.recv(zmq.NOBLOCK)
        msg = msgpack.unpackb(raw, raw=False)
        assert msg["severity"] == "info"
        assert msg["event_type"] == "comm_recovery"
        data = msg["data"]
        assert data["device_id"] == "meter"
        assert data["offline_duration_ms"] == 15000


class TestPublishCommException:
    """publish_comm_exception with severity based on exception code."""

    def test_exception_code_01_warning(self, zmq_push_pull: tuple[zmq.Socket, zmq.Socket]) -> None:
        push, pull = zmq_push_pull
        publish_comm_exception(
            push_socket=push,
            device_id="btms",
            device_address=3,
            port="/dev/ttyRS485-3",
            exception_code=1,
            message="Illegal function",
        )
        raw = pull.recv(zmq.NOBLOCK)
        msg = msgpack.unpackb(raw, raw=False)
        assert msg["severity"] == "warning"
        assert msg["data"]["exception_code"] == 1

    def test_exception_code_04_error(self, zmq_push_pull: tuple[zmq.Socket, zmq.Socket]) -> None:
        push, pull = zmq_push_pull
        publish_comm_exception(
            push_socket=push,
            device_id="pcs",
            device_address=1,
            port="/dev/ttyRS485-1",
            exception_code=4,
            message="Device Failure",
        )
        raw = pull.recv(zmq.NOBLOCK)
        msg = msgpack.unpackb(raw, raw=False)
        assert msg["severity"] == "error"
        assert msg["data"]["exception_code"] == 4

    def test_exception_has_timestamp(self, zmq_push_pull: tuple[zmq.Socket, zmq.Socket]) -> None:
        push, pull = zmq_push_pull
        publish_comm_exception(
            push_socket=push,
            device_id="pcs",
            device_address=1,
            port="/dev/ttyRS485-1",
            exception_code=2,
            message="Illegal data address",
        )
        raw = pull.recv(zmq.NOBLOCK)
        msg = msgpack.unpackb(raw, raw=False)
        assert "ts" in msg
        assert isinstance(msg["ts"], int)
