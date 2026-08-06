"""MQTT publisher for EMS cloud_manager.

Wraps paho-mqtt 2.x with mTLS, exponential reconnect backoff, and VERSION2
callback API. Publishes cloud connection status on a dedicated ZMQ PUB socket
(SOCK_CLOUD_PUB) so the HMI can display connectivity state.

Thread model:
- paho runs its own network thread via loop_start().
- Callbacks (_on_connect, _on_disconnect, _on_message) run in paho's thread.
- command_queue and status_queue are threading.Queue (NOT asyncio.Queue) to
  allow safe put_nowait() from the paho callback thread.
- publish_* methods are safe to call from any thread (paho publish() is
  internally thread-safe in paho 2.x).
"""

from __future__ import annotations

import json
import logging
import queue
import ssl
import time
from typing import Any

import paho.mqtt.client as mqtt
import zmq
from paho.mqtt.client import CallbackAPIVersion

from ems_common.ipc import SOCK_CLOUD_PUB, encode_telemetry

logger: logging.Logger = logging.getLogger(__name__)


class MqttPublisher:
    """MQTT client wrapper with mTLS, reconnect backoff, and ZMQ cloud status.

    Initializes the paho Client with CallbackAPIVersion.VERSION2, configures
    TLS if auth.method is 'mtls', sets exponential reconnect backoff (1s–60s),
    and binds a ZMQ PUB socket for cloud connection status.

    Call start() to begin the network loop and initiate the first connection.
    Call cleanup() when shutting down.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        cloud_pub_endpoint: str | None = None,
    ) -> None:
        """Initialise the MQTT publisher.

        Args:
            config: Validated cloud_config dict (from load_cloud_config).
            cloud_pub_endpoint: ZMQ PUB endpoint to bind for cloud status.
                Defaults to SOCK_CLOUD_PUB ("ipc:///run/ems/cloud_pub.sock").
        """
        self._config: dict[str, Any] = config
        self._broker: dict[str, Any] = config["broker"]
        self._auth: dict[str, Any] = config["auth"]
        self._telemetry_cfg: dict[str, Any] = config["telemetry"]
        self._prefix: str = self._telemetry_cfg["topic_prefix"]
        self._broker_host: str = self._broker["host"]
        self._broker_port: int = self._broker["port"]

        # Thread-safe queues — paho callbacks run in paho's network thread
        self._command_queue: queue.Queue[mqtt.MQTTMessage] = queue.Queue()
        self._status_queue: queue.Queue[str] = queue.Queue()

        # Connection state (written by paho callback thread, read by any thread)
        self._connected: bool = False
        self._seq: int = 0

        # ZMQ PUB socket for cloud status (CLOUD-08)
        self._zmq_ctx: zmq.Context = zmq.Context()
        self._cloud_pub: zmq.Socket = self._zmq_ctx.socket(zmq.PUB)
        endpoint: str = cloud_pub_endpoint or SOCK_CLOUD_PUB
        self._cloud_pub.bind(endpoint)

        # Create paho Client with VERSION2 (required in paho-mqtt 2.x)
        self._client: mqtt.Client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id="ems-cloud-manager",
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )

        # Configure mTLS if required (must be called before connect())
        if self._broker.get("protocol") == "mqtts" or self._auth.get("method") == "mtls":
            self._client.tls_set(
                ca_certs=self._auth["ca_cert_path"],
                certfile=self._auth["client_cert_path"],
                keyfile=self._auth["client_key_path"],
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )

        # Exponential backoff: 1s initial → 60s maximum
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        # Assign VERSION2 callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the paho network loop and connect to the broker.

        Calls loop_start() (background thread) then connect().
        connect() is non-blocking; on_connect fires when the broker responds.
        """
        self._client.loop_start()
        self._client.connect(self._broker_host, self._broker_port, keepalive=60)

    @property
    def connected(self) -> bool:
        """True if the MQTT client is currently connected to the broker."""
        return self._connected

    @property
    def command_queue(self) -> queue.Queue[mqtt.MQTTMessage]:
        """Thread-safe queue of inbound MQTT command messages."""
        return self._command_queue

    @property
    def status_queue(self) -> queue.Queue[str]:
        """Thread-safe queue of connection status strings ('connected'/'disconnected')."""
        return self._status_queue

    def publish_telemetry(self, payload: dict[str, Any]) -> None:
        """Publish consolidated telemetry snapshot to {prefix}/telemetry (QoS 0).

        Args:
            payload: Dict of telemetry data to publish as JSON.
        """
        topic: str = f"{self._prefix}/telemetry"
        self._client.publish(topic, json.dumps(payload).encode(), qos=0)

    def publish_event(self, topic_suffix: str, payload: dict[str, Any]) -> None:
        """Publish an event to {prefix}/events (QoS 1).

        Args:
            topic_suffix: Suffix appended after {prefix}/ (e.g. "events").
            payload: Dict of event data to publish as JSON.
        """
        topic: str = f"{self._prefix}/{topic_suffix}"
        self._client.publish(topic, json.dumps(payload).encode(), qos=1)

    def publish_heartbeat(self, payload: dict[str, Any]) -> None:
        """Publish device heartbeat to {prefix}/status (QoS 0).

        Args:
            payload: Dict with heartbeat fields (state, uptime_s, etc.).
        """
        topic: str = f"{self._prefix}/status"
        self._client.publish(topic, json.dumps(payload).encode(), qos=0)

    def publish_response(
        self,
        request_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error_msg: str | None = None,
    ) -> None:
        """Publish a command response to {prefix}/responses/{request_id} (QoS 1).

        Args:
            request_id: The request_id from the original MQTT command.
            status: "ok" or "error".
            result: Optional result payload dict.
            error_msg: Optional human-readable error description.
        """
        topic: str = f"{self._prefix}/responses/{request_id}"
        response: dict[str, Any] = {
            "status": status,
            "result": result,
            "error_msg": error_msg,
        }
        self._client.publish(topic, json.dumps(response).encode(), qos=1)

    def cleanup(self) -> None:
        """Stop the MQTT network loop, disconnect, and close ZMQ sockets."""
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._cloud_pub.close(linger=0)
            self._zmq_ctx.term()
        except Exception:  # noqa: BLE001
            pass

    # -----------------------------------------------------------------------
    # paho VERSION2 callbacks (run in paho's network thread)
    # -----------------------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        connect_flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties,
    ) -> None:
        """Handle broker connection established.

        Re-subscribes to {prefix}/commands (clean_session=True loses subs on
        reconnect). Sets connected flag and publishes status on ZMQ PUB.
        """
        if reason_code.is_failure:
            logger.error("MQTT connect failed: %s", reason_code)
            return

        logger.info("MQTT connected to broker: %s", reason_code)

        # Re-subscribe every connect — required with clean_session=True
        client.subscribe(f"{self._prefix}/commands", qos=1)

        self._connected = True
        self._status_queue.put_nowait("connected")
        self._publish_cloud_status("connected")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties,
    ) -> None:
        """Handle broker disconnection.

        Clears connected flag and publishes status on ZMQ PUB. paho's
        reconnect backoff (configured in __init__) handles reconnection.
        """
        logger.warning("MQTT disconnected: %s", reason_code)

        self._connected = False
        self._status_queue.put_nowait("disconnected")
        self._publish_cloud_status("disconnected")

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        """Handle inbound MQTT message (command from cloud).

        Puts message onto command_queue using put_nowait() — safe to call
        from paho's network thread without asyncio context.
        """
        self._command_queue.put_nowait(message)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _publish_cloud_status(self, state: str) -> None:
        """Publish cloud connection state on SOCK_CLOUD_PUB for HMI (CLOUD-08).

        Uses encode_telemetry from ems_common.ipc so the HMI can decode it
        the same way as other telemetry messages.

        Args:
            state: Connection state string: 'connected' or 'disconnected'.
        """
        payload: dict[str, Any] = {
            "state": state,
            "broker": self._broker_host,
            "ts": int(time.time() * 1000),
        }
        raw: bytes = encode_telemetry(
            timestamp_ms=int(time.time() * 1000),
            seq=self._seq,
            source="cloud_manager",
            topic="cloud",
            payload=payload,
        )
        self._seq += 1

        try:
            self._cloud_pub.send_string("cloud", zmq.SNDMORE | zmq.NOBLOCK)
            self._cloud_pub.send(raw, zmq.NOBLOCK)
        except zmq.ZMQError as exc:
            logger.warning("ZMQ cloud status publish failed: %s", exc)
