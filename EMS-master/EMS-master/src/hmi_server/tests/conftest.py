"""Shared test fixtures for HMI server tests."""

from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator, Callable, Generator

import bcrypt
import pytest
import zmq
import zmq.asyncio
from httpx import ASGITransport, AsyncClient

from ems_common.ipc import encode_telemetry
from ems_hmi_server.app import create_app

# Pre-compute bcrypt hashes for test PINs
_OPERATOR_HASH: str = bcrypt.hashpw(b"1234", bcrypt.gensalt()).decode("utf-8")
_ADMIN_HASH: str = bcrypt.hashpw(b"5678", bcrypt.gensalt()).decode("utf-8")


@pytest.fixture
def test_config() -> dict:
    """Return a test HMI config dict with known bcrypt hashes."""
    return {
        "server": {
            "http_port": 8080,
            "host": "0.0.0.0",
        },
        "auth": {
            "operator_pin_hash": _OPERATOR_HASH,
            "admin_pin_hash": _ADMIN_HASH,
            "session_timeout_s": 1800,
        },
        "display": {
            "refresh_interval_ms": 1000,
            "kiosk_mode": False,
            "screen_size": "10inch",
        },
    }


@pytest.fixture
def test_app(test_config: dict):
    """Create a FastAPI test application instance."""
    return create_app(test_config)


@pytest.fixture
async def async_client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client bound to the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def mock_zmq_pub() -> Generator[tuple[str, Callable], None, None]:
    """Create a ZMQ PUB socket on tcp://127.0.0.1 with random port.

    Yields (address, publish_fn) where publish_fn(topic, payload) sends
    a properly formatted multipart telemetry message.
    """
    ctx: zmq.Context = zmq.Context()
    pub: zmq.Socket = ctx.socket(zmq.PUB)
    port: int = pub.bind_to_random_port("tcp://127.0.0.1")
    address: str = f"tcp://127.0.0.1:{port}"

    seq: int = 0

    def publish(topic: str, payload: dict) -> None:
        nonlocal seq
        seq += 1
        ts: int = int(time.time() * 1000)
        envelope: bytes = encode_telemetry(ts, seq, "test", topic, payload)
        pub.send_string(topic, zmq.SNDMORE)
        pub.send(envelope)

    yield address, publish

    pub.close()
    ctx.term()


@pytest.fixture
def mock_zmq_cloud_pub() -> Generator[tuple[str, Callable], None, None]:
    """Create a ZMQ PUB socket simulating cloud_manager cloud status output.

    Yields (address, publish_fn) where publish_fn(topic, payload) sends
    a properly formatted multipart telemetry message.
    """
    ctx: zmq.Context = zmq.Context()
    pub: zmq.Socket = ctx.socket(zmq.PUB)
    port: int = pub.bind_to_random_port("tcp://127.0.0.1")
    address: str = f"tcp://127.0.0.1:{port}"

    seq: int = 0

    def publish(topic: str, payload: dict) -> None:
        nonlocal seq
        seq += 1
        ts: int = int(time.time() * 1000)
        envelope: bytes = encode_telemetry(ts, seq, "cloud_manager", topic, payload)
        pub.send_string(topic, zmq.SNDMORE)
        pub.send(envelope)

    yield address, publish

    pub.close()
    ctx.term()


@pytest.fixture
def mock_zmq_ota_pub() -> Generator[tuple[str, Callable], None, None]:
    """Create a ZMQ PUB socket simulating ota_manager OTA status output.

    Yields (address, publish_fn) where publish_fn(topic, payload) sends
    a properly formatted multipart telemetry message.
    """
    ctx: zmq.Context = zmq.Context()
    pub: zmq.Socket = ctx.socket(zmq.PUB)
    port: int = pub.bind_to_random_port("tcp://127.0.0.1")
    address: str = f"tcp://127.0.0.1:{port}"

    seq: int = 0

    def publish(topic: str, payload: dict) -> None:
        nonlocal seq
        seq += 1
        ts: int = int(time.time() * 1000)
        envelope: bytes = encode_telemetry(ts, seq, "ota_manager", topic, payload)
        pub.send_string(topic, zmq.SNDMORE)
        pub.send(envelope)

    yield address, publish

    pub.close()
    ctx.term()


@pytest.fixture
def ws_test_app(test_config: dict, mock_zmq_pub: tuple[str, Callable]):
    """Create a FastAPI test app with telemetry socket pointing to mock PUB."""
    address: str = mock_zmq_pub[0]
    app = create_app(test_config, telemetry_socket=address)
    return app
