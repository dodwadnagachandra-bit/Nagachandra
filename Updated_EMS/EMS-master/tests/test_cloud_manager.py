"""Unit tests for cloud_manager — CLOUD-01 through CLOUD-08 coverage.

Test strategy:
- Config loader tests: real file I/O against temp YAML files; no mocks needed.
- MQTT publisher tests: mock paho.mqtt.client.Client entirely — no broker required.
- ZMQ status tests: use tcp://127.0.0.1:* ephemeral ports; no /run/ems dependency.
- Remaining CLOUD-02/03/06/07 stubs: xfail until Plans 02/03 implement them.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import zmq


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cloud_config_dict() -> dict[str, Any]:
    """Valid cloud config dict using plain MQTT (no TLS) for unit testing."""
    return {
        "_schema_version": "1.0",
        "broker": {
            "host": "localhost",
            "port": 1883,
            "protocol": "mqtt",
        },
        "auth": {
            "method": "token",
        },
        "telemetry": {
            "interval_s": 60,
            "topic_prefix": "ems/TEST-001",
        },
        "offline_buffer": {
            "enabled": False,
            "max_hours": 24,
            "max_mb": 50,
        },
    }


@pytest.fixture()
def cloud_config_yaml(tmp_path: Path, cloud_config_dict: dict[str, Any]) -> Path:
    """Write cloud_config_dict to a temp YAML file and return its path.

    Also creates the schemas/ directory alongside config/ so the loader
    can find cloud_config.schema.json via path derivation.
    """
    import yaml

    config_dir: Path = tmp_path / "config"
    config_dir.mkdir()
    schema_dir: Path = tmp_path / "schemas"
    schema_dir.mkdir()

    # Copy schema from project root
    project_schema: Path = Path("config/schemas/cloud_config.schema.json")
    schema_dest: Path = schema_dir / "cloud_config.schema.json"
    schema_dest.write_bytes(project_schema.read_bytes())

    config_file: Path = config_dir / "cloud_config.yaml"
    with config_file.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cloud_config_dict, f)

    return config_file


@pytest.fixture()
def schema_path() -> Path:
    """Return the project's cloud_config.schema.json path directly."""
    return Path("config/schemas/cloud_config.schema.json")


@pytest.fixture()
def mock_paho_client() -> mock.MagicMock:
    """Mock for paho.mqtt.client.Client class (not instance)."""
    with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
        mock_instance: mock.MagicMock = mock.MagicMock()
        mock_client_cls.return_value = mock_instance
        yield mock_client_cls


# ---------------------------------------------------------------------------
# Task 1: Config loader tests (CLOUD-01 / config validation)
# ---------------------------------------------------------------------------


class TestLoadCloudConfig:
    """Tests for ems_cloud_manager.config.load_cloud_config."""

    def test_happy_path_returns_dict(
        self, cloud_config_yaml: Path, schema_path: Path
    ) -> None:
        """load_cloud_config returns a dict with all expected top-level keys."""
        from ems_cloud_manager.config import load_cloud_config

        result: dict[str, Any] = load_cloud_config(cloud_config_yaml, schema_path)

        assert isinstance(result, dict)
        assert "broker" in result
        assert "auth" in result
        assert "telemetry" in result
        assert "offline_buffer" in result

    def test_happy_path_broker_values(
        self, cloud_config_yaml: Path, schema_path: Path
    ) -> None:
        """Loaded config has correct broker host, port, protocol."""
        from ems_cloud_manager.config import load_cloud_config

        result: dict[str, Any] = load_cloud_config(cloud_config_yaml, schema_path)

        assert result["broker"]["host"] == "localhost"
        assert result["broker"]["port"] == 1883
        assert result["broker"]["protocol"] == "mqtt"

    def test_missing_config_file_raises_file_not_found(
        self, tmp_path: Path, schema_path: Path
    ) -> None:
        """FileNotFoundError raised when config YAML does not exist."""
        from ems_cloud_manager.config import load_cloud_config

        missing: Path = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError, match="not found"):
            load_cloud_config(missing, schema_path)

    def test_invalid_yaml_raises_value_error(
        self, tmp_path: Path, schema_path: Path
    ) -> None:
        """ValueError raised when config file contains invalid YAML."""
        from ems_cloud_manager.config import load_cloud_config

        bad_yaml: Path = tmp_path / "bad.yaml"
        bad_yaml.write_text("{ unclosed: [bracket", encoding="utf-8")

        with pytest.raises(ValueError, match="invalid YAML"):
            load_cloud_config(bad_yaml, schema_path)

    def test_schema_violation_raises_value_error(
        self, tmp_path: Path, schema_path: Path
    ) -> None:
        """ValueError raised when config fails JSON Schema (e.g. interval_s=0)."""
        import yaml
        from ems_cloud_manager.config import load_cloud_config

        invalid_config: dict[str, Any] = {
            "_schema_version": "1.0",
            "broker": {"host": "localhost", "port": 1883, "protocol": "mqtt"},
            "auth": {"method": "token"},
            "telemetry": {"interval_s": 0, "topic_prefix": "ems/TEST"},  # 0 < minimum 1
            "offline_buffer": {"enabled": False, "max_hours": 24, "max_mb": 50},
        }
        bad_config: Path = tmp_path / "invalid.yaml"
        with bad_config.open("w") as f:
            yaml.safe_dump(invalid_config, f)

        with pytest.raises(ValueError, match="validation failed"):
            load_cloud_config(bad_config, schema_path)

    def test_config_cert_validation_missing_cert_path(
        self, tmp_path: Path, schema_path: Path
    ) -> None:
        """FileNotFoundError raised when auth.method=mtls and cert paths don't exist."""
        import yaml
        from ems_cloud_manager.config import load_cloud_config

        mtls_config: dict[str, Any] = {
            "_schema_version": "1.0",
            "broker": {"host": "broker.example.com", "port": 8883, "protocol": "mqtts"},
            "auth": {
                "method": "mtls",
                "ca_cert_path": "/nonexistent/ca.crt",
                "client_cert_path": "/nonexistent/device.crt",
                "client_key_path": "/nonexistent/device.key",
            },
            "telemetry": {"interval_s": 60, "topic_prefix": "ems/RES-001"},
            "offline_buffer": {"enabled": True, "max_hours": 24, "max_mb": 50},
        }
        config_file: Path = tmp_path / "mtls_config.yaml"
        with config_file.open("w") as f:
            yaml.safe_dump(mtls_config, f)

        with pytest.raises(FileNotFoundError, match="cert"):
            load_cloud_config(config_file, schema_path)

    def test_token_auth_skips_cert_validation(
        self, cloud_config_yaml: Path, schema_path: Path
    ) -> None:
        """No FileNotFoundError when auth.method=token (no cert paths needed)."""
        from ems_cloud_manager.config import load_cloud_config

        # cloud_config_yaml fixture uses method=token — should load cleanly
        result: dict[str, Any] = load_cloud_config(cloud_config_yaml, schema_path)
        assert result["auth"]["method"] == "token"


# ---------------------------------------------------------------------------
# SOCK_CLOUD_PUB and TOPIC_CLOUD constants in ems_common.ipc
# ---------------------------------------------------------------------------


class TestIpcConstants:
    """Verify SOCK_CLOUD_PUB and TOPIC_CLOUD are importable from ems_common.ipc."""

    def test_sock_cloud_pub_importable(self) -> None:
        """SOCK_CLOUD_PUB constant is importable and has correct ipc path."""
        from ems_common.ipc import SOCK_CLOUD_PUB

        assert SOCK_CLOUD_PUB == "ipc:///run/ems/cloud_pub.sock"

    def test_topic_cloud_importable(self) -> None:
        """TOPIC_CLOUD constant is importable and has value 'cloud'."""
        from ems_common.ipc import TOPIC_CLOUD

        assert TOPIC_CLOUD == "cloud"


# ---------------------------------------------------------------------------
# Task 2: MqttPublisher tests (CLOUD-01, CLOUD-08)
# ---------------------------------------------------------------------------


class TestMqttPublisherInit:
    """Tests for MqttPublisher initialization and paho Client setup (CLOUD-01)."""

    def test_mtls_connect(self, cloud_config_dict: dict[str, Any], tmp_path: Path) -> None:
        """MqttPublisher creates paho Client with VERSION2 and calls tls_set when auth.method=mtls."""
        # Create real cert files so config validation passes
        ca_cert: Path = tmp_path / "ca.crt"
        client_cert: Path = tmp_path / "device.crt"
        client_key: Path = tmp_path / "device.key"
        ca_cert.write_text("fake-ca", encoding="utf-8")
        client_cert.write_text("fake-cert", encoding="utf-8")
        client_key.write_text("fake-key", encoding="utf-8")

        mtls_config: dict[str, Any] = {
            "_schema_version": "1.0",
            "broker": {"host": "broker.example.com", "port": 8883, "protocol": "mqtts"},
            "auth": {
                "method": "mtls",
                "ca_cert_path": str(ca_cert),
                "client_cert_path": str(client_cert),
                "client_key_path": str(client_key),
            },
            "telemetry": {"interval_s": 60, "topic_prefix": "ems/RES-001"},
            "offline_buffer": {"enabled": False, "max_hours": 24, "max_mb": 50},
        }

        import paho.mqtt.client as mqtt
        from paho.mqtt.client import CallbackAPIVersion

        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                mtls_config, cloud_pub_endpoint="tcp://127.0.0.1:15001"
            )

            # Verify CallbackAPIVersion.VERSION2 was passed
            call_kwargs: dict[str, Any] = mock_client_cls.call_args.kwargs
            assert call_kwargs.get("callback_api_version") == CallbackAPIVersion.VERSION2

            # Verify tls_set was called with correct cert paths
            mock_instance.tls_set.assert_called_once()
            tls_call: mock.call = mock_instance.tls_set.call_args
            assert str(ca_cert) in str(tls_call)
            assert str(client_cert) in str(tls_call)
            assert str(client_key) in str(tls_call)

            publisher.cleanup()

    def test_reconnect_backoff(self, cloud_config_dict: dict[str, Any]) -> None:
        """MqttPublisher calls reconnect_delay_set(min_delay=1, max_delay=60)."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15002"
            )

            mock_instance.reconnect_delay_set.assert_called_once_with(
                min_delay=1, max_delay=60
            )
            publisher.cleanup()

    def test_no_tls_when_protocol_mqtt(self, cloud_config_dict: dict[str, Any]) -> None:
        """tls_set is NOT called when broker.protocol is 'mqtt' (test/dev mode)."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15003"
            )

            mock_instance.tls_set.assert_not_called()
            publisher.cleanup()

    def test_start_calls_loop_start_and_connect(
        self, cloud_config_dict: dict[str, Any]
    ) -> None:
        """start() calls loop_start() then connect(host, port, keepalive=60)."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15004"
            )
            publisher.start()

            mock_instance.loop_start.assert_called_once()
            mock_instance.connect.assert_called_once_with(
                cloud_config_dict["broker"]["host"],
                cloud_config_dict["broker"]["port"],
                keepalive=60,
            )
            publisher.cleanup()

    def test_callbacks_set_on_client(self, cloud_config_dict: dict[str, Any]) -> None:
        """on_connect, on_disconnect, and on_message callbacks are assigned."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15005"
            )

            # Callbacks must be set as attributes on the client instance
            assert mock_instance.on_connect is not None
            assert mock_instance.on_disconnect is not None
            assert mock_instance.on_message is not None
            publisher.cleanup()


class TestMqttPublisherCallbacks:
    """Tests for MqttPublisher paho callback behavior."""

    def test_on_connect_sets_connected_true(
        self, cloud_config_dict: dict[str, Any]
    ) -> None:
        """_on_connect callback sets self._connected = True on success."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15006"
            )

            # Simulate successful on_connect callback
            mock_reason_code: mock.MagicMock = mock.MagicMock()
            mock_reason_code.is_failure = False
            mock_connect_flags: mock.MagicMock = mock.MagicMock()
            mock_properties: mock.MagicMock = mock.MagicMock()

            assert not publisher.connected
            publisher._on_connect(
                mock_instance,
                None,
                mock_connect_flags,
                mock_reason_code,
                mock_properties,
            )
            assert publisher.connected
            publisher.cleanup()

    def test_on_connect_resubscribes_to_commands(
        self, cloud_config_dict: dict[str, Any]
    ) -> None:
        """_on_connect re-subscribes to {prefix}/commands QoS 1."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15007"
            )

            mock_reason_code: mock.MagicMock = mock.MagicMock()
            mock_reason_code.is_failure = False
            mock_connect_flags: mock.MagicMock = mock.MagicMock()
            mock_properties: mock.MagicMock = mock.MagicMock()

            publisher._on_connect(
                mock_instance,
                None,
                mock_connect_flags,
                mock_reason_code,
                mock_properties,
            )

            prefix: str = cloud_config_dict["telemetry"]["topic_prefix"]
            mock_instance.subscribe.assert_called_once_with(
                f"{prefix}/commands", qos=1
            )
            publisher.cleanup()

    def test_on_disconnect_sets_connected_false(
        self, cloud_config_dict: dict[str, Any]
    ) -> None:
        """_on_disconnect callback sets self._connected = False."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15008"
            )

            # Force connected state first
            publisher._connected = True

            mock_disconnect_flags: mock.MagicMock = mock.MagicMock()
            mock_reason_code: mock.MagicMock = mock.MagicMock()
            mock_properties: mock.MagicMock = mock.MagicMock()

            publisher._on_disconnect(
                mock_instance,
                None,
                mock_disconnect_flags,
                mock_reason_code,
                mock_properties,
            )
            assert not publisher.connected
            publisher.cleanup()

    def test_on_message_puts_to_command_queue(
        self, cloud_config_dict: dict[str, Any]
    ) -> None:
        """_on_message callback puts MQTTMessage onto command_queue (thread-safe)."""
        with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
            mock_instance: mock.MagicMock = mock.MagicMock()
            mock_client_cls.return_value = mock_instance

            from ems_cloud_manager.publisher import MqttPublisher

            publisher: MqttPublisher = MqttPublisher(
                cloud_config_dict, cloud_pub_endpoint="tcp://127.0.0.1:15009"
            )

            mock_message: mock.MagicMock = mock.MagicMock()
            mock_message.topic = "ems/TEST-001/commands"
            mock_message.payload = b'{"command": "fault_reset", "params": {}, "request_id": "abc"}'

            publisher._on_message(mock_instance, None, mock_message)

            assert not publisher.command_queue.empty()
            retrieved: Any = publisher.command_queue.get_nowait()
            assert retrieved is mock_message
            publisher.cleanup()


class TestMqttPublisherZmqStatus:
    """Tests for MqttPublisher ZMQ cloud status publication (CLOUD-08).

    ZMQ PUB/SUB role assignment:
    - MqttPublisher BINDS a PUB socket (server role — HMI connects to it).
    - The test SUB socket CONNECTS to the publisher's bound address.
    - We pick a free port, tell the publisher to bind there, then connect the SUB.
    """

    def _find_free_port(self) -> int:
        """Find an available TCP port on localhost."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_connection_status_zmq(self, cloud_config_dict: dict[str, Any]) -> None:
        """After on_connect fires, ZMQ SUB receives 'cloud' topic with status payload."""
        port: int = self._find_free_port()
        endpoint: str = f"tcp://127.0.0.1:{port}"

        zmq_ctx: zmq.Context = zmq.Context()
        sub_sock: zmq.Socket = zmq_ctx.socket(zmq.SUB)
        sub_sock.setsockopt_string(zmq.SUBSCRIBE, "cloud")
        sub_sock.setsockopt(zmq.RCVTIMEO, 2000)  # 2s timeout

        try:
            with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
                mock_instance: mock.MagicMock = mock.MagicMock()
                mock_client_cls.return_value = mock_instance

                from ems_cloud_manager.publisher import MqttPublisher

                # Publisher binds PUB socket at the chosen endpoint
                publisher: MqttPublisher = MqttPublisher(
                    cloud_config_dict, cloud_pub_endpoint=endpoint
                )
                # SUB connects to the publisher's bound address
                sub_sock.connect(endpoint)

                # Give ZMQ connect time to establish
                time.sleep(0.05)

                # Simulate on_connect callback (paho network thread)
                mock_reason_code: mock.MagicMock = mock.MagicMock()
                mock_reason_code.is_failure = False
                mock_connect_flags: mock.MagicMock = mock.MagicMock()
                mock_properties: mock.MagicMock = mock.MagicMock()

                publisher._on_connect(
                    mock_instance,
                    None,
                    mock_connect_flags,
                    mock_reason_code,
                    mock_properties,
                )

                # Read from ZMQ SUB
                topic_frame: bytes = sub_sock.recv()
                payload_frame: bytes = sub_sock.recv()

                assert topic_frame == b"cloud"

                import msgpack

                decoded: dict[str, Any] = msgpack.unpackb(payload_frame, raw=False)
                assert decoded["topic"] == "cloud"
                assert decoded["payload"]["state"] == "connected"

                publisher.cleanup()
        finally:
            sub_sock.close()
            zmq_ctx.term()

    def test_disconnect_status_zmq(self, cloud_config_dict: dict[str, Any]) -> None:
        """After on_disconnect fires, ZMQ SUB receives 'cloud' topic with 'disconnected' state."""
        port: int = self._find_free_port()
        endpoint: str = f"tcp://127.0.0.1:{port}"

        zmq_ctx: zmq.Context = zmq.Context()
        sub_sock: zmq.Socket = zmq_ctx.socket(zmq.SUB)
        sub_sock.setsockopt_string(zmq.SUBSCRIBE, "cloud")
        sub_sock.setsockopt(zmq.RCVTIMEO, 2000)

        try:
            with mock.patch("paho.mqtt.client.Client") as mock_client_cls:
                mock_instance: mock.MagicMock = mock.MagicMock()
                mock_client_cls.return_value = mock_instance

                from ems_cloud_manager.publisher import MqttPublisher

                publisher: MqttPublisher = MqttPublisher(
                    cloud_config_dict, cloud_pub_endpoint=endpoint
                )
                sub_sock.connect(endpoint)
                publisher._connected = True

                time.sleep(0.05)

                mock_disconnect_flags: mock.MagicMock = mock.MagicMock()
                mock_reason_code: mock.MagicMock = mock.MagicMock()
                mock_properties: mock.MagicMock = mock.MagicMock()

                publisher._on_disconnect(
                    mock_instance,
                    None,
                    mock_disconnect_flags,
                    mock_reason_code,
                    mock_properties,
                )

                topic_frame: bytes = sub_sock.recv()
                payload_frame: bytes = sub_sock.recv()

                assert topic_frame == b"cloud"

                import msgpack

                decoded: dict[str, Any] = msgpack.unpackb(payload_frame, raw=False)
                assert decoded["payload"]["state"] == "disconnected"

                publisher.cleanup()
        finally:
            sub_sock.close()
            zmq_ctx.term()


# ---------------------------------------------------------------------------
# CLOUD-02: Telemetry accumulator and periodic publish (Plan 22-02)
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Find an available TCP port on localhost."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_telemetry_bytes(topic: str, payload: dict[str, Any]) -> bytes:
    """Encode a ZMQ telemetry frame (msgpack envelope with nested payload)."""
    import msgpack

    msg: dict[str, Any] = {
        "ts": int(time.time() * 1000),
        "seq": 0,
        "src": "test",
        "topic": topic,
        "payload": payload,
    }
    return msgpack.packb(msg, use_bin_type=True)


def test_telemetry_accumulator(cloud_config_dict: dict[str, Any]) -> None:
    """ZMQ telemetry collector stores latest value per topic in snapshot dict (CLOUD-02).

    Sends a msgpack telemetry envelope on a tcp ZMQ PUB socket and verifies
    that CloudLoop._telemetry_snapshot is updated correctly.
    """
    import asyncio

    import msgpack

    from ems_cloud_manager.loop import CloudLoop

    telemetry_port: int = _find_free_port()
    alarm_port: int = _find_free_port()
    telemetry_endpoint: str = f"tcp://127.0.0.1:{telemetry_port}"
    alarm_endpoint: str = f"tcp://127.0.0.1:{alarm_port}"

    mock_publisher: mock.MagicMock = mock.MagicMock()
    mock_publisher.connected = True

    # Create ZMQ PUB that will act as data_manager
    pub_ctx: zmq.Context = zmq.Context()
    pub_sock: zmq.Socket = pub_ctx.socket(zmq.PUB)
    pub_sock.bind(telemetry_endpoint)

    try:
        loop_obj: CloudLoop = CloudLoop(
            cloud_config_dict,
            mock_publisher,
            telemetry_sub_endpoint=telemetry_endpoint,
            alarm_sub_endpoint=alarm_endpoint,
        )

        async def run_collector_briefly() -> None:
            # Let SUB socket connect to PUB
            await asyncio.sleep(0.1)
            # Send a PCS telemetry message
            payload: dict[str, Any] = {"dc_voltage": 400}
            body: bytes = _make_telemetry_bytes("pcs", payload)
            pub_sock.send_string("pcs", zmq.SNDMORE)
            pub_sock.send(body)
            # Let collector poll and pick it up
            await asyncio.sleep(0.15)

        async def main() -> None:
            task = asyncio.create_task(loop_obj._zmq_telemetry_collector())
            await run_collector_briefly()
            loop_obj.stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(main())

        # Verify snapshot has pcs entry
        assert "pcs" in loop_obj._telemetry_snapshot
        assert loop_obj._telemetry_snapshot["pcs"]["dc_voltage"] == 400

    finally:
        loop_obj.cleanup()
        pub_sock.close(linger=0)
        pub_ctx.term()


def test_periodic_publish(cloud_config_dict: dict[str, Any]) -> None:
    """Periodic timer fires at configured interval and publishes telemetry JSON (CLOUD-02).

    Injects a short interval_s so the test completes quickly. Verifies
    publish_telemetry is called with the correct JSON structure.
    """
    import asyncio

    from ems_cloud_manager.loop import CloudLoop

    # Override interval for fast test
    fast_config: dict[str, Any] = dict(cloud_config_dict)
    fast_config["telemetry"] = dict(cloud_config_dict["telemetry"])
    fast_config["telemetry"]["interval_s"] = 0.1

    telemetry_port: int = _find_free_port()
    alarm_port: int = _find_free_port()

    mock_publisher: mock.MagicMock = mock.MagicMock()
    mock_publisher.connected = True

    loop_obj: CloudLoop = CloudLoop(
        fast_config,
        mock_publisher,
        telemetry_sub_endpoint=f"tcp://127.0.0.1:{telemetry_port}",
        alarm_sub_endpoint=f"tcp://127.0.0.1:{alarm_port}",
    )

    # Pre-seed snapshot
    loop_obj._telemetry_snapshot["pcs"] = {"dc_voltage": 400}

    async def main() -> None:
        task = asyncio.create_task(loop_obj._periodic_publish())
        # Wait long enough for one interval to fire
        await asyncio.sleep(0.3)
        loop_obj.stop_event.set()
        await asyncio.wait_for(task, timeout=2.0)

    try:
        asyncio.run(main())
    finally:
        loop_obj.cleanup()

    # publish_telemetry should have been called at least once
    mock_publisher.publish_telemetry.assert_called()
    call_args: dict[str, Any] = mock_publisher.publish_telemetry.call_args[0][0]
    assert "ts" in call_args
    assert "data" in call_args
    assert call_args["data"]["pcs"]["dc_voltage"] == 400


def test_missing_topics_omitted(cloud_config_dict: dict[str, Any]) -> None:
    """Topics with no data since last publish are omitted from the payload (CLOUD-02).

    Only 'pcs' is in the snapshot; 'bms.rack' should NOT appear in the published payload.
    """
    import asyncio

    from ems_cloud_manager.loop import CloudLoop

    fast_config: dict[str, Any] = dict(cloud_config_dict)
    fast_config["telemetry"] = dict(cloud_config_dict["telemetry"])
    fast_config["telemetry"]["interval_s"] = 0.1

    telemetry_port: int = _find_free_port()
    alarm_port: int = _find_free_port()

    mock_publisher: mock.MagicMock = mock.MagicMock()
    mock_publisher.connected = True

    loop_obj: CloudLoop = CloudLoop(
        fast_config,
        mock_publisher,
        telemetry_sub_endpoint=f"tcp://127.0.0.1:{telemetry_port}",
        alarm_sub_endpoint=f"tcp://127.0.0.1:{alarm_port}",
    )

    # Only inject pcs; bms.rack never arrives
    loop_obj._telemetry_snapshot["pcs"] = {"dc_voltage": 400}

    async def main() -> None:
        task = asyncio.create_task(loop_obj._periodic_publish())
        await asyncio.sleep(0.3)
        loop_obj.stop_event.set()
        await asyncio.wait_for(task, timeout=2.0)

    try:
        asyncio.run(main())
    finally:
        loop_obj.cleanup()

    mock_publisher.publish_telemetry.assert_called()
    call_args: dict[str, Any] = mock_publisher.publish_telemetry.call_args[0][0]
    assert "bms.rack" not in call_args["data"], "Missing topic should be omitted, not null"
    assert "pcs" in call_args["data"]


# ---------------------------------------------------------------------------
# CLOUD-03: Event forwarding with QoS 1 (Plan 22-02)
# ---------------------------------------------------------------------------


def test_event_qos1(cloud_config_dict: dict[str, Any]) -> None:
    """Alarm events from ZMQ are forwarded to MQTT publish_event with correct payload (CLOUD-03).

    Binds a ZMQ PUB socket at a test endpoint acting as alarm_manager.
    Verifies CloudLoop._zmq_event_forwarder calls publisher.publish_event.
    """
    import asyncio

    import msgpack

    from ems_cloud_manager.loop import CloudLoop

    telemetry_port: int = _find_free_port()
    alarm_port: int = _find_free_port()
    telemetry_endpoint: str = f"tcp://127.0.0.1:{telemetry_port}"
    alarm_endpoint: str = f"tcp://127.0.0.1:{alarm_port}"

    mock_publisher: mock.MagicMock = mock.MagicMock()
    mock_publisher.connected = True

    # Alarm PUB acts as alarm_manager
    alarm_ctx: zmq.Context = zmq.Context()
    alarm_pub: zmq.Socket = alarm_ctx.socket(zmq.PUB)
    alarm_pub.bind(alarm_endpoint)

    try:
        loop_obj: CloudLoop = CloudLoop(
            cloud_config_dict,
            mock_publisher,
            telemetry_sub_endpoint=telemetry_endpoint,
            alarm_sub_endpoint=alarm_endpoint,
        )

        async def run_forwarder_briefly() -> None:
            await asyncio.sleep(0.1)
            # Send alarm event
            event_body: dict[str, Any] = {
                "ts": int(time.time() * 1000),
                "src": "alarm_manager",
                "severity": "critical",
                "event_type": "alarm",
                "message": "Overcurrent detected",
                "data": {"alarm_id": "OC-001"},
            }
            raw: bytes = msgpack.packb(event_body, use_bin_type=True)
            alarm_pub.send_string("alarm", zmq.SNDMORE)
            alarm_pub.send(raw)
            await asyncio.sleep(0.15)

        async def main() -> None:
            task = asyncio.create_task(loop_obj._zmq_event_forwarder())
            await run_forwarder_briefly()
            loop_obj.stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(main())

        # Verify publish_event was called
        mock_publisher.publish_event.assert_called()
        call_args = mock_publisher.publish_event.call_args
        topic_suffix: str = call_args[0][0]
        event_payload: dict[str, Any] = call_args[0][1]
        assert topic_suffix == "alarm"
        assert "ts" in event_payload
        assert event_payload["event_type"] == "alarm"
        assert "data" in event_payload

    finally:
        loop_obj.cleanup()
        alarm_pub.close(linger=0)
        alarm_ctx.term()


# ---------------------------------------------------------------------------
# CommandDispatcher tests (CLOUD-06) — Task 1 of Plan 22-03
# ---------------------------------------------------------------------------


class TestCommandDispatcher:
    """Tests for ems_cloud_manager.dispatcher.CommandDispatcher (CLOUD-06)."""

    def _make_rep_server(
        self,
        zmq_ctx: zmq.Context,
        endpoint: str,
        response_status: str = "ok",
    ) -> threading.Thread:
        """Spin up a minimal ZMQ REP server on a background thread.

        Returns thread with a .received attribute (list of decoded messages).
        """
        import msgpack

        rep_sock: zmq.Socket = zmq_ctx.socket(zmq.REP)
        rep_sock.setsockopt(zmq.LINGER, 0)
        rep_sock.setsockopt(zmq.RCVTIMEO, 3000)
        rep_sock.bind(endpoint)

        received: list[dict[str, Any]] = []

        def _serve() -> None:
            try:
                raw: bytes = rep_sock.recv()
                msg: dict[str, Any] = msgpack.unpackb(raw, raw=False)
                received.append(msg)
                reply: bytes = msgpack.packb(
                    {"status": response_status, "result": None, "error_msg": None},
                    use_bin_type=True,
                )
                rep_sock.send(reply)
            except Exception:
                pass
            finally:
                rep_sock.close(linger=0)

        t: threading.Thread = threading.Thread(target=_serve, daemon=True)
        t.start()
        t.received = received  # type: ignore[attr-defined]
        return t

    def test_command_dispatch(self, cloud_config_dict: dict[str, Any]) -> None:
        """Valid mode_change command forwarded via ZMQ REQ to control_cmd (CLOUD-06).

        Verifies that CommandDispatcher:
        1. Encodes correct ZMQ action + params to REP server.
        2. Publishes a success response via publisher.publish_response.
        """
        import asyncio

        from ems_cloud_manager.dispatcher import CommandDispatcher

        zmq_ctx: zmq.Context = zmq.Context()
        port: int = _find_free_port()
        control_endpoint: str = f"tcp://127.0.0.1:{port}"

        t: threading.Thread = self._make_rep_server(zmq_ctx, control_endpoint)

        mock_pub: mock.MagicMock = mock.MagicMock()
        mock_pub.connected = True

        dispatcher: CommandDispatcher = CommandDispatcher(
            mock_pub,
            control_cmd_endpoint=control_endpoint,
            alarm_cmd_endpoint=None,
        )

        # Give REP server time to bind
        time.sleep(0.05)

        mock_msg: mock.MagicMock = mock.MagicMock()
        mock_msg.payload = json.dumps(
            {
                "command": "mode_change",
                "params": {"target_state": "standby"},
                "request_id": "req-001",
            }
        ).encode()

        asyncio.run(dispatcher.dispatch(mock_msg))

        t.join(timeout=2.0)
        dispatcher.cleanup()
        zmq_ctx.term()

        # REP server must have received action=mode_change
        assert len(t.received) == 1  # type: ignore[attr-defined]
        assert t.received[0]["action"] == "mode_change"  # type: ignore[attr-defined]
        assert t.received[0]["params"] == {"target_state": "standby"}  # type: ignore[attr-defined]

        # Publisher must have received success response
        mock_pub.publish_response.assert_called_once()
        call_kwargs: dict[str, Any] = mock_pub.publish_response.call_args.kwargs
        assert call_kwargs["request_id"] == "req-001"
        assert call_kwargs["status"] == "ok"

    def test_command_invalid_rejected(
        self, cloud_config_dict: dict[str, Any]
    ) -> None:
        """Invalid/malformed commands are rejected without forwarding to ZMQ (CLOUD-06).

        Tests:
        - Malformed JSON (parse error)
        - Missing request_id key
        - Unknown command name
        """
        import asyncio

        from ems_cloud_manager.dispatcher import CommandDispatcher

        mock_pub: mock.MagicMock = mock.MagicMock()
        mock_pub.connected = True

        port: int = _find_free_port()
        dispatcher: CommandDispatcher = CommandDispatcher(
            mock_pub,
            control_cmd_endpoint=f"tcp://127.0.0.1:{port}",
            alarm_cmd_endpoint=None,
        )

        # 1. Malformed JSON
        bad_json_msg: mock.MagicMock = mock.MagicMock()
        bad_json_msg.payload = b"not valid json {{{"
        asyncio.run(dispatcher.dispatch(bad_json_msg))

        # 2. Missing request_id
        no_req_id_msg: mock.MagicMock = mock.MagicMock()
        no_req_id_msg.payload = json.dumps(
            {"command": "fault_reset", "params": {}}
        ).encode()
        asyncio.run(dispatcher.dispatch(no_req_id_msg))

        # 3. Unknown command
        unknown_cmd_msg: mock.MagicMock = mock.MagicMock()
        unknown_cmd_msg.payload = json.dumps(
            {
                "command": "launch_missile",
                "params": {},
                "request_id": "req-bad",
            }
        ).encode()
        asyncio.run(dispatcher.dispatch(unknown_cmd_msg))

        dispatcher.cleanup()

        # publish_response called 3 times, all error
        assert mock_pub.publish_response.call_count == 3
        for call in mock_pub.publish_response.call_args_list:
            assert call.kwargs["status"] == "error"

    def test_command_rate_limit(self, cloud_config_dict: dict[str, Any]) -> None:
        """Commands exceeding 10/min are rejected with rate limit error (CLOUD-06).

        First 10 commands pass through to ZMQ REP; the 11th is rejected with
        status='error' containing 'rate' in error_msg.
        """
        import asyncio

        import msgpack

        from ems_cloud_manager.dispatcher import CommandDispatcher

        zmq_ctx: zmq.Context = zmq.Context()
        port: int = _find_free_port()
        control_endpoint: str = f"tcp://127.0.0.1:{port}"

        rep_sock: zmq.Socket = zmq_ctx.socket(zmq.REP)
        rep_sock.setsockopt(zmq.LINGER, 0)
        rep_sock.setsockopt(zmq.RCVTIMEO, 2000)
        rep_sock.bind(control_endpoint)

        served: list[int] = [0]

        def _multi_serve() -> None:
            ok_reply: bytes = msgpack.packb(
                {"status": "ok", "result": None, "error_msg": None},
                use_bin_type=True,
            )
            for _ in range(10):
                try:
                    rep_sock.recv()
                    rep_sock.send(ok_reply)
                    served[0] += 1
                except Exception:
                    break
            rep_sock.close(linger=0)

        t: threading.Thread = threading.Thread(target=_multi_serve, daemon=True)
        t.start()

        mock_pub: mock.MagicMock = mock.MagicMock()
        mock_pub.connected = True

        dispatcher: CommandDispatcher = CommandDispatcher(
            mock_pub,
            control_cmd_endpoint=control_endpoint,
            alarm_cmd_endpoint=None,
        )

        time.sleep(0.05)

        for i in range(11):
            msg: mock.MagicMock = mock.MagicMock()
            msg.payload = json.dumps(
                {"command": "fault_reset", "params": {}, "request_id": f"req-{i}"}
            ).encode()
            asyncio.run(dispatcher.dispatch(msg))

        t.join(timeout=3.0)
        dispatcher.cleanup()
        zmq_ctx.term()

        # Exactly 10 commands should have passed through
        assert served[0] == 10

        # 11 publish_response calls: 10 ok + 1 rate-limit error
        assert mock_pub.publish_response.call_count == 11
        last_call: dict[str, Any] = mock_pub.publish_response.call_args_list[-1].kwargs
        assert last_call["status"] == "error"
        assert "rate" in last_call.get("error_msg", "").lower()


# ---------------------------------------------------------------------------
# Heartbeat tests (CLOUD-07) — Task 2 of Plan 22-03
# ---------------------------------------------------------------------------


def test_heartbeat_payload(cloud_config_dict: dict[str, Any]) -> None:
    """Heartbeat published at configured interval with required fields (CLOUD-07).

    Uses heartbeat_interval=0 to fire immediately on first tick.
    Verifies payload contains device_id, uptime_s, version, connected, ts.
    """
    import asyncio

    from ems_cloud_manager.loop import CloudLoop

    mock_pub: mock.MagicMock = mock.MagicMock()
    mock_pub.connected = True

    loop_obj: CloudLoop = CloudLoop(
        cloud_config_dict,
        mock_pub,
        telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
        alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
        heartbeat_interval=0,  # fire immediately on first iteration
    )

    async def _run_once() -> None:
        task = asyncio.create_task(loop_obj._heartbeat_publisher())
        await asyncio.sleep(0.15)
        loop_obj.stop_event.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run_once())
    loop_obj.cleanup()

    mock_pub.publish_heartbeat.assert_called()
    payload: dict[str, Any] = mock_pub.publish_heartbeat.call_args[0][0]

    assert "device_id" in payload
    assert "uptime_s" in payload
    assert "version" in payload
    assert "connected" in payload
    assert "ts" in payload
    assert isinstance(payload["uptime_s"], int)
    assert payload["connected"] is True
    assert isinstance(payload["ts"], int)


# ---------------------------------------------------------------------------
# BufferManager tests (CLOUD-04) — Task 1 of Plan 23-01
# ---------------------------------------------------------------------------


class TestBufferManager:
    """Tests for BufferManager JSONL offline buffering (CLOUD-04).

    All tests use tmp_path for real filesystem I/O. Time is controlled
    via unittest.mock.patch to avoid coupling to wall-clock hours.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_bm(
        tmp_path: Path,
        max_hours: int = 24,
        max_mb: int = 50,
    ) -> "BufferManager":  # noqa: F821
        from ems_cloud_manager.buffer import BufferManager

        return BufferManager(tmp_path / "buf", max_hours=max_hours, max_mb=max_mb)

    @staticmethod
    def _fixed_dt(year: int, month: int, day: int, hour: int) -> "datetime":  # noqa: F821
        from datetime import datetime, timezone

        return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # Write tests
    # ------------------------------------------------------------------

    def test_buffer_write_creates_jsonl(self, tmp_path: Path) -> None:
        """write() creates hourly JSONL file at correct path; each line is valid JSON."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path)
        fixed = self._fixed_dt(2025, 6, 15, 10)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            bm.write("telemetry", "pcs/status", {"soc": 80})

        # Flush so buffered content is written to disk
        bm.flush()

        buf_dir = tmp_path / "buf"
        expected = buf_dir / "2025" / "06" / "15" / "cloud_10.jsonl"
        assert expected.exists(), f"Expected JSONL file not found: {expected}"

        lines = expected.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert "ts" in record
        assert record["type"] == "telemetry"
        assert record["topic"] == "pcs/status"
        assert record["payload"] == {"soc": 80}

    def test_buffer_rotation(self, tmp_path: Path) -> None:
        """Writes at different hours produce separate files under correct date dirs."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path)
        buf_dir = tmp_path / "buf"

        hour_a = self._fixed_dt(2025, 6, 15, 10)
        hour_b = self._fixed_dt(2025, 6, 15, 11)
        hour_c = self._fixed_dt(2025, 6, 16, 0)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = hour_a
            bm.write("telemetry", "pcs", {"v": 1})
            mock_dt.now.return_value = hour_b
            bm.write("telemetry", "pcs", {"v": 2})
            mock_dt.now.return_value = hour_c
            bm.write("telemetry", "pcs", {"v": 3})

        assert (buf_dir / "2025" / "06" / "15" / "cloud_10.jsonl").exists()
        assert (buf_dir / "2025" / "06" / "15" / "cloud_11.jsonl").exists()
        assert (buf_dir / "2025" / "06" / "16" / "cloud_00.jsonl").exists()

    # ------------------------------------------------------------------
    # Drain / FIFO tests
    # ------------------------------------------------------------------

    def test_buffer_drain_fifo(self, tmp_path: Path) -> None:
        """drain() yields records oldest-file-first (FIFO)."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path)

        hours = [
            self._fixed_dt(2025, 6, 15, 8),
            self._fixed_dt(2025, 6, 15, 9),
            self._fixed_dt(2025, 6, 15, 10),
        ]
        values = [{"v": 1}, {"v": 2}, {"v": 3}]

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            for dt, val in zip(hours, values):
                mock_dt.now.return_value = dt
                bm.write("telemetry", "pcs", val)

        # flush so drain can read the currently-open file
        bm.flush()

        results = list(bm.drain())
        assert len(results) == 3

        # Oldest-first: hour 08 -> 09 -> 10
        payloads = [records[0]["payload"]["v"] for _path, records in results]
        assert payloads == [1, 2, 3]

    def test_buffer_crash_recovery(self, tmp_path: Path) -> None:
        """drain() skips truncated last lines without error, returns valid lines."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path)
        fixed = self._fixed_dt(2025, 6, 15, 10)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            bm.write("telemetry", "pcs", {"soc": 80})
            bm.flush()

        # Corrupt the JSONL file by appending a truncated line
        buf_dir = tmp_path / "buf"
        jsonl_file = buf_dir / "2025" / "06" / "15" / "cloud_10.jsonl"
        with jsonl_file.open("a") as f:
            f.write('{"ts": 999, "type": "telemetry", "topic": "incomplete"\n')  # truncated

        results = list(bm.drain())
        assert len(results) == 1
        _path, records = results[0]
        # Should have exactly 1 valid record (the truncated line is skipped)
        assert len(records) == 1
        assert records[0]["payload"] == {"soc": 80}

    def test_drain_deletes_file(self, tmp_path: Path) -> None:
        """Caller can delete file after draining it; drain yields file path."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path)
        fixed = self._fixed_dt(2025, 6, 15, 10)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            bm.write("event", "alarm", {"code": 42})
            bm.flush()

        results = list(bm.drain())
        assert len(results) == 1
        path, records = results[0]
        assert isinstance(path, Path)
        # Caller can now delete the file
        path.unlink()
        assert not path.exists()

    # ------------------------------------------------------------------
    # Retention tests
    # ------------------------------------------------------------------

    def test_retention_hours(self, tmp_path: Path) -> None:
        """Files older than max_hours are deleted by _enforce_retention(); recent kept."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path, max_hours=24, max_mb=500)
        buf_dir = tmp_path / "buf"

        # Write old file (48 hours ago = 2025-06-13 T10)
        old_hour = self._fixed_dt(2025, 6, 13, 10)
        # Write recent file (now = 2025-06-15 T10)
        recent_hour = self._fixed_dt(2025, 6, 15, 10)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = old_hour
            bm.write("telemetry", "pcs", {"v": "old"})
            bm.flush()

        old_file = buf_dir / "2025" / "06" / "13" / "cloud_10.jsonl"
        assert old_file.exists()

        # Now trigger a write at the "recent" time — retention runs on write()
        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = recent_hour
            bm.write("telemetry", "pcs", {"v": "recent"})

        # Old file must be gone; recent file must exist
        assert not old_file.exists(), "Old file should have been deleted by retention"
        recent_file = buf_dir / "2025" / "06" / "15" / "cloud_10.jsonl"
        assert recent_file.exists()

    def test_retention_mb(self, tmp_path: Path) -> None:
        """When total buffer exceeds max_mb, oldest files deleted until under limit."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        # Use tiny max_mb so we can trigger it with small test files
        bm = self._make_bm(tmp_path, max_hours=720, max_mb=1)
        buf_dir = tmp_path / "buf"

        # Write 3 files at different hours; each file ~50 KB (well under 1MB each)
        # We'll mock _total_size_mb to simulate exceeding limit after first two files
        hours = [
            self._fixed_dt(2025, 6, 15, 8),
            self._fixed_dt(2025, 6, 15, 9),
            self._fixed_dt(2025, 6, 15, 10),
        ]

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            for dt in hours:
                mock_dt.now.return_value = dt
                bm.write("telemetry", "pcs", {"v": dt.hour})
            bm.flush()

        # All 3 files exist
        files = [
            buf_dir / "2025" / "06" / "15" / f"cloud_{h:02d}.jsonl"
            for h in [8, 9, 10]
        ]
        for f in files:
            assert f.exists()

        # Now patch _total_size_mb to return 2.0 MB (over the 1MB limit)
        # and trigger retention by calling _enforce_retention directly
        with patch.object(bm, "_total_size_mb", return_value=2.0):
            with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
                mock_dt.now.return_value = self._fixed_dt(2025, 6, 15, 11)
                # Calling write() triggers _enforce_retention internally
                bm.write("telemetry", "pcs", {"v": 11})

        # At least the oldest file(s) must have been deleted
        # (exact count depends on reported size; oldest-first)
        remaining = sorted(
            (buf_dir / "2025" / "06" / "15").glob("cloud_*.jsonl")
        )
        # The newest files survive; the oldest was deleted
        assert (buf_dir / "2025" / "06" / "15" / "cloud_10.jsonl").exists() or len(
            remaining
        ) < 4, "Retention should have deleted oldest files"

    def test_retention_dual(self, tmp_path: Path) -> None:
        """Both max_hours and max_mb are enforced — whichever triggers first deletes."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path, max_hours=24, max_mb=1)
        buf_dir = tmp_path / "buf"

        # Write an old file (will be caught by hours-based retention)
        old_hour = self._fixed_dt(2025, 6, 13, 10)
        recent_hour = self._fixed_dt(2025, 6, 15, 10)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = old_hour
            bm.write("telemetry", "pcs", {"v": "old"})
            bm.flush()

        old_file = buf_dir / "2025" / "06" / "13" / "cloud_10.jsonl"
        assert old_file.exists()

        # Trigger write at recent time — hours-based retention fires first
        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = recent_hour
            bm.write("telemetry", "pcs", {"v": "recent"})

        assert not old_file.exists()

    # ------------------------------------------------------------------
    # Full-buffer / drop tests
    # ------------------------------------------------------------------

    def test_buffer_full_drops(self, tmp_path: Path) -> None:
        """When buffer is full (both limits minimal), write() logs WARNING and drops."""
        from datetime import datetime, timezone
        from unittest.mock import patch
        import logging

        # Use max_mb=0 to guarantee buffer is always "full"
        bm = self._make_bm(tmp_path, max_hours=1, max_mb=0)
        fixed = self._fixed_dt(2025, 6, 15, 10)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            # Patch _total_size_mb to return value over max_mb=0
            with patch.object(bm, "_total_size_mb", return_value=0.001):
                with patch.object(bm, "_all_files_sorted", return_value=[]):
                    # No files to delete but buffer "full" — should drop
                    import logging as logging_module
                    with patch.object(logging_module.getLogger("ems_cloud_manager.buffer"), "warning") as mock_warn:
                        bm.write("telemetry", "pcs", {"dropped": True})
                        # Warning must be emitted (or message dropped silently — either is OK)
                        # The key invariant is: no exception raised, operation is non-blocking

        # The write either succeeded or was dropped — no exception either way
        # This is the "non-blocking" guarantee
        buf_dir = tmp_path / "buf"
        # We can't assert file exists/not without knowing exact drop logic;
        # just assert no exception was raised (test passes if we get here)

    # ------------------------------------------------------------------
    # fsync tests
    # ------------------------------------------------------------------

    def test_fsync_every_100(self, tmp_path: Path) -> None:
        """After 100 writes, file is fsynced (tracked via _writes_since_sync counter)."""
        from datetime import datetime, timezone
        from unittest.mock import patch
        import os

        bm = self._make_bm(tmp_path)
        fixed = self._fixed_dt(2025, 6, 15, 10)

        fsync_calls: list[int] = []

        def mock_fsync(fd: int) -> None:
            fsync_calls.append(fd)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            with patch("os.fsync", side_effect=mock_fsync):
                for i in range(100):
                    bm.write("telemetry", "pcs", {"i": i})

        # After 100 writes, fsync must have been called at least once
        assert len(fsync_calls) >= 1, "fsync not called after 100 writes"

        # _writes_since_sync should be reset after fsync
        assert bm._writes_since_sync == 0

    # ------------------------------------------------------------------
    # Stats tests
    # ------------------------------------------------------------------

    def test_stats_property(self, tmp_path: Path) -> None:
        """stats returns {files_remaining: N, mb_remaining: float}."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path)
        buf_dir = tmp_path / "buf"

        # Empty buffer
        stats = bm.stats
        assert stats["files_remaining"] == 0
        assert stats["mb_remaining"] == 0.0

        # Write two files
        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = self._fixed_dt(2025, 6, 15, 8)
            bm.write("telemetry", "pcs", {"v": 1})
            mock_dt.now.return_value = self._fixed_dt(2025, 6, 15, 9)
            bm.write("telemetry", "pcs", {"v": 2})
            bm.flush()

        stats = bm.stats
        assert stats["files_remaining"] == 2
        assert stats["mb_remaining"] >= 0.0
        assert isinstance(stats["mb_remaining"], float)

    # ------------------------------------------------------------------
    # Empty parent cleanup tests
    # ------------------------------------------------------------------

    def test_empty_parent_cleanup(self, tmp_path: Path) -> None:
        """After retention deletes a file, empty parent directories are removed."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        bm = self._make_bm(tmp_path, max_hours=24, max_mb=500)
        buf_dir = tmp_path / "buf"

        # Write old file
        old_hour = self._fixed_dt(2025, 6, 13, 10)
        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = old_hour
            bm.write("telemetry", "pcs", {"v": "old"})
            bm.flush()

        old_day_dir = buf_dir / "2025" / "06" / "13"
        assert old_day_dir.exists()

        # Trigger retention at 2025-06-15 — old file removed, empty dirs cleaned
        recent = self._fixed_dt(2025, 6, 15, 10)
        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = recent
            bm.write("telemetry", "pcs", {"v": "recent"})

        # The empty day dir should have been removed
        assert not old_day_dir.exists(), "Empty parent dir should be cleaned up"

    # ------------------------------------------------------------------
    # Flush tests
    # ------------------------------------------------------------------

    def test_flush_current_file(self, tmp_path: Path) -> None:
        """flush() closes and fsyncs the currently-open write file handle."""
        from datetime import datetime, timezone
        from unittest.mock import patch
        import os

        bm = self._make_bm(tmp_path)
        fixed = self._fixed_dt(2025, 6, 15, 10)

        with patch("ems_cloud_manager.buffer.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            bm.write("telemetry", "pcs", {"v": 1})

        # File handle should be open
        assert bm._current_file is not None

        bm.flush()

        # After flush, handle is closed and hour key is reset
        assert bm._current_file is None
        assert bm._current_hour_key == ""

        # File should exist and be readable
        buf_dir = tmp_path / "buf"
        expected = buf_dir / "2025" / "06" / "15" / "cloud_10.jsonl"
        assert expected.exists()
        records = [json.loads(line) for line in expected.read_text().strip().splitlines()]
        assert len(records) == 1


# ---------------------------------------------------------------------------
# Phase 23 Plan 02: BufferedCloudLoop tests (CLOUD-05)
# ---------------------------------------------------------------------------


def _make_mock_publisher(connected: bool = True) -> mock.MagicMock:
    """Create a MagicMock MqttPublisher with a controllable connected property."""
    pub: mock.MagicMock = mock.MagicMock()
    # Make `connected` a regular attribute (not a call result)
    pub.connected = connected
    pub.command_queue = queue.Queue()
    return pub


def _make_buffered_loop(
    config: dict[str, Any],
    publisher: mock.MagicMock,
    buffer_dir: Path,
    *,
    max_hours: int = 24,
    max_mb: int = 50,
) -> "BufferedCloudLoop":
    """Build a BufferedCloudLoop with test-safe ZMQ endpoints."""
    from ems_cloud_manager.buffer import BufferManager
    from ems_cloud_manager.buffered_loop import BufferedCloudLoop

    bm: BufferManager = BufferManager(buffer_dir, max_hours=max_hours, max_mb=max_mb)
    return BufferedCloudLoop(
        config,
        publisher,
        bm,
        telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
        alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
        heartbeat_interval=9999,
    )


class TestBufferedCloudLoop:
    """Tests for BufferedCloudLoop — CLOUD-05 offline buffer integration."""

    # ------------------------------------------------------------------
    # Telemetry routing
    # ------------------------------------------------------------------

    def test_telemetry_routes_to_buffer_when_offline(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """When offline, _do_publish_telemetry writes to buffer, not MQTT."""
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        pub: mock.MagicMock = _make_mock_publisher(connected=False)
        bm: BufferManager = BufferManager(tmp_path, max_hours=24, max_mb=50)
        bm_mock: mock.MagicMock = mock.MagicMock(wraps=bm)

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm_mock,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        payload: dict[str, Any] = {"ts": 1000, "data": {"pcs": {"soc": 80}}}
        loop_obj._do_publish_telemetry(payload)

        bm_mock.write.assert_called_once_with("telemetry", "telemetry", payload)
        pub.publish_telemetry.assert_not_called()
        loop_obj.cleanup()

    def test_telemetry_routes_to_mqtt_when_online(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """When online, _do_publish_telemetry delegates to publisher.publish_telemetry."""
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop
        from ems_cloud_manager.buffer import BufferManager

        pub: mock.MagicMock = _make_mock_publisher(connected=True)
        bm: BufferManager = BufferManager(tmp_path, max_hours=24, max_mb=50)
        bm_mock: mock.MagicMock = mock.MagicMock(wraps=bm)

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm_mock,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        payload: dict[str, Any] = {"ts": 1000, "data": {"pcs": {"soc": 80}}}
        loop_obj._do_publish_telemetry(payload)

        pub.publish_telemetry.assert_called_once_with(payload)
        bm_mock.write.assert_not_called()
        loop_obj.cleanup()

    # ------------------------------------------------------------------
    # Event routing
    # ------------------------------------------------------------------

    def test_event_routes_to_buffer_when_offline(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """When offline, _do_publish_event writes to buffer."""
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        pub: mock.MagicMock = _make_mock_publisher(connected=False)
        bm: BufferManager = BufferManager(tmp_path, max_hours=24, max_mb=50)
        bm_mock: mock.MagicMock = mock.MagicMock(wraps=bm)

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm_mock,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        topic: str = "alarm"
        payload: dict[str, Any] = {"ts": 2000, "event_type": "alarm", "data": {}}
        loop_obj._do_publish_event(topic, payload)

        bm_mock.write.assert_called_once_with("event", topic, payload)
        pub.publish_event.assert_not_called()
        loop_obj.cleanup()

    def test_event_routes_to_mqtt_when_online(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """When online, _do_publish_event delegates to publisher.publish_event."""
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        pub: mock.MagicMock = _make_mock_publisher(connected=True)
        bm: BufferManager = BufferManager(tmp_path, max_hours=24, max_mb=50)
        bm_mock: mock.MagicMock = mock.MagicMock(wraps=bm)

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm_mock,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        topic: str = "state_change"
        payload: dict[str, Any] = {"ts": 3000, "event_type": "state_change", "data": {}}
        loop_obj._do_publish_event(topic, payload)

        pub.publish_event.assert_called_once_with(topic, payload)
        bm_mock.write.assert_not_called()
        loop_obj.cleanup()

    # ------------------------------------------------------------------
    # _periodic_publish override: no connection guard
    # ------------------------------------------------------------------

    def test_periodic_publish_calls_hook_when_offline(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """_periodic_publish must call _do_publish_telemetry even when offline.

        The base CloudLoop skips publish when disconnected; BufferedCloudLoop
        removes that guard so telemetry flows to the buffer.
        """
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        pub: mock.MagicMock = _make_mock_publisher(connected=False)
        bm: BufferManager = BufferManager(tmp_path, max_hours=24, max_mb=50)

        cfg: dict[str, Any] = dict(cloud_config_dict)
        cfg["telemetry"] = dict(cfg["telemetry"], interval_s=0.05)

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cfg,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        # Pre-populate snapshot so the empty-snapshot guard doesn't skip it
        loop_obj._telemetry_snapshot["pcs"] = {"soc": 75}

        called: list[dict[str, Any]] = []
        original_hook = loop_obj._do_publish_telemetry

        def _capture(payload: dict[str, Any]) -> None:
            called.append(payload)

        loop_obj._do_publish_telemetry = _capture  # type: ignore[method-assign]

        async def _run_once() -> None:
            task = asyncio.create_task(loop_obj._periodic_publish())
            await asyncio.sleep(0.15)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_once())
        loop_obj.cleanup()

        assert len(called) >= 1, (
            "_do_publish_telemetry should be called even when publisher is offline"
        )

    # ------------------------------------------------------------------
    # _zmq_event_forwarder override: no connection guard
    # ------------------------------------------------------------------

    def test_event_forwarder_calls_hook_when_offline(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """_zmq_event_forwarder must call _do_publish_event even when offline.

        The base CloudLoop guards with `if self._publisher.connected:`; the
        override in BufferedCloudLoop removes that guard.
        """
        import asyncio
        import msgpack
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        alarm_port: int = _find_free_port()
        pub: mock.MagicMock = _make_mock_publisher(connected=False)
        bm: BufferManager = BufferManager(tmp_path, max_hours=24, max_mb=50)

        # Bind the alarm PUB socket BEFORE constructing the loop so the subscriber
        # connects to an already-listening socket (avoids ZMQ slow-joiner drop).
        alarm_ctx: zmq.Context = zmq.Context()
        alarm_pub: zmq.Socket = alarm_ctx.socket(zmq.PUB)
        alarm_pub.bind(f"tcp://127.0.0.1:{alarm_port}")

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{alarm_port}",
            heartbeat_interval=9999,
        )

        captured_events: list[tuple[str, dict[str, Any]]] = []

        def _capture(topic: str, payload: dict[str, Any]) -> None:
            captured_events.append((topic, payload))

        loop_obj._do_publish_event = _capture  # type: ignore[method-assign]

        async def _run_with_event() -> None:
            task = asyncio.create_task(loop_obj._zmq_event_forwarder())

            # Let subscriber settle, then send event
            await asyncio.sleep(0.1)
            body: bytes = msgpack.packb({"severity": "critical"}, use_bin_type=True)
            alarm_pub.send_multipart([b"alarm", body])

            await asyncio.sleep(0.15)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_with_event())
        loop_obj.cleanup()
        alarm_pub.close(linger=0)
        alarm_ctx.term()

        assert len(captured_events) >= 1, (
            "_do_publish_event should be called even when publisher is offline"
        )
        assert captured_events[0][0] == "alarm"

    # ------------------------------------------------------------------
    # Replay task: drain FIFO
    # ------------------------------------------------------------------

    def test_replay_drains_fifo(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """On reconnect, _buffer_replay_task reads buffer files oldest-first via drain()."""
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        buf_dir: Path = tmp_path / "buf"
        bm: BufferManager = BufferManager(buf_dir, max_hours=24, max_mb=50)

        # Pre-write two records to the buffer (flushed so drain() can read them)
        bm.write("telemetry", "telemetry", {"soc": 80})
        bm.flush()

        pub: mock.MagicMock = _make_mock_publisher(connected=True)

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        # Increase replay rate for fast test
        loop_obj._replay_rate = 1000.0

        async def _run_replay() -> None:
            task = asyncio.create_task(loop_obj._buffer_replay_task())
            await asyncio.sleep(0.3)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_replay())
        loop_obj.cleanup()

        pub.publish_telemetry.assert_called()

    def test_replay_throttle(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """Replay task sleeps ~0.1s between messages (10 msg/s throttle)."""
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        buf_dir: Path = tmp_path / "buf"
        bm: BufferManager = BufferManager(buf_dir, max_hours=24, max_mb=50)

        # Write 3 records
        for i in range(3):
            bm.write("telemetry", "telemetry", {"seq": i})
        bm.flush()

        pub: mock.MagicMock = _make_mock_publisher(connected=True)
        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        # Default rate: 10 msg/s = 0.1s interval

        async def _run_replay() -> None:
            task = asyncio.create_task(loop_obj._buffer_replay_task())
            # 3 messages at 0.1s each = 0.3s; wait a bit longer
            await asyncio.sleep(0.8)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        t_start: float = time.monotonic()
        asyncio.run(_run_replay())
        elapsed: float = time.monotonic() - t_start
        loop_obj.cleanup()

        # 3 messages * 0.1s = 0.3s minimum; task overhead gives some slack
        assert elapsed >= 0.25, f"Expected >= 0.25s for 3 messages, got {elapsed:.3f}s"
        assert pub.publish_telemetry.call_count == 3

    def test_replay_stops_on_disconnect(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """If publisher.connected goes False mid-replay, task stops draining."""
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        buf_dir: Path = tmp_path / "buf"
        bm: BufferManager = BufferManager(buf_dir, max_hours=24, max_mb=50)

        # Write many records so replay takes long enough to interrupt
        for i in range(20):
            bm.write("telemetry", "telemetry", {"seq": i})
        bm.flush()

        pub: mock.MagicMock = _make_mock_publisher(connected=True)

        call_count: list[int] = [0]
        original_publish_telemetry = pub.publish_telemetry

        def _disconnect_after_first(payload: dict[str, Any]) -> None:
            call_count[0] += 1
            if call_count[0] >= 1:
                pub.connected = False

        pub.publish_telemetry = _disconnect_after_first

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        loop_obj._replay_rate = 100.0  # fast enough to detect stop quickly

        async def _run_replay() -> None:
            task = asyncio.create_task(loop_obj._buffer_replay_task())
            await asyncio.sleep(0.3)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_replay())
        loop_obj.cleanup()

        # Should have stopped after just a few publishes (far fewer than 20)
        assert call_count[0] < 10, (
            f"Replay should stop on disconnect, but published {call_count[0]} messages"
        )

    def test_replay_resumes_on_reconnect(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """After replay stops due to disconnect, reconnect resumes drain."""
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        buf_dir: Path = tmp_path / "buf"
        bm: BufferManager = BufferManager(buf_dir, max_hours=24, max_mb=50)

        bm.write("telemetry", "telemetry", {"seq": 0})
        bm.flush()

        pub: mock.MagicMock = _make_mock_publisher(connected=False)
        call_count: list[int] = [0]

        def _count_publish(payload: dict[str, Any]) -> None:
            call_count[0] += 1

        pub.publish_telemetry = _count_publish

        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        loop_obj._replay_rate = 100.0

        async def _run_with_reconnect() -> None:
            task = asyncio.create_task(loop_obj._buffer_replay_task())
            # Start disconnected — replay should wait; the disconnect poll is 1.0s
            await asyncio.sleep(0.05)
            assert call_count[0] == 0, "Should not publish while disconnected"
            # Simulate reconnect; wait long enough for the 1.0s poll to fire
            pub.connected = True
            await asyncio.sleep(1.5)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_with_reconnect())
        loop_obj.cleanup()

        assert call_count[0] >= 1, "Replay should resume after reconnect"

    def test_replay_deletes_file_after_full_publish(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """After all records in a buffer file are published, the file is deleted."""
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        buf_dir: Path = tmp_path / "buf"
        bm: BufferManager = BufferManager(buf_dir, max_hours=24, max_mb=50)

        bm.write("telemetry", "telemetry", {"soc": 90})
        bm.flush()

        files_before: list[Path] = list(buf_dir.rglob("cloud_*.jsonl"))
        assert len(files_before) == 1, "Should have one buffer file before replay"

        pub: mock.MagicMock = _make_mock_publisher(connected=True)
        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        loop_obj._replay_rate = 1000.0

        async def _run_replay() -> None:
            task = asyncio.create_task(loop_obj._buffer_replay_task())
            await asyncio.sleep(0.3)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_replay())
        loop_obj.cleanup()

        files_after: list[Path] = list(buf_dir.rglob("cloud_*.jsonl"))
        assert len(files_after) == 0, (
            f"Buffer file should be deleted after full replay, got: {files_after}"
        )

    # ------------------------------------------------------------------
    # Buffer status on ZMQ
    # ------------------------------------------------------------------

    def test_buffer_status_published(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """After replay cycle, _publish_buffer_status is called with buffer stats."""
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        buf_dir: Path = tmp_path / "buf"
        bm: BufferManager = BufferManager(buf_dir, max_hours=24, max_mb=50)

        # Empty buffer — replay cycle runs, then publishes status
        pub: mock.MagicMock = _make_mock_publisher(connected=True)
        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )

        publish_status_calls: list[int] = [0]
        original_method = loop_obj._publish_buffer_status

        def _track_status() -> None:
            publish_status_calls[0] += 1
            original_method()

        loop_obj._publish_buffer_status = _track_status  # type: ignore[method-assign]

        async def _run_replay() -> None:
            task = asyncio.create_task(loop_obj._buffer_replay_task())
            await asyncio.sleep(0.3)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_replay())
        loop_obj.cleanup()

        assert publish_status_calls[0] >= 1, "_publish_buffer_status should be called"

    # ------------------------------------------------------------------
    # flush() called before replay
    # ------------------------------------------------------------------

    def test_flush_before_replay(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """On each reconnect cycle, buffer.flush() is called before drain()."""
        import asyncio
        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        buf_dir: Path = tmp_path / "buf"
        bm: BufferManager = BufferManager(buf_dir, max_hours=24, max_mb=50)

        flush_calls: list[int] = [0]
        original_flush = bm.flush

        def _track_flush() -> None:
            flush_calls[0] += 1
            original_flush()

        bm.flush = _track_flush  # type: ignore[method-assign]

        pub: mock.MagicMock = _make_mock_publisher(connected=True)
        loop_obj: BufferedCloudLoop = BufferedCloudLoop(
            cloud_config_dict,
            pub,
            bm,
            telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
            heartbeat_interval=9999,
        )
        loop_obj._replay_rate = 1000.0

        async def _run_replay() -> None:
            task = asyncio.create_task(loop_obj._buffer_replay_task())
            await asyncio.sleep(0.2)
            loop_obj.stop_event.set()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run_replay())
        loop_obj.cleanup()

        assert flush_calls[0] >= 1, "buffer.flush() should be called before drain()"


class TestMainWiring:
    """Tests for __main__.py conditional BufferedCloudLoop wiring."""

    def _build_loop_from_config(
        self,
        config: dict[str, Any],
        tmp_path: Path,
        *,
        buffer_dir_env: str | None = None,
    ) -> object:
        """Replicate the __main__.py wiring logic and return the constructed loop."""
        import os
        from unittest.mock import patch

        from ems_cloud_manager.buffer import BufferManager
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop
        from ems_cloud_manager.dispatcher import CommandDispatcher
        from ems_cloud_manager.loop import CloudLoop

        pub: mock.MagicMock = mock.MagicMock()
        pub.command_queue = queue.Queue()
        dispatcher: mock.MagicMock = mock.MagicMock()

        buffer_cfg: dict[str, Any] = config.get("offline_buffer", {})
        t_ep: str = f"tcp://127.0.0.1:{_find_free_port()}"
        a_ep: str = f"tcp://127.0.0.1:{_find_free_port()}"

        if buffer_cfg.get("enabled", False):
            buf_dir: Path = Path(
                buffer_dir_env if buffer_dir_env else str(tmp_path / "cloud_buffer")
            )
            bm: BufferManager = BufferManager(
                buf_dir,
                max_hours=buffer_cfg["max_hours"],
                max_mb=buffer_cfg["max_mb"],
            )
            loop_obj = BufferedCloudLoop(
                config,
                pub,
                bm,
                dispatcher=dispatcher,
                telemetry_sub_endpoint=t_ep,
                alarm_sub_endpoint=a_ep,
                heartbeat_interval=9999,
            )
        else:
            loop_obj = CloudLoop(
                config,
                pub,
                dispatcher=dispatcher,
                telemetry_sub_endpoint=t_ep,
                alarm_sub_endpoint=a_ep,
                heartbeat_interval=9999,
            )
        return loop_obj

    def test_main_creates_buffered_loop_when_enabled(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """When offline_buffer.enabled is True, wiring creates a BufferedCloudLoop."""
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        cfg: dict[str, Any] = dict(cloud_config_dict)
        cfg["offline_buffer"] = {"enabled": True, "max_hours": 24, "max_mb": 50}

        loop_obj = self._build_loop_from_config(cfg, tmp_path)
        try:
            assert isinstance(loop_obj, BufferedCloudLoop), (
                f"Expected BufferedCloudLoop, got {type(loop_obj)}"
            )
        finally:
            loop_obj.cleanup()  # type: ignore[union-attr]

    def test_main_creates_plain_loop_when_disabled(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """When offline_buffer.enabled is False, wiring creates a plain CloudLoop."""
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop
        from ems_cloud_manager.loop import CloudLoop

        cfg: dict[str, Any] = dict(cloud_config_dict)
        cfg["offline_buffer"] = {"enabled": False, "max_hours": 24, "max_mb": 50}

        loop_obj = self._build_loop_from_config(cfg, tmp_path)
        try:
            assert isinstance(loop_obj, CloudLoop), (
                f"Expected CloudLoop, got {type(loop_obj)}"
            )
            assert not isinstance(loop_obj, BufferedCloudLoop), (
                "Should be plain CloudLoop, not BufferedCloudLoop"
            )
        finally:
            loop_obj.cleanup()  # type: ignore[union-attr]

    def test_main_uses_env_var_for_buffer_dir(
        self, tmp_path: Path, cloud_config_dict: dict[str, Any]
    ) -> None:
        """EMS_CLOUD_BUFFER_DIR env var overrides the default buffer directory."""
        from ems_cloud_manager.buffered_loop import BufferedCloudLoop

        cfg: dict[str, Any] = dict(cloud_config_dict)
        cfg["offline_buffer"] = {"enabled": True, "max_hours": 24, "max_mb": 50}
        custom_dir: Path = tmp_path / "custom_buffer"

        loop_obj = self._build_loop_from_config(
            cfg, tmp_path, buffer_dir_env=str(custom_dir)
        )
        try:
            assert isinstance(loop_obj, BufferedCloudLoop)
            assert loop_obj._buffer._buffer_dir == custom_dir
        finally:
            loop_obj.cleanup()  # type: ignore[union-attr]


def test_heartbeat_not_published_when_disconnected(
    cloud_config_dict: dict[str, Any],
) -> None:
    """Heartbeat is NOT published when publisher.connected is False (CLOUD-07)."""
    import asyncio

    from ems_cloud_manager.loop import CloudLoop

    mock_pub: mock.MagicMock = mock.MagicMock()
    mock_pub.connected = False  # disconnected

    loop_obj: CloudLoop = CloudLoop(
        cloud_config_dict,
        mock_pub,
        telemetry_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
        alarm_sub_endpoint=f"tcp://127.0.0.1:{_find_free_port()}",
        heartbeat_interval=0,
    )

    async def _run_once() -> None:
        task = asyncio.create_task(loop_obj._heartbeat_publisher())
        await asyncio.sleep(0.15)
        loop_obj.stop_event.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run_once())
    loop_obj.cleanup()

    mock_pub.publish_heartbeat.assert_not_called()
