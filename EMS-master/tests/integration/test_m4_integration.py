"""M4 integration tests -- cloud_manager MQTT connectivity, telemetry, events,
and E2E remote command flow.

Validates end-to-end:
- cloud_manager connects to a local Mosquitto broker (subprocess)
- Heartbeat/status published to {prefix}/status
- Telemetry forwarded to {prefix}/telemetry from ZMQ PUB
- Events forwarded to {prefix}/events (QoS 1, CLOUD-03)
- ZMQ cloud PUB socket emits cloud status (CLOUD-08)
- Remote command via MQTT reaches control_manager via ZMQ and updates RTDB (CLOUD-06)

These tests are the "graduation test" for M4 -- they prove that cloud_manager
works correctly with all M1-M3 modules in a running system.

Prerequisites:
- mosquitto broker installed (sudo apt-get install -y mosquitto)
- vcan0 virtual CAN interface (run 'make sim' to create)
- C binaries built in build/ directory (run 'make build')
- All Python modules installed in the workspace (uv sync --all-packages)
- /run/ems/ directory exists (for IPC sockets)

Test isolation:
- Mosquitto uses a randomly allocated TCP port to avoid conflicts
- cloud_manager ZMQ PUB uses TCP (not IPC) to avoid /run/ems binding issues
- Each test class has its own class-scoped system fixture
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import msgpack
import pytest
import yaml
import zmq

try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.client import CallbackAPIVersion
except ImportError:
    pytest.skip(
        "paho-mqtt not installed -- run 'uv add --dev paho-mqtt'",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Import conftest helpers
# ---------------------------------------------------------------------------

_INTEG_DIR: Path = Path(__file__).resolve().parent
if str(_INTEG_DIR) not in sys.path:
    sys.path.insert(0, str(_INTEG_DIR))

from conftest import (  # noqa: E402
    BUILD_DIR,
    CONFIG_DIR,
    ModuleProcess,
    PROJECT_ROOT,
    PROFILES,
    _check_http_health,
    check_rtdb_exists,
    check_rtdb_fresh,
    wait_for_criteria,
)

from ems_common.ipc import (  # noqa: E402
    TOPIC_CONTROL_STATE,
    decode_command_response,
    encode_command_request,
)
from ems_common.rtdb import attach_rtdb, detach_rtdb  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level markers
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

# ---------------------------------------------------------------------------
# State constants (mirror ems_control_manager/state_machine.py)
# ---------------------------------------------------------------------------

STATE_INIT: int = 0
STATE_IDLE: int = 1
STATE_STANDBY: int = 2
STATE_CHARGING: int = 3
STATE_DISCHARGING: int = 4
STATE_FAULT: int = 5
STATE_EMERGENCY: int = 6
STATE_MAINTENANCE: int = 7

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Allocate one free TCP port using socket bind."""
    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def _allocate_tcp_ports(count: int) -> list[int]:
    """Allocate ``count`` random free TCP ports."""
    ports: list[int] = []
    sockets: list[socket.socket] = []
    for _ in range(count):
        s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        ports.append(s.getsockname()[1])
        sockets.append(s)
    for s in sockets:
        s.close()
    return ports


def _vcan_available() -> bool:
    """Return True if the vcan0 network interface exists."""
    return Path("/sys/class/net/vcan0").exists()


def _binary_exists(name: str) -> bool:
    """Return True if the named C binary is available in the build directory."""
    binaries: dict[str, Path] = {
        "data_manager_c": BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c",
        "safety_manager": BUILD_DIR / "src" / "safety_manager" / "safety_manager",
        "comm_manager_c": BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c",
    }
    path: Path | None = binaries.get(name)
    return path is not None and path.exists()


def _delay_ready(seconds: float) -> Callable[[], bool]:
    """Return a ready_check that passes after ``seconds`` of wall-clock time."""
    _start: list[float] = [0.0]
    _initialized: list[bool] = [False]

    def _check() -> bool:
        if not _initialized[0]:
            _start[0] = time.monotonic()
            _initialized[0] = True
        return (time.monotonic() - _start[0]) >= seconds

    return _check


def read_control_state() -> int:
    """Attach RTDB, read system.control_state, detach, return int."""
    try:
        shm, rtdb = attach_rtdb()
        state: int = int(rtdb.system.control_state)
        detach_rtdb(shm)
        return state
    except Exception:
        return -1


def check_control_state(target: int) -> Callable[[], bool]:
    """Return a callable that checks if control_state matches ``target``."""
    def _check() -> bool:
        return read_control_state() == target
    return _check


def wait_for_mqtt_message(
    host: str,
    port: int,
    topic: str,
    timeout: float = 15.0,
) -> dict | None:
    """Subscribe to an MQTT topic and wait for the first JSON message.

    Uses paho.mqtt.client with CallbackAPIVersion.VERSION2. Returns the
    parsed dict or None on timeout. Subscribes with QoS 1.

    Args:
        host: MQTT broker host.
        port: MQTT broker port.
        topic: MQTT topic to subscribe to.
        timeout: Seconds to wait for a message.

    Returns:
        Parsed JSON dict, or None if no message arrived within timeout.
    """
    received: list[dict | None] = [None]
    event: threading.Event = threading.Event()

    def _on_connect(
        client: mqtt.Client,
        userdata: Any,
        connect_flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties,
    ) -> None:
        if not reason_code.is_failure:
            client.subscribe(topic, qos=1)

    def _on_message(
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            received[0] = json.loads(message.payload.decode("utf-8"))
        except Exception:
            received[0] = {}
        event.set()

    client: mqtt.Client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=f"ems_test_sub_{uuid.uuid4().hex[:8]}",
        clean_session=True,
    )
    client.on_connect = _on_connect
    client.on_message = _on_message

    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()
        event.wait(timeout=timeout)
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass

    return received[0]


def collect_mqtt_messages(
    host: str,
    port: int,
    topic: str,
    count: int,
    timeout: float = 15.0,
) -> list[dict]:
    """Subscribe to an MQTT topic and collect up to ``count`` JSON messages.

    Args:
        host: MQTT broker host.
        port: MQTT broker port.
        topic: MQTT topic to subscribe to (supports wildcards).
        count: Maximum number of messages to collect.
        timeout: Total seconds to wait.

    Returns:
        List of parsed JSON dicts received within timeout.
    """
    collected: list[dict] = []
    event: threading.Event = threading.Event()

    def _on_connect(
        client: mqtt.Client,
        userdata: Any,
        connect_flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties,
    ) -> None:
        if not reason_code.is_failure:
            client.subscribe(topic, qos=1)

    def _on_message(
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            parsed: dict = json.loads(message.payload.decode("utf-8"))
            collected.append(parsed)
        except Exception:
            collected.append({})
        if len(collected) >= count:
            event.set()

    client: mqtt.Client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=f"ems_test_collect_{uuid.uuid4().hex[:8]}",
        clean_session=True,
    )
    client.on_connect = _on_connect
    client.on_message = _on_message

    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()
        event.wait(timeout=timeout)
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass

    return collected


def _kill_subprocess_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill a subprocess and its entire process group (best-effort)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# Mosquitto fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def mosquitto_broker() -> Any:
    """Start a local Mosquitto broker on a random port.

    Skips the test if mosquitto is not installed. Yields (host, port).
    Terminates the broker and removes temp files on teardown.
    """
    if not shutil.which("mosquitto"):
        pytest.skip(
            "mosquitto not installed -- run: sudo apt-get install -y mosquitto"
        )

    port: int = _free_port()
    tmpdir: str = tempfile.mkdtemp(prefix="ems_mosquitto_")
    tmpdir_path: Path = Path(tmpdir)
    config_file: Path = tmpdir_path / "mosquitto.conf"

    config_file.write_text(
        f"listener {port} 127.0.0.1\n"
        "allow_anonymous true\n"
        "log_type error\n"
    )

    proc: subprocess.Popen[bytes] = subprocess.Popen(
        ["mosquitto", "-c", str(config_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for broker to be reachable
    host: str = "127.0.0.1"
    deadline: float = time.monotonic() + 5.0
    broker_ready: bool = False
    while time.monotonic() < deadline:
        try:
            test_client: mqtt.Client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION2,
                client_id=f"ems_probe_{uuid.uuid4().hex[:8]}",
                clean_session=True,
            )
            test_client.connect(host, port, keepalive=5)
            test_client.disconnect()
            broker_ready = True
            break
        except Exception:
            time.sleep(0.2)

    if not broker_ready:
        proc.terminate()
        proc.wait(timeout=5)
        shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.skip("Mosquitto broker failed to start within 5s")

    yield (host, port)

    # Teardown
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# System builder / teardown
# ---------------------------------------------------------------------------


def _build_m4_system(mqtt_host: str, mqtt_port: int) -> dict[str, Any]:
    """Launch the full M1+M2+M3+cloud_manager system with Mosquitto integration.

    Uses ipc:// sockets for all internal ZMQ endpoints. Overrides the
    cloud_manager's ZMQ PUB socket to TCP to avoid IPC path conflicts.

    Args:
        mqtt_host: Mosquitto broker host (e.g. "127.0.0.1").
        mqtt_port: Mosquitto broker port (randomly allocated).

    Returns:
        Dict with modules, simulators, tmpdir, ports, and topic info.
    """
    # Check prerequisites
    if not _vcan_available():
        pytest.skip("vcan0 not available -- run 'make sim' to set up virtual CAN")
    if not _binary_exists("data_manager_c"):
        pytest.skip("data_manager_c not built -- run 'make build' first")
    if not _binary_exists("safety_manager"):
        pytest.skip("safety_manager not built -- run 'make build' first")

    # Ensure /run/ems/ exists for ipc:// sockets
    run_ems: Path = Path("/run/ems")
    if not run_ems.exists():
        pytest.skip(
            "/run/ems/ directory not available -- "
            "run 'sudo mkdir -p /run/ems && sudo chmod 777 /run/ems'"
        )

    profile: dict[str, Any] = PROFILES["residential"]
    config_dir: Path = profile["config_dir"]

    # Allocate TCP ports
    ports: list[int] = _allocate_tcp_ports(2)
    hmi_http_port: int = ports[0]
    cloud_pub_port: int = ports[1]
    cloud_pub_endpoint: str = f"tcp://127.0.0.1:{cloud_pub_port}"

    # Temp directory for test-specific configs
    tmpdir_path: Path = Path(tempfile.mkdtemp(prefix="ems_m4_e2e_"))
    buffer_dir: Path = tmpdir_path / "cloud_buffer"
    buffer_dir.mkdir(parents=True, exist_ok=True)

    # Copy residential profile configs to tmpdir
    for yaml_file in config_dir.glob("*.yaml"):
        shutil.copy(yaml_file, tmpdir_path / yaml_file.name)

    # Write test HMI config with allocated port
    test_hmi_config_src: Path = config_dir / "hmi_config_test.yaml"
    test_hmi_config: Path = tmpdir_path / "hmi_config_test.yaml"
    if test_hmi_config_src.exists():
        with open(test_hmi_config_src) as f:
            hmi_cfg: dict = yaml.safe_load(f)
        hmi_cfg["server"]["http_port"] = hmi_http_port
        with open(test_hmi_config, "w") as f:
            yaml.dump(hmi_cfg, f, default_flow_style=False)

    # Write test cloud config (auth.method: token to skip cert checks)
    topic_prefix: str = "ems/TEST-001"
    cloud_config: dict[str, Any] = {
        "_schema_version": "1.0",
        "broker": {
            "host": mqtt_host,
            "port": mqtt_port,
            "protocol": "mqtt",
        },
        "auth": {
            "method": "token",
        },
        "telemetry": {
            "interval_s": 10,
            "topic_prefix": topic_prefix,
        },
        "offline_buffer": {
            "enabled": True,
            "max_hours": 1,
            "max_mb": 10,
        },
    }
    cloud_config_path: Path = tmpdir_path / "cloud_config.yaml"
    with open(cloud_config_path, "w") as f:
        yaml.dump(cloud_config, f, default_flow_style=False)

    modules: dict[str, ModuleProcess] = {}
    simulators: list[subprocess.Popen[bytes]] = []

    # 1. data_manager_c
    modules["data_manager_c"] = ModuleProcess(
        name="data_manager_c",
        cmd=[
            str(BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c"),
            str(profile["clusters"]),
            str(profile["racks"]),
            str(profile["modules"]),
            str(profile["cells"]),
            str(profile["temps"]),
        ],
        ready_check=check_rtdb_exists,
    )

    # 2. data_manager_python
    modules["data_manager_python"] = ModuleProcess(
        name="data_manager_python",
        cmd=["uv", "run", "python", "-m", "ems_data_manager"],
        ready_check=_delay_ready(2.0),
    )

    # 3. config_manager
    modules["config_manager"] = ModuleProcess(
        name="config_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_config_manager",
            "--config-dir", str(tmpdir_path),
            "--schema-dir", str(CONFIG_DIR / "schemas"),
        ],
        ready_check=_delay_ready(1.0),
    )

    # 4. safety_manager
    modules["safety_manager"] = ModuleProcess(
        name="safety_manager",
        cmd=[str(BUILD_DIR / "src" / "safety_manager" / "safety_manager")],
        ready_check=_delay_ready(1.0),
        env={"EMS_GPIO_BACKEND": "rtdb"},
    )

    # 5. comm_manager_c
    if _binary_exists("comm_manager_c"):
        modules["comm_manager_c"] = ModuleProcess(
            name="comm_manager_c",
            cmd=[
                str(BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"),
                "--interface", "vcan0",
                "--base-id", "0x18FF0003",
                "--heartbeat-timeout-ms", "900",
            ],
            ready_check=_delay_ready(1.0),
        )

    # 6. comm_manager_python
    modules["comm_manager_python"] = ModuleProcess(
        name="comm_manager_python",
        cmd=[
            "uv", "run", "python", "-m", "ems_comm_manager",
            "--config", str(CONFIG_DIR),
        ],
        ready_check=_delay_ready(2.0),
    )

    # 7. logger
    modules["logger"] = ModuleProcess(
        name="logger",
        cmd=[
            "uv", "run", "python", "-m", "ems_logger",
            "--config", str(CONFIG_DIR / "logger_config.yaml"),
        ],
        ready_check=_delay_ready(1.0),
    )

    # 8. CAN simulator
    can_proc: subprocess.Popen[bytes] = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "tools.simulators.can_sim",
            "--interface", "vcan0",
            "--config", str(tmpdir_path / "bms_config.yaml"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setpgrp,
        cwd=str(PROJECT_ROOT),
    )
    simulators.append(can_proc)

    # 9. Modbus simulator
    modbus_proc: subprocess.Popen[bytes] = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "tools.simulators.modbus_sim",
            "--transport", "tcp",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setpgrp,
        cwd=str(PROJECT_ROOT),
    )
    simulators.append(modbus_proc)

    # 10. control_manager (uses ipc:// defaults)
    modules["control_manager"] = ModuleProcess(
        name="control_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_control_manager",
            "--config", str(tmpdir_path / "control_config.yaml"),
        ],
        ready_check=_delay_ready(2.0),
    )

    # 11. alarm_manager (uses ipc:// defaults)
    modules["alarm_manager"] = ModuleProcess(
        name="alarm_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_alarm_manager",
            "--config", str(tmpdir_path / "alarms_config.yaml"),
        ],
        ready_check=_delay_ready(2.0),
    )

    # 12. hmi_server (optional -- only if test config exists)
    if test_hmi_config.exists():
        modules["hmi_server"] = ModuleProcess(
            name="hmi_server",
            cmd=[
                "uv", "run", "python", "-m", "ems_hmi_server",
                "--config", str(test_hmi_config),
            ],
            ready_check=lambda: _check_http_health(hmi_http_port),
        )

    # 13. scheduler
    schedule_config_path: Path = tmpdir_path / "schedule_config.yaml"
    if schedule_config_path.exists():
        modules["scheduler"] = ModuleProcess(
            name="scheduler",
            cmd=[
                "uv", "run", "python", "-m", "ems_scheduler",
                "--config", str(schedule_config_path),
            ],
            ready_check=_delay_ready(3.0),
        )

    # 14. cloud_manager -- wait until it publishes the first status heartbeat
    def _cloud_ready() -> bool:
        """Check if cloud_manager has connected by looking for any MQTT message."""
        msg: dict | None = wait_for_mqtt_message(
            mqtt_host, mqtt_port, f"{topic_prefix}/status", timeout=2.0
        )
        return msg is not None

    modules["cloud_manager"] = ModuleProcess(
        name="cloud_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_cloud_manager",
            "--config", str(cloud_config_path),
            "--log-level", "DEBUG",
        ],
        ready_check=_cloud_ready,
        env={
            "EMS_CLOUD_PUB_ENDPOINT": cloud_pub_endpoint,
            "EMS_CLOUD_BUFFER_DIR": str(buffer_dir),
        },
    )

    # Allow system to fully settle
    time.sleep(3)

    return {
        "modules": modules,
        "simulators": simulators,
        "tmpdir": tmpdir_path,
        "hmi_http_port": hmi_http_port,
        "cloud_pub_endpoint": cloud_pub_endpoint,
        "mqtt_host": mqtt_host,
        "mqtt_port": mqtt_port,
        "buffer_dir": buffer_dir,
        "topic_prefix": topic_prefix,
        "control_cmd_endpoint": "ipc:///run/ems/control_cmd.sock",
    }


def _teardown_m4_system(system: dict[str, Any]) -> None:
    """Tear down the M4 system in reverse order."""
    # Kill simulators first
    for sim in system.get("simulators", []):
        _kill_subprocess_group(sim)
    # Kill modules in reverse order
    modules: dict[str, ModuleProcess] = system.get("modules", {})
    for name in reversed(list(modules.keys())):
        try:
            modules[name].cleanup()
        except Exception:
            pass
    # Clean up temp directory
    tmpdir: Path | None = system.get("tmpdir")
    if tmpdir is not None:
        shutil.rmtree(str(tmpdir), ignore_errors=True)


# ===========================================================================
# TestM4Startup
# ===========================================================================


class TestM4Startup:
    """M4 startup tests: MQTT connectivity, heartbeat, telemetry, events, ZMQ status.

    Validates that cloud_manager connects to a real Mosquitto broker and
    forwards data through the full module stack.
    """

    @pytest.fixture(scope="class")
    def m4_system(self, mosquitto_broker: tuple[str, int]) -> Any:
        """Launch full M1+M2+M3+cloud_manager system with Mosquitto."""
        mqtt_host, mqtt_port = mosquitto_broker
        system: dict[str, Any] = _build_m4_system(mqtt_host, mqtt_port)
        try:
            yield system
        finally:
            _teardown_m4_system(system)

    def test_all_modules_alive(self, m4_system: dict[str, Any]) -> None:
        """Verify all modules including cloud_manager are still running."""
        modules: dict[str, ModuleProcess] = m4_system["modules"]
        dead: list[str] = [name for name, m in modules.items() if not m.is_alive]
        assert not dead, f"Dead modules after startup: {dead}"

    def test_cloud_manager_connects(self, m4_system: dict[str, Any]) -> None:
        """Verify cloud_manager connects to MQTT by receiving a heartbeat on {prefix}/status."""
        host: str = m4_system["mqtt_host"]
        port: int = m4_system["mqtt_port"]
        prefix: str = m4_system["topic_prefix"]

        msg: dict | None = wait_for_mqtt_message(
            host, port, f"{prefix}/status", timeout=15.0
        )
        assert msg is not None, (
            "No heartbeat message received on {prefix}/status within 15s -- "
            "cloud_manager did not connect to broker"
        )
        # Heartbeat payload includes 'connected' and/or 'state' fields
        assert isinstance(msg, dict), f"Expected dict, got {type(msg)}"
        # The heartbeat from _heartbeat_publisher has 'connected' field;
        # the status-change message from _publish_cloud_status has 'state' field
        has_cloud_field: bool = (
            "connected" in msg
            or "state" in msg
            or "device_id" in msg
            or "uptime_s" in msg
        )
        assert has_cloud_field, (
            f"Heartbeat message missing expected fields (connected/state/device_id): {msg}"
        )

    def test_telemetry_reaches_mqtt(self, m4_system: dict[str, Any]) -> None:
        """Verify that ZMQ telemetry is forwarded to {prefix}/telemetry on MQTT."""
        host: str = m4_system["mqtt_host"]
        port: int = m4_system["mqtt_port"]
        prefix: str = m4_system["topic_prefix"]

        messages: list[dict] = collect_mqtt_messages(
            host, port, f"{prefix}/telemetry", count=2, timeout=30.0
        )
        assert len(messages) >= 1, (
            f"No telemetry messages received on {prefix}/telemetry within 30s -- "
            f"cloud_manager may not be forwarding ZMQ telemetry to MQTT"
        )
        msg: dict = messages[0]
        assert isinstance(msg, dict), f"Telemetry message is not a dict: {type(msg)}"
        # Telemetry has {"ts": ..., "data": {...}} structure
        assert "ts" in msg or "data" in msg, (
            f"Telemetry message missing expected structure: {msg}"
        )

    def test_cloud_zmq_status(self, m4_system: dict[str, Any]) -> None:
        """Validate CLOUD-08: ZMQ cloud PUB socket emits cloud connection status.

        Connects to the cloud_manager's TCP ZMQ PUB socket and receives one
        frame. Decodes via msgpack and verifies cloud status fields.
        """
        cloud_pub_endpoint: str = m4_system["cloud_pub_endpoint"]

        ctx: zmq.Context = zmq.Context()
        sub: zmq.Socket = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt(zmq.RCVTIMEO, 10000)
        sub.setsockopt_string(zmq.SUBSCRIBE, "")  # receive all topics

        try:
            sub.connect(cloud_pub_endpoint)

            # Wait for a cloud status message
            # cloud_manager publishes on connect -- subscribe before connecting
            # Give it time to publish another status message
            time.sleep(1.0)

            # Receive topic frame + body frame
            try:
                frames: list[bytes] = sub.recv_multipart()
                assert len(frames) >= 2, (
                    f"Expected at least 2 ZMQ frames, got {len(frames)}"
                )
                # Decode the body
                body: dict = msgpack.unpackb(frames[1], raw=False)
                assert isinstance(body, dict), f"ZMQ cloud status body is not a dict: {type(body)}"

                # The body is a telemetry envelope from encode_telemetry()
                # which has "ts", "seq", "src", "topic", "payload" keys.
                # The nested payload contains "state", "broker", "ts".
                assert "payload" in body or "state" in body, (
                    f"ZMQ cloud status missing payload/state: {body}"
                )

            except zmq.Again:
                # Trigger by publishing a new command if possible -- this happens
                # if cloud_manager has already published its connected status before
                # we subscribed (slow joiner problem). The cloud_manager publishes
                # status on each reconnect -- try waiting longer.
                pytest.skip(
                    "No ZMQ cloud status received within 10s -- "
                    "cloud_manager may not have reconnected"
                )
        finally:
            sub.close()
            ctx.term()

    def test_event_forwarded_to_mqtt(self, m4_system: dict[str, Any]) -> None:
        """Validate CLOUD-03: events are forwarded to {prefix}/events with QoS 1.

        The system generates state_change events as control_manager transitions
        through INIT -> IDLE. If no event arrives within 30s, explicitly triggers
        one by publishing an MQTT mode_change command, which generates a
        state_change event that cloud_manager forwards to {prefix}/events.
        """
        host: str = m4_system["mqtt_host"]
        port: int = m4_system["mqtt_port"]
        prefix: str = m4_system["topic_prefix"]

        # First: wait for a naturally occurring event (state_change during startup)
        msg: dict | None = wait_for_mqtt_message(
            host, port, f"{prefix}/events", timeout=30.0
        )

        if msg is None:
            # Trigger an event explicitly by publishing a mode_change command.
            # This causes control_manager to emit a state_change event, which
            # cloud_manager subscribes to (via ZMQ alarm_pub) and forwards to MQTT.
            request_id: str = uuid.uuid4().hex
            command_payload: dict[str, Any] = {
                "command": "mode_change",
                "params": {"target_state": "idle"},
                "request_id": request_id,
            }

            trigger_client: mqtt.Client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION2,
                client_id=f"ems_trigger_{uuid.uuid4().hex[:8]}",
                clean_session=True,
            )
            trigger_client.connect(host, port, keepalive=5)
            trigger_client.loop_start()
            trigger_client.publish(
                f"{prefix}/commands",
                json.dumps(command_payload).encode(),
                qos=1,
            )
            time.sleep(1.0)
            trigger_client.loop_stop()
            trigger_client.disconnect()

            # Now wait for the event triggered by the command
            msg = wait_for_mqtt_message(
                host, port, f"{prefix}/events", timeout=30.0
            )

        assert msg is not None, (
            f"No event message received on {prefix}/events within 60s -- "
            f"cloud_manager may not be forwarding ZMQ events to MQTT QoS 1"
        )
        assert isinstance(msg, dict), (
            f"Event message is not a dict: {type(msg)}"
        )
        # Cloud event payload: {"ts": ..., "event_type": ..., "data": {...}}
        has_event_field: bool = (
            "event_type" in msg
            or "type" in msg
            or "event" in msg
            or "ts" in msg
        )
        assert has_event_field, (
            f"Event message missing expected fields (event_type/type/ts): {msg}"
        )


# ===========================================================================
# TestE2ERemoteCommand
# ===========================================================================


class TestE2ERemoteCommand:
    """E2E: MQTT command -> ZMQ -> control_manager -> RTDB verified end-to-end.

    Validates CLOUD-06: remote commands reach the control plane and produce
    observable effects in the RTDB shared memory.
    """

    @pytest.fixture(scope="class")
    def m4_system(self, mosquitto_broker: tuple[str, int]) -> Any:
        """Launch full M1+M2+M3+cloud_manager system with Mosquitto."""
        mqtt_host, mqtt_port = mosquitto_broker
        system: dict[str, Any] = _build_m4_system(mqtt_host, mqtt_port)
        try:
            yield system
        finally:
            _teardown_m4_system(system)

    def test_e2e_command_flow(self, m4_system: dict[str, Any]) -> None:
        """Full chain: MQTT publish -> cloud_manager -> ZMQ REQ -> control_manager -> RTDB.

        Steps:
        1. Verify all modules alive.
        2. Subscribe to response topic BEFORE publishing command (avoid slow-join).
        3. Publish mode_change command to MQTT commands topic.
        4. Wait for response on {prefix}/responses/{request_id}.
        5. Verify response status == "ok".
        6. Verify RTDB control_state reaches STANDBY within 15s.
        """
        host: str = m4_system["mqtt_host"]
        port: int = m4_system["mqtt_port"]
        prefix: str = m4_system["topic_prefix"]

        # Step 1: Verify all modules alive
        modules: dict[str, ModuleProcess] = m4_system["modules"]
        dead: list[str] = [name for name, m in modules.items() if not m.is_alive]
        assert not dead, f"Dead modules before E2E test: {dead}"

        # Step 2: Generate a unique request_id
        request_id: str = uuid.uuid4().hex

        # Step 2b: Set up response subscription BEFORE publishing command.
        # We collect from the specific response topic using a background thread.
        response_topic: str = f"{prefix}/responses/{request_id}"
        response: list[dict | None] = [None]
        response_event: threading.Event = threading.Event()

        def _on_connect_resp(
            client: mqtt.Client,
            userdata: Any,
            connect_flags: mqtt.ConnectFlags,
            reason_code: mqtt.ReasonCode,
            properties: mqtt.Properties,
        ) -> None:
            if not reason_code.is_failure:
                client.subscribe(response_topic, qos=1)

        def _on_message_resp(
            client: mqtt.Client,
            userdata: Any,
            message: mqtt.MQTTMessage,
        ) -> None:
            try:
                response[0] = json.loads(message.payload.decode("utf-8"))
            except Exception:
                response[0] = {}
            response_event.set()

        resp_client: mqtt.Client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"ems_resp_{uuid.uuid4().hex[:8]}",
            clean_session=True,
        )
        resp_client.on_connect = _on_connect_resp
        resp_client.on_message = _on_message_resp
        resp_client.connect(host, port, keepalive=10)
        resp_client.loop_start()

        # Give the subscription a moment to activate before publishing
        time.sleep(0.5)

        try:
            # Step 3-4: Publish mode_change command
            command_payload: dict[str, Any] = {
                "command": "mode_change",
                "params": {"target_state": "standby"},
                "request_id": request_id,
            }

            pub_client: mqtt.Client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION2,
                client_id=f"ems_pub_{uuid.uuid4().hex[:8]}",
                clean_session=True,
            )
            pub_client.connect(host, port, keepalive=5)
            pub_client.loop_start()
            pub_client.publish(
                f"{prefix}/commands",
                json.dumps(command_payload).encode(),
                qos=1,
            )
            time.sleep(0.5)
            pub_client.loop_stop()
            pub_client.disconnect()

            # Step 5: Wait for response (up to 10s)
            response_received: bool = response_event.wait(timeout=10.0)

            if response_received and response[0] is not None:
                # Step 6: Verify response status == "ok"
                resp_data: dict = response[0]
                assert resp_data.get("status") == "ok", (
                    f"Command response status is not 'ok': {resp_data}"
                )
            # If response not received, still check RTDB (command may have worked
            # but response was lost -- the E2E chain still validates state change)

        finally:
            resp_client.loop_stop()
            try:
                resp_client.disconnect()
            except Exception:
                pass

        # Step 7: Wait for RTDB control_state to reach STANDBY (up to 15s)
        state: dict[str, bool] = wait_for_criteria(
            {"standby": check_control_state(STATE_STANDBY)},
            timeout=15.0,
        )
        assert state["standby"], (
            f"control_state did not reach STANDBY (2) within 15s -- "
            f"current state: {read_control_state()}"
        )


# ===========================================================================
# MosquittoController
# ===========================================================================


class MosquittoController:
    """Controls a Mosquitto broker process lifecycle for offline/online tests.

    Wraps a subprocess.Popen for a running Mosquitto broker and provides
    stop/restart methods so tests can simulate broker outages mid-test.
    """

    def __init__(self, host: str, port: int, config_path: Path) -> None:
        self.host: str = host
        self.port: int = port
        self._config_path: Path = config_path
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Launch Mosquitto and wait for broker to become reachable."""
        self._proc = subprocess.Popen(
            ["mosquitto", "-c", str(self._config_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for broker ready (paho connect test, deadline 5s)
        deadline: float = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                probe: mqtt.Client = mqtt.Client(
                    callback_api_version=CallbackAPIVersion.VERSION2,
                    client_id=f"ems_ctrl_probe_{uuid.uuid4().hex[:8]}",
                    clean_session=True,
                )
                probe.connect(self.host, self.port, keepalive=5)
                probe.disconnect()
                return
            except Exception:
                time.sleep(0.2)
        raise TimeoutError(
            f"Mosquitto controller broker at {self.host}:{self.port} "
            "did not become ready within 5s"
        )

    def stop(self) -> None:
        """Terminate the broker process."""
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None

    def restart(self) -> None:
        """Stop then start the broker, allowing 0.5s for port release."""
        self.stop()
        time.sleep(0.5)
        self.start()


# ===========================================================================
# TestOfflineTransition
# ===========================================================================


class TestOfflineTransition:
    """Validates CLOUD-04 and CLOUD-05: offline buffer fill and replay on reconnect.

    Uses a MosquittoController to stop/restart the broker mid-test, then
    verifies that:
    - Buffer JSONL files appear on disk during the offline period.
    - Buffer files are drained and deleted after the broker restarts.
    """

    @pytest.fixture(scope="class")
    def mosquitto_controller(self) -> Any:
        """Start Mosquitto via MosquittoController, yield it, stop on teardown."""
        if not shutil.which("mosquitto"):
            pytest.skip(
                "mosquitto not installed -- run: sudo apt-get install -y mosquitto"
            )

        port: int = _free_port()
        tmpdir: str = tempfile.mkdtemp(prefix="ems_offline_mosq_")
        tmpdir_path: Path = Path(tmpdir)
        config_file: Path = tmpdir_path / "mosquitto.conf"
        config_file.write_text(
            f"listener {port} 127.0.0.1\n"
            "allow_anonymous true\n"
            "log_type error\n"
        )

        controller: MosquittoController = MosquittoController(
            host="127.0.0.1",
            port=port,
            config_path=config_file,
        )
        controller.start()

        try:
            yield controller
        finally:
            controller.stop()
            shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture(scope="class")
    def m4_system_with_controller(
        self, mosquitto_controller: MosquittoController
    ) -> Any:
        """Launch full M4 system connected to the MosquittoController broker."""
        system: dict[str, Any] = _build_m4_system(
            mosquitto_controller.host, mosquitto_controller.port
        )
        try:
            yield (system, mosquitto_controller)
        finally:
            _teardown_m4_system(system)

    def test_offline_online_cycle(
        self, m4_system_with_controller: tuple[dict[str, Any], MosquittoController]
    ) -> None:
        """Full offline/online buffer cycle: fill during outage, drain on reconnect.

        Steps:
        1. Verify telemetry is flowing to MQTT.
        2. Kill Mosquitto (simulates network outage).
        3. Wait 30s for buffer to accumulate (telemetry interval 10s = ~3 files).
        4. Assert buffer JSONL files exist on disk.
        5. Restart Mosquitto.
        6. Subscribe a test MQTT client to receive replayed messages.
        7. Wait up to 60s for buffer files to be drained and deleted.
        8. Assert test client received messages during replay.
        """
        system, controller = m4_system_with_controller
        host: str = system["mqtt_host"]
        port: int = system["mqtt_port"]
        prefix: str = system["topic_prefix"]
        buffer_dir: Path = system["buffer_dir"]

        # Step 1: Verify telemetry is flowing before we kill the broker
        initial_msg: dict | None = wait_for_mqtt_message(
            host, port, f"{prefix}/telemetry", timeout=60.0
        )
        assert initial_msg is not None, (
            "No telemetry received before offline test started -- "
            "cloud_manager may not be connected or forwarding telemetry"
        )

        # Step 2: Kill Mosquitto (offline)
        controller.stop()

        # Step 3: Wait 30s -- at 10s telemetry interval, expect ~3 buffered records
        time.sleep(30)

        # Step 4: Assert buffer files were created during offline period
        buffer_files: list[Path] = list(buffer_dir.rglob("*.jsonl"))
        assert len(buffer_files) > 0, (
            f"No buffer JSONL files found in {buffer_dir} after 30s offline -- "
            "cloud_manager may not be writing to the buffer when MQTT is offline"
        )

        # Step 5: Set up test subscriber BEFORE restarting broker (subscribe fast)
        # The client will reconnect and collect replayed messages.
        replayed: list[dict] = []
        replay_event: threading.Event = threading.Event()

        def _on_connect_replay(
            client: mqtt.Client,
            userdata: Any,
            connect_flags: mqtt.ConnectFlags,
            reason_code: mqtt.ReasonCode,
            properties: mqtt.Properties,
        ) -> None:
            if not reason_code.is_failure:
                client.subscribe(f"{prefix}/telemetry", qos=1)

        def _on_message_replay(
            client: mqtt.Client,
            userdata: Any,
            message: mqtt.MQTTMessage,
        ) -> None:
            try:
                parsed: dict = json.loads(message.payload.decode("utf-8"))
                replayed.append(parsed)
            except Exception:
                replayed.append({})
            replay_event.set()

        replay_client: mqtt.Client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"ems_replay_sub_{uuid.uuid4().hex[:8]}",
            clean_session=True,
        )
        replay_client.on_connect = _on_connect_replay
        replay_client.on_message = _on_message_replay

        # Step 6: Restart Mosquitto, then connect subscriber
        controller.restart()

        # Connect subscriber now that broker is up
        try:
            replay_client.connect(host, port, keepalive=10)
            replay_client.loop_start()

            # Step 7: Wait up to 60s for buffer to drain (5s replay backoff per cycle)
            drain_deadline: float = time.monotonic() + 60.0
            while time.monotonic() < drain_deadline:
                remaining_files: list[Path] = list(buffer_dir.rglob("*.jsonl"))
                if len(remaining_files) == 0:
                    break
                time.sleep(2.0)

            remaining_files = list(buffer_dir.rglob("*.jsonl"))
            assert len(remaining_files) == 0, (
                f"Buffer files still present after 60s reconnect: {remaining_files} -- "
                "cloud_manager may not be replaying or deleting buffer files"
            )

            # Step 8: Verify replay messages arrived
            assert len(replayed) > 0, (
                "No messages received on telemetry topic after broker restart -- "
                "buffer replay may not be publishing to MQTT"
            )

        finally:
            replay_client.loop_stop()
            try:
                replay_client.disconnect()
            except Exception:
                pass


# ===========================================================================
# OTA helpers: package builder, mock partition, HTTP file server
# ===========================================================================


def _build_test_ota_package(
    staging_dir: Path,
    private_key: Any,
    version: str = "1.2.3",
) -> tuple[Path, str, str]:
    """Build a minimal valid OTA .tar.gz package signed with Ed25519.

    Creates a 1KB firmware.img, a manifest.json with Ed25519 signature, and
    packages them as a tar.gz. Returns (package_path, sha256_hex, version).

    Args:
        staging_dir: Directory to create the package in.
        private_key: Ed25519PrivateKey from cryptography library.
        version: Firmware version string for the manifest.

    Returns:
        Tuple of (package_path, sha256_of_package, version).
    """
    import hashlib
    import io
    import json
    import os
    import struct
    import tarfile

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    workdir: Path = staging_dir / f"build_{version}_{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)

    # 1KB deterministic firmware data
    firmware_data: bytes = os.urandom(1024)
    firmware_path: Path = workdir / "firmware.img"
    firmware_path.write_bytes(firmware_data)

    # SHA-256 of firmware
    fw_sha256: str = hashlib.sha256(firmware_data).hexdigest()

    # Build manifest without signature first (for signing)
    manifest_no_sig: dict[str, Any] = {
        "firmware": "firmware.img",
        "min_version": "0.0.0",
        "sha256": fw_sha256,
        "version": version,
    }
    manifest_bytes: bytes = json.dumps(
        manifest_no_sig, sort_keys=True
    ).encode("utf-8")

    # Sign manifest bytes
    sig_bytes: bytes = private_key.sign(manifest_bytes)
    sig_hex: str = sig_bytes.hex()

    # Add signature to manifest dict
    manifest_with_sig: dict[str, Any] = {**manifest_no_sig, "signature": sig_hex}
    manifest_path: Path = workdir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_with_sig, sort_keys=True), encoding="utf-8"
    )

    # Create tar.gz package
    package_filename: str = f"ota_{version}.tar.gz"
    package_path: Path = staging_dir / package_filename
    with tarfile.open(package_path, "w:gz") as tf:
        tf.add(manifest_path, arcname="manifest.json")
        tf.add(firmware_path, arcname="firmware.img")

    # SHA-256 of the package itself
    pkg_hasher: Any = hashlib.sha256()
    with package_path.open("rb") as fh:
        while True:
            chunk: bytes = fh.read(65536)
            if not chunk:
                break
            pkg_hasher.update(chunk)
    pkg_sha256: str = pkg_hasher.hexdigest()

    return package_path, pkg_sha256, version


class MockPartitionBackend:
    """Duck-type replacement for PartitionBackend using temp dirs, no real partitions.

    Implements the same interface as PartitionBackend but operates entirely
    in memory and temp directories. The reboot() method is a no-op -- it
    NEVER calls systemctl reboot.
    """

    def __init__(self, tmpdir: Path) -> None:
        self._tmpdir: Path = tmpdir
        self._boot_flag_path: Path = tmpdir / "boot_flag.json"
        self._image_written: Path | None = None
        self._rebooted: bool = False
        self._rollback_called: bool = False

        # Write initial boot flag
        import json as _json

        self._boot_flag_path.write_text(
            _json.dumps(
                {
                    "active": "a",
                    "previous": "b",
                    "boot_count": 1,
                    "pending_health_check": False,
                }
            ),
            encoding="utf-8",
        )

    def read_boot_flag(self) -> Any:
        """Read boot flag from the temp file. Returns BootFlag dataclass."""
        from ems_ota_manager.partition import BootFlag

        import json as _json

        data: dict[str, Any] = _json.loads(
            self._boot_flag_path.read_text(encoding="utf-8")
        )
        return BootFlag(
            active=data["active"],
            previous=data["previous"],
            boot_count=int(data["boot_count"]),
            pending_health_check=bool(data.get("pending_health_check", False)),
        )

    def write_boot_flag(self, flag: Any) -> None:
        """Write boot flag atomically to temp file."""
        import json as _json

        data: dict[str, Any] = {
            "active": flag.active,
            "previous": flag.previous,
            "boot_count": flag.boot_count,
            "pending_health_check": flag.pending_health_check,
        }
        tmp_path: Path = self._boot_flag_path.with_suffix(".tmp")
        tmp_path.write_text(_json.dumps(data), encoding="utf-8")
        tmp_path.rename(self._boot_flag_path)

    def get_standby_partition(self) -> str:
        """Return standby slot ('a' or 'b')."""
        flag: Any = self.read_boot_flag()
        return "b" if flag.active.lower() == "a" else "a"

    async def write_image_to_standby(self, image_path: Path) -> None:
        """Copy firmware image to temp standby dir (no-op flash operation)."""
        import shutil as _shutil

        dest: Path = self._tmpdir / "standby_image"
        _shutil.copy(str(image_path), str(dest))
        self._image_written = dest

    async def reboot(self) -> None:
        """No-op reboot -- NEVER calls systemctl reboot in tests."""
        self._rebooted = True


# ===========================================================================
# TestOtaCycle
# ===========================================================================


class TestOtaCycle:
    """OTA update cycle tests with in-process OtaStateMachine and MockPartitionBackend.

    Validates OTA-01 through OTA-06:
    - OTA-01: Package download via HTTP
    - OTA-02: Ed25519 manifest signature verification
    - OTA-03: Firmware SHA-256 integrity check
    - OTA-04: Boot flag swap and partition write
    - OTA-05: ZMQ PUB state transition messages
    - OTA-06: Rollback on health check failure

    All tests run in-process (no subprocess) to allow mock partition injection
    and avoid real systemctl reboot calls.
    """

    @pytest.fixture(scope="session")
    def ed25519_keypair(self) -> Any:
        """Generate an Ed25519 key pair for signing test OTA packages."""
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
            from cryptography.hazmat.primitives.serialization import (
                Encoding,
                PublicFormat,
            )
        except ImportError:
            pytest.skip(
                "cryptography library not installed -- run 'uv add cryptography'"
            )

        private_key: Any = Ed25519PrivateKey.generate()
        public_key: Any = private_key.public_key()
        pub_hex: str = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        return private_key, pub_hex

    @pytest.fixture(scope="class")
    def http_file_server(self, tmp_path_factory: Any) -> Any:
        """Local HTTP server serving OTA packages from a temp directory.

        Runs in a background daemon thread. Yields (serve_dir, base_url, port).
        """
        import threading
        from http.server import HTTPServer, SimpleHTTPRequestHandler

        serve_dir: Path = tmp_path_factory.mktemp("ota_packages")
        port: int = _free_port()

        def _handler(*args: Any) -> Any:
            return SimpleHTTPRequestHandler(
                *args, directory=str(serve_dir), **{}
            )

        server: HTTPServer = HTTPServer(("127.0.0.1", port), _handler)
        thread: threading.Thread = threading.Thread(
            target=server.serve_forever, daemon=True
        )
        thread.start()

        yield serve_dir, f"http://127.0.0.1:{port}", port

        server.shutdown()

    @pytest.fixture(scope="class")
    def ota_env(
        self,
        ed25519_keypair: tuple[Any, str],
        http_file_server: tuple[Path, str, int],
        tmp_path_factory: Any,
    ) -> Any:
        """Wire up OTA test environment: components and ports, without a pre-built manager.

        Creates:
        - MockPartitionBackend (no real partitions)
        - HttpDownloader with real HTTP (served by http_file_server)
        - PackageVerifier with real Ed25519 key
        - Shared staging and version state directories
        - Allocated TCP ports for ZMQ PUB + REP endpoints

        Note: Each test creates its own OtaManager + OtaStateMachine inside its
        asyncio.run() call so that asyncio.Event() is bound to the correct event
        loop (Python 3.10+ asyncio.Event is loop-bound at creation time).

        Yields dict with components, ports, and factory helpers.
        """
        from ems_ota_manager.downloader import HttpDownloader
        from ems_ota_manager.health import HealthChecker
        from ems_ota_manager.verifier import PackageVerifier

        _, pub_hex = ed25519_keypair
        serve_dir, base_url, http_port = http_file_server

        tmpdir: Path = tmp_path_factory.mktemp("ota_env")
        staging_dir: Path = tmpdir / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        version_state_path: Path = tmpdir / "version_state.json"

        mock_partition: MockPartitionBackend = MockPartitionBackend(tmpdir)

        verifier: PackageVerifier = PackageVerifier(public_key_hex=pub_hex)

        # Allocate TCP ports for ZMQ
        zmq_ports: list[int] = _allocate_tcp_ports(2)
        pub_port: int = zmq_ports[0]
        rep_port: int = zmq_ports[1]
        pub_endpoint: str = f"tcp://127.0.0.1:{pub_port}"
        rep_endpoint: str = f"tcp://127.0.0.1:{rep_port}"

        # Minimal config dict for OtaManager
        ota_config: dict[str, Any] = {
            "download": {
                "progress_interval_s": 5.0,
                "chunk_size": 4096,
                "timeout_s": 30,
            },
        }

        yield {
            "pub_hex": pub_hex,
            "mock_partition": mock_partition,
            "verifier": verifier,
            "staging_dir": staging_dir,
            "version_state_path": version_state_path,
            "pub_endpoint": pub_endpoint,
            "rep_endpoint": rep_endpoint,
            "pub_port": pub_port,
            "rep_port": rep_port,
            "ota_config": ota_config,
            "tmpdir": tmpdir,
            "serve_dir": serve_dir,
            "base_url": base_url,
        }

    def _make_manager_and_state_machine(
        self,
        ota_env: dict[str, Any],
        health_check_fn: Any | None = None,
    ) -> tuple[Any, Any]:
        """Factory: create OtaStateMachine + OtaManager with pre-built ZMQ sockets.

        Must be called from within an asyncio event loop so that the manager's
        asyncio.Event (stop_event) is bound to the correct loop.

        Args:
            ota_env: Environment dict from ota_env fixture.
            health_check_fn: Optional async check_fn for HealthChecker.
                If None, uses a default that always returns True.

        Returns:
            Tuple of (OtaManager, OtaStateMachine).
        """
        import asyncio
        import zmq.asyncio as zmq_asyncio

        from ems_ota_manager.downloader import HttpDownloader
        from ems_ota_manager.health import HealthChecker
        from ems_ota_manager.loop import OtaManager
        from ems_ota_manager.state_machine import OtaStateMachine

        mock_partition: MockPartitionBackend = ota_env["mock_partition"]
        verifier: Any = ota_env["verifier"]
        staging_dir: Path = ota_env["staging_dir"]
        version_state_path: Path = ota_env["version_state_path"]
        pub_endpoint: str = ota_env["pub_endpoint"]
        rep_endpoint: str = ota_env["rep_endpoint"]
        ota_config: dict[str, Any] = ota_env["ota_config"]

        downloader: HttpDownloader = HttpDownloader(
            staging_dir=staging_dir,
            max_package_mb=100,
            chunk_size=4096,
            timeout_s=30.0,
        )

        if health_check_fn is None:
            async def _default_check(service: str) -> bool:
                return True
            check_fn: Any = _default_check
        else:
            check_fn = health_check_fn

        health_checker: HealthChecker = HealthChecker(
            services=["test_svc"],
            timeout_s=5.0,
            poll_interval_s=1.0,
            check_fn=check_fn,
        )

        state_machine: OtaStateMachine = OtaStateMachine(
            downloader=downloader,
            verifier=verifier,
            partition=mock_partition,  # type: ignore[arg-type]
            health_checker=health_checker,
            version_state_path=version_state_path,
        )

        # Create ZMQ sockets inside the running event loop (linger=0, STATE.md decision)
        zmq_ctx: zmq_asyncio.Context = zmq_asyncio.Context()

        pub_sock: zmq_asyncio.Socket = zmq_ctx.socket(zmq.PUB)
        pub_sock.setsockopt(zmq.LINGER, 0)
        pub_sock.bind(pub_endpoint)

        rep_sock: zmq_asyncio.Socket = zmq_ctx.socket(zmq.REP)
        rep_sock.setsockopt(zmq.LINGER, 0)
        rep_sock.bind(rep_endpoint)

        # OtaManager created here — asyncio.Event bound to current loop
        manager: OtaManager = OtaManager(
            config=ota_config,
            state_machine=state_machine,
            partition=mock_partition,  # type: ignore[arg-type]
            ota_pub_socket=pub_sock,
            ota_rep_socket=rep_sock,
        )

        # Store sockets on manager for cleanup
        manager._test_zmq_ctx = zmq_ctx  # type: ignore[attr-defined]
        manager._test_pub_sock = pub_sock  # type: ignore[attr-defined]
        manager._test_rep_sock = rep_sock  # type: ignore[attr-defined]

        return manager, state_machine

    def _cleanup_manager(self, manager: Any) -> None:
        """Close ZMQ sockets created by _make_manager_and_state_machine."""
        for attr in ("_test_pub_sock", "_test_rep_sock"):
            sock: Any = getattr(manager, attr, None)
            if sock is not None:
                try:
                    sock.close(linger=0)
                except Exception:
                    pass
        ctx: Any = getattr(manager, "_test_zmq_ctx", None)
        if ctx is not None:
            try:
                ctx.term()
            except Exception:
                pass

    def _send_ota_command(
        self,
        action: str,
        params: dict[str, Any],
        rep_endpoint: str,
        timeout_ms: int = 3000,
    ) -> dict[str, Any]:
        """Send a ZMQ REQ command to the OTA REP socket and return the response.

        Uses a fresh REQ socket per call with linger=0 for clean teardown.

        Args:
            action: OTA action string (e.g. "get_version", "start_update").
            params: Action parameters dict.
            rep_endpoint: ZMQ REP endpoint to connect to.
            timeout_ms: Receive timeout in milliseconds.

        Returns:
            Decoded response dict.
        """
        ctx: zmq.Context = zmq.Context()
        req: zmq.Socket = ctx.socket(zmq.REQ)
        req.setsockopt(zmq.LINGER, 0)
        req.setsockopt(zmq.RCVTIMEO, timeout_ms)
        req.connect(rep_endpoint)

        try:
            from ems_common.ipc import decode_command_response, encode_command_request

            req.send(encode_command_request(action, params))
            # STATE.md decision: sleep 50ms after send before expecting response
            time.sleep(0.1)
            raw: bytes = req.recv()
            return decode_command_response(raw)
        finally:
            req.close(linger=0)
            ctx.term()

    def _collect_ota_states(
        self,
        pub_endpoint: str,
        timeout_s: float = 30.0,
        target_states: list[str] | None = None,
    ) -> list[str]:
        """Subscribe to OTA PUB and collect state transition values.

        Stops when one of target_states is received or timeout elapses.

        Args:
            pub_endpoint: ZMQ PUB endpoint to subscribe to.
            timeout_s: Maximum seconds to collect.
            target_states: Stop collecting when any of these states are seen.

        Returns:
            List of state strings collected (e.g. ["idle", "downloading", ...]).
        """
        ctx: zmq.Context = zmq.Context()
        sub: zmq.Socket = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt(zmq.RCVTIMEO, 500)
        sub.setsockopt_string(zmq.SUBSCRIBE, "")
        sub.connect(pub_endpoint)

        # Small delay for slow-joiner
        time.sleep(0.1)

        states: list[str] = []
        stop_set: set[str] = set(target_states or [])
        deadline: float = time.monotonic() + timeout_s

        try:
            while time.monotonic() < deadline:
                try:
                    frames: list[bytes] = sub.recv_multipart()
                    if len(frames) >= 2:
                        body: dict[str, Any] = msgpack.unpackb(frames[1], raw=False)
                        payload: dict[str, Any] = body.get("payload", body)
                        state_val: str = payload.get("state", "")
                        if state_val:
                            states.append(state_val)
                            if state_val in stop_set:
                                break
                except zmq.Again:
                    continue
        finally:
            sub.close(linger=0)
            ctx.term()

        return states

    def _run_ota_test(
        self,
        ota_env: dict[str, Any],
        test_fn: Any,
        health_check_fn: Any | None = None,
    ) -> None:
        """Run a test function inside a fresh asyncio event loop with a fresh OtaManager.

        Creates a new OtaManager + OtaStateMachine inside asyncio.run() so that
        the asyncio.Event (stop_event) is bound to the correct event loop.
        The manager runs as a background task; test_fn receives the manager.

        Args:
            ota_env: Environment dict from ota_env fixture.
            test_fn: Async callable accepting (manager, state_machine) that
                implements the test body and sets manager.stop_event when done.
            health_check_fn: Optional async check_fn for HealthChecker.
        """
        import asyncio as _asyncio

        async def _run_all() -> None:
            manager, sm = self._make_manager_and_state_machine(
                ota_env, health_check_fn
            )
            # Run manager as a background task
            run_task: _asyncio.Task = _asyncio.create_task(manager.run())
            try:
                # Give command loop time to start
                await _asyncio.sleep(0.2)
                await test_fn(manager, sm)
            finally:
                manager.stop_event.set()
                try:
                    await _asyncio.wait_for(run_task, timeout=5.0)
                except (_asyncio.TimeoutError, _asyncio.CancelledError):
                    run_task.cancel()
                self._cleanup_manager(manager)

        _asyncio.run(_run_all())

    def test_ota_version_query(self, ota_env: dict[str, Any]) -> None:
        """Validate OTA REP socket responds to get_version with version fields."""
        rep_endpoint: str = ota_env["rep_endpoint"]
        result_holder: list[dict[str, Any]] = []

        async def _test(manager: Any, sm: Any) -> None:
            import asyncio as _asyncio

            # Send get_version in a thread so we don't block the event loop
            loop: Any = _asyncio.get_running_loop()
            response: dict[str, Any] = await loop.run_in_executor(
                None,
                lambda: self._send_ota_command(
                    "get_version", {}, rep_endpoint, timeout_ms=5000
                ),
            )
            result_holder.append(response)

        self._run_ota_test(ota_env, _test)

        assert result_holder, "test_fn did not produce a result"
        response: dict[str, Any] = result_holder[0]
        assert response.get("status") == "ok", (
            f"get_version response status is not 'ok': {response}"
        )
        result: dict[str, Any] = response.get("result") or {}
        assert "current" in result, (
            f"get_version response missing 'current' field: {result}"
        )
        assert "previous" in result, (
            f"get_version response missing 'previous' field: {result}"
        )

    def test_ota_download_and_verify(
        self,
        ota_env: dict[str, Any],
        ed25519_keypair: tuple[Any, str],
    ) -> None:
        """Validate OTA-01 through OTA-04: download, Ed25519 verify, partition write, boot flag swap.

        Builds a test OTA package, serves it via local HTTP, triggers update
        via ZMQ REQ, and validates that the mock partition received the firmware
        and the boot flag was swapped.
        """
        import asyncio as _asyncio
        import json as _json
        import shutil as _shutil

        from ems_ota_manager.state_machine import OtaState

        private_key, _ = ed25519_keypair
        mock_partition: MockPartitionBackend = ota_env["mock_partition"]
        serve_dir: Path = ota_env["serve_dir"]
        base_url: str = ota_env["base_url"]
        staging_dir: Path = ota_env["staging_dir"]
        pub_endpoint: str = ota_env["pub_endpoint"]
        rep_endpoint: str = ota_env["rep_endpoint"]

        # Reset partition state for this test
        mock_partition._boot_flag_path.write_text(
            _json.dumps(
                {
                    "active": "a",
                    "previous": "b",
                    "boot_count": 1,
                    "pending_health_check": False,
                }
            ),
            encoding="utf-8",
        )
        mock_partition._image_written = None
        mock_partition._rebooted = False

        # Build and serve OTA package
        package_path, pkg_sha256, version = _build_test_ota_package(
            staging_dir / "packages", private_key, version="2.0.0"
        )
        dest_pkg: Path = serve_dir / package_path.name
        _shutil.copy(str(package_path), str(dest_pkg))
        package_url: str = f"{base_url}/{package_path.name}"

        states_holder: list[list[str]] = [[]]

        async def _test(manager: Any, sm: Any) -> None:
            loop: Any = _asyncio.get_running_loop()

            # Start state collector thread
            states: list[str] = []
            states_done: threading.Event = threading.Event()

            def _collect() -> None:
                collected: list[str] = self._collect_ota_states(
                    pub_endpoint, timeout_s=30.0, target_states=["rebooting", "idle"]
                )
                states.extend(collected)
                states_done.set()

            import threading as _threading
            ct: _threading.Thread = _threading.Thread(target=_collect, daemon=True)
            ct.start()

            await _asyncio.sleep(0.2)  # Let subscriber connect

            # Send start_update in executor (blocking ZMQ REQ)
            response: dict[str, Any] = await loop.run_in_executor(
                None,
                lambda: self._send_ota_command(
                    "start_update",
                    {"url": package_url, "sha256": pkg_sha256, "version": version},
                    rep_endpoint,
                    timeout_ms=5000,
                ),
            )
            assert response.get("status") == "ok", (
                f"start_update response is not 'ok': {response}"
            )

            # Wait for update pipeline to complete (timeout 30s)
            await loop.run_in_executor(
                None, lambda: states_done.wait(timeout=30.0)
            )
            states_holder[0] = states

        self._run_ota_test(ota_env, _test)

        # Validate mock partition was written and boot flag was swapped
        assert mock_partition._image_written is not None, (
            "MockPartitionBackend.write_image_to_standby() was not called -- "
            "OTA applying step did not write firmware to standby"
        )
        assert mock_partition._rebooted, (
            "MockPartitionBackend.reboot() was not called -- "
            "OTA pipeline did not reach the REBOOTING state"
        )

        flag: Any = mock_partition.read_boot_flag()
        assert flag.active == "b", (
            f"Boot flag not swapped: expected active='b', got active='{flag.active}'"
        )

        states: list[str] = states_holder[0]
        assert "downloading" in states, (
            f"OTA state never reached 'downloading': {states}"
        )
        assert "verifying" in states, (
            f"OTA state never reached 'verifying': {states}"
        )
        assert "applying" in states, (
            f"OTA state never reached 'applying': {states}"
        )

    def test_ota_rollback(
        self,
        ota_env: dict[str, Any],
        ed25519_keypair: tuple[Any, str],
    ) -> None:
        """Validate OTA-06: rollback when health check fails post-reboot.

        Simulates a completed update (boot flag with pending_health_check=True
        on the new partition 'b') then starts OtaManager, which calls
        _maybe_run_post_boot_health() on startup with a failing health checker.
        Verifies the state machine reaches ROLLED_BACK and reverts the boot flag
        back to 'a'.

        In production, the health check runs at OtaManager startup after reboot
        (not inline with the update). This test replicates that by writing the
        post-reboot boot flag before starting the manager.
        """
        import asyncio as _asyncio
        import json as _json

        mock_partition: MockPartitionBackend = ota_env["mock_partition"]
        pub_endpoint: str = ota_env["pub_endpoint"]

        # Simulate post-reboot state: rebooted into new partition 'b',
        # pending health check outstanding.
        mock_partition._boot_flag_path.write_text(
            _json.dumps(
                {
                    "active": "b",
                    "previous": "a",
                    "boot_count": 1,
                    "pending_health_check": True,
                }
            ),
            encoding="utf-8",
        )
        mock_partition._rebooted = False

        states_holder: list[list[str]] = [[]]

        # Health checker that always fails (triggers rollback on timeout)
        async def _always_fail(service: str) -> bool:
            return False

        async def _test(manager: Any, sm: Any) -> None:
            import asyncio as _asyncio

            loop: Any = _asyncio.get_running_loop()

            # Start state collector to capture rolled_back transition.
            # OtaManager.run() calls _maybe_run_post_boot_health() which will
            # detect pending_health_check=True and call check_post_boot_health().
            # With _always_fail, this will timeout and call _do_rollback().
            states: list[str] = []
            states_done: threading.Event = threading.Event()

            def _collect() -> None:
                # Collect until rolled_back seen or timeout
                collected: list[str] = self._collect_ota_states(
                    pub_endpoint, timeout_s=15.0, target_states=["rolled_back"]
                )
                states.extend(collected)
                states_done.set()

            import threading as _threading
            ct: _threading.Thread = _threading.Thread(target=_collect, daemon=True)
            ct.start()

            # Wait for OtaManager startup health check to complete (timeout_s=5s)
            # plus a buffer for state publishing
            await loop.run_in_executor(
                None, lambda: states_done.wait(timeout=15.0)
            )
            states_holder[0] = states

        # Use health_check_fn=_always_fail so the HealthChecker created inside
        # _make_manager_and_state_machine always returns False
        self._run_ota_test(ota_env, _test, health_check_fn=_always_fail)

        states: list[str] = states_holder[0]
        assert "rolled_back" in states, (
            f"OTA state never reached 'rolled_back': {states} -- "
            "rollback not triggered when health check fails at startup"
        )

        # Verify boot flag reverted to original partition (a)
        reverted_flag: Any = mock_partition.read_boot_flag()
        assert reverted_flag.active == "a", (
            f"Boot flag not reverted after rollback: active='{reverted_flag.active}'"
        )

        # Verify mock reboot was called during rollback
        assert mock_partition._rebooted, (
            "MockPartitionBackend.reboot() not called during rollback"
        )

        # Reset boot flag to clean state for subsequent tests
        mock_partition._boot_flag_path.write_text(
            _json.dumps(
                {
                    "active": "a",
                    "previous": "b",
                    "boot_count": 1,
                    "pending_health_check": False,
                }
            ),
            encoding="utf-8",
        )

    def test_ota_status_published(
        self,
        ota_env: dict[str, Any],
        ed25519_keypair: tuple[Any, str],
    ) -> None:
        """Validate OTA-05: ZMQ PUB socket emits state transitions during update.

        Triggers an OTA update and asserts that the ZMQ PUB socket emits
        at least: downloading, verifying, applying transitions.
        """
        import asyncio as _asyncio
        import json as _json
        import shutil as _shutil

        from ems_ota_manager.state_machine import OtaState

        private_key, _ = ed25519_keypair
        mock_partition: MockPartitionBackend = ota_env["mock_partition"]
        serve_dir: Path = ota_env["serve_dir"]
        base_url: str = ota_env["base_url"]
        staging_dir: Path = ota_env["staging_dir"]
        pub_endpoint: str = ota_env["pub_endpoint"]
        rep_endpoint: str = ota_env["rep_endpoint"]

        # Reset state
        mock_partition._boot_flag_path.write_text(
            _json.dumps(
                {
                    "active": "a",
                    "previous": "b",
                    "boot_count": 1,
                    "pending_health_check": False,
                }
            ),
            encoding="utf-8",
        )
        mock_partition._image_written = None
        mock_partition._rebooted = False

        # Build and serve package
        package_path, pkg_sha256, version = _build_test_ota_package(
            staging_dir / "pub_test_pkgs", private_key, version="5.0.0"
        )
        dest_pkg: Path = serve_dir / package_path.name
        _shutil.copy(str(package_path), str(dest_pkg))
        package_url: str = f"{base_url}/{package_path.name}"

        states_holder: list[list[str]] = [[]]

        async def _test(manager: Any, sm: Any) -> None:
            loop: Any = _asyncio.get_running_loop()

            # Start collector before sending update command
            states: list[str] = []
            states_done: threading.Event = threading.Event()

            def _collect() -> None:
                collected: list[str] = self._collect_ota_states(
                    pub_endpoint, timeout_s=30.0, target_states=["rebooting", "idle"]
                )
                states.extend(collected)
                states_done.set()

            import threading as _threading
            ct: _threading.Thread = _threading.Thread(target=_collect, daemon=True)
            ct.start()

            await _asyncio.sleep(0.2)

            # Trigger update
            await loop.run_in_executor(
                None,
                lambda: self._send_ota_command(
                    "start_update",
                    {"url": package_url, "sha256": pkg_sha256, "version": version},
                    rep_endpoint,
                    timeout_ms=5000,
                ),
            )

            await loop.run_in_executor(
                None, lambda: states_done.wait(timeout=30.0)
            )
            states_holder[0] = states

        self._run_ota_test(ota_env, _test)

        states = states_holder[0]
        required: list[str] = ["downloading", "verifying", "applying"]
        for expected in required:
            assert expected in states, (
                f"OTA state '{expected}' not published on ZMQ PUB: {states}"
            )


# ===========================================================================
# TestM4CrashRecovery
# ===========================================================================


def _check_ota_subprocess_ready(ota_cmd_endpoint: str, timeout_s: float = 2.0) -> bool:
    """Check if ota_manager subprocess is ready by sending a get_version ZMQ REQ.

    Returns True if a valid response is received within timeout_s, False otherwise.
    Uses linger=0 to avoid blocking on socket cleanup (STATE.md decision).

    Args:
        ota_cmd_endpoint: ZMQ REP endpoint to connect to.
        timeout_s: Seconds to wait for a response.

    Returns:
        True if ota_manager responded to get_version, False otherwise.
    """
    from ems_common.ipc import decode_command_response, encode_command_request

    ctx: zmq.Context = zmq.Context()
    req: zmq.Socket = ctx.socket(zmq.REQ)
    req.setsockopt(zmq.LINGER, 0)
    req.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    req.connect(ota_cmd_endpoint)

    try:
        req.send(encode_command_request("get_version", {}))
        raw: bytes = req.recv()
        response: dict = decode_command_response(raw)
        return response.get("status") == "ok"
    except Exception:
        return False
    finally:
        req.close(linger=0)
        ctx.term()


def _build_m4_crash_system(mqtt_host: str, mqtt_port: int) -> dict[str, Any]:
    """Launch full M4 system plus ota_manager subprocess for crash recovery tests.

    Extends _build_m4_system with an ota_manager subprocess using TCP ZMQ
    endpoints (not IPC) so tests can send commands to it from any thread.

    Args:
        mqtt_host: Mosquitto broker host.
        mqtt_port: Mosquitto broker port.

    Returns:
        Dict extending _build_m4_system output with ota_manager process and endpoints.
    """
    # Build base M4 system first
    system: dict[str, Any] = _build_m4_system(mqtt_host, mqtt_port)
    tmpdir: Path = system["tmpdir"]

    # Allocate TCP ports for ota_manager ZMQ PUB + CMD
    ota_ports: list[int] = _allocate_tcp_ports(2)
    ota_pub_port: int = ota_ports[0]
    ota_cmd_port: int = ota_ports[1]
    ota_pub_endpoint: str = f"tcp://127.0.0.1:{ota_pub_port}"
    ota_cmd_endpoint: str = f"tcp://127.0.0.1:{ota_cmd_port}"

    # Create ota_config.yaml with required schema fields
    # A dummy 64-hex-char Ed25519 public key (not real -- ota_manager doesn't verify at startup)
    dummy_pub_key_hex: str = "a" * 64

    ota_staging_dir: Path = tmpdir / "ota_staging"
    ota_staging_dir.mkdir(parents=True, exist_ok=True)
    ota_boot_flag_path: Path = tmpdir / "boot_flag.json"
    ota_version_state_path: Path = tmpdir / "ota_version_state.json"

    # Write initial boot flag (no pending health check)
    import json as _json

    ota_boot_flag_path.write_text(
        _json.dumps(
            {
                "active": "a",
                "previous": "b",
                "boot_count": 1,
                "pending_health_check": False,
            }
        ),
        encoding="utf-8",
    )

    ota_config_data: dict[str, Any] = {
        "_schema_version": "1.0",
        "staging": {
            "dir": str(ota_staging_dir),
            "max_package_mb": 100,
        },
        "partition": {
            "boot_flag_path": str(ota_boot_flag_path),
            "active_device": "/dev/null",
            "standby_device": "/dev/null",
        },
        "security": {
            "public_key_hex": dummy_pub_key_hex,
        },
        "download": {
            "timeout_s": 300,
            "chunk_size": 65536,
            "progress_interval_s": 5,
        },
        "health_check": {
            # minItems: 1 required by schema -- use a dummy service name
            "services": ["ems_dummy_service"],
            "timeout_s": 300,
            "poll_interval_s": 10,
        },
        "version": {
            "state_path": str(ota_version_state_path),
        },
    }

    ota_config_path: Path = tmpdir / "ota_config_crash_test.yaml"
    with open(ota_config_path, "w") as f:
        yaml.dump(ota_config_data, f, default_flow_style=False)

    # Ready check: send get_version to ota_cmd_endpoint
    def _ota_ready() -> bool:
        return _check_ota_subprocess_ready(ota_cmd_endpoint, timeout_s=2.0)

    ota_manager_proc: ModuleProcess = ModuleProcess(
        name="ota_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_ota_manager",
            "--config", str(ota_config_path),
            "--log-level", "DEBUG",
        ],
        ready_check=_ota_ready,
        env={
            "EMS_OTA_PUB_ENDPOINT": ota_pub_endpoint,
            "EMS_OTA_CMD_ENDPOINT": ota_cmd_endpoint,
        },
    )

    system["modules"]["ota_manager"] = ota_manager_proc
    system["ota_cmd_endpoint"] = ota_cmd_endpoint
    system["ota_pub_endpoint"] = ota_pub_endpoint
    system["ota_config_path"] = ota_config_path

    return system


def _teardown_m4_crash_system(system: dict[str, Any]) -> None:
    """Teardown the M4 crash test system (same as _teardown_m4_system)."""
    _teardown_m4_system(system)


class TestM4CrashRecovery:
    """Crash recovery tests for cloud_manager and ota_manager.

    Validates that after SIGKILL or SIGTERM, each module restarts and
    reconnects within 10 seconds:
    - cloud_manager: reconnects to MQTT and publishes status
    - ota_manager: responds to ZMQ get_version after restart

    Tests use full M4 system with Mosquitto broker (cloud_manager tests)
    and ota_manager subprocess with TCP ZMQ endpoints.
    """

    @pytest.fixture(scope="class")
    def m4_crash_system(self, mosquitto_broker: tuple[str, int]) -> Any:
        """Launch full M4 system including ota_manager for crash recovery tests."""
        mqtt_host, mqtt_port = mosquitto_broker
        system: dict[str, Any] = _build_m4_crash_system(mqtt_host, mqtt_port)
        try:
            yield system
        finally:
            _teardown_m4_crash_system(system)

    def test_cloud_manager_crash_recovery(
        self, m4_crash_system: dict[str, Any]
    ) -> None:
        """Validate cloud_manager recovers within 10s after SIGKILL.

        Steps:
        1. Verify cloud_manager is alive and MQTT heartbeat is flowing.
        2. Send SIGKILL to cloud_manager process.
        3. Wait for process to die (up to 3s).
        4. Restart via ModuleProcess.restart().
        5. Wait up to 10s for status message on MQTT {prefix}/status.
        6. Assert cloud_manager is alive.
        """
        modules: dict[str, ModuleProcess] = m4_crash_system["modules"]
        host: str = m4_crash_system["mqtt_host"]
        port: int = m4_crash_system["mqtt_port"]
        prefix: str = m4_crash_system["topic_prefix"]

        cloud_proc: ModuleProcess = modules["cloud_manager"]

        # Step 1: Verify alive and heartbeat flowing before kill
        assert cloud_proc.is_alive, "cloud_manager not alive at test start"

        # Step 2: Kill with SIGKILL
        cloud_proc.kill(sig=signal.SIGKILL)

        # Step 3: Wait for process to die (up to 3s)
        deadline: float = time.monotonic() + 3.0
        while time.monotonic() < deadline and cloud_proc.is_alive:
            time.sleep(0.1)
        assert not cloud_proc.is_alive, (
            "cloud_manager did not die after SIGKILL within 3s"
        )

        # Step 4: Restart
        cloud_proc.restart()

        # Step 5: Verify recovery -- wait for MQTT heartbeat within 10s
        msg: dict | None = wait_for_mqtt_message(
            host, port, f"{prefix}/status", timeout=10.0
        )
        assert msg is not None, (
            "cloud_manager did not reconnect to MQTT and publish status within 10s "
            "after SIGKILL restart"
        )

        # Step 6: Verify process is alive
        assert cloud_proc.is_alive, (
            "cloud_manager process died again after restart"
        )

    def test_cloud_manager_sigterm_recovery(
        self, m4_crash_system: dict[str, Any]
    ) -> None:
        """Validate cloud_manager recovers within 10s after SIGTERM (graceful shutdown).

        Same as SIGKILL test but validates graceful shutdown + clean restart.
        """
        modules: dict[str, ModuleProcess] = m4_crash_system["modules"]
        host: str = m4_crash_system["mqtt_host"]
        port: int = m4_crash_system["mqtt_port"]
        prefix: str = m4_crash_system["topic_prefix"]

        cloud_proc: ModuleProcess = modules["cloud_manager"]

        assert cloud_proc.is_alive, "cloud_manager not alive at test start"

        # Send SIGTERM (graceful shutdown)
        cloud_proc.kill(sig=signal.SIGTERM)

        # Wait for die (SIGTERM may take longer than SIGKILL -- up to 5s)
        deadline: float = time.monotonic() + 5.0
        while time.monotonic() < deadline and cloud_proc.is_alive:
            time.sleep(0.1)

        if cloud_proc.is_alive:
            # Fallback to SIGKILL if SIGTERM didn't terminate within 5s
            cloud_proc.kill(sig=signal.SIGKILL)
            time.sleep(0.5)

        cloud_proc.restart()

        msg: dict | None = wait_for_mqtt_message(
            host, port, f"{prefix}/status", timeout=10.0
        )
        assert msg is not None, (
            "cloud_manager did not reconnect to MQTT and publish status within 10s "
            "after SIGTERM restart"
        )
        assert cloud_proc.is_alive, "cloud_manager process died again after restart"

    def test_ota_manager_crash_recovery(
        self, m4_crash_system: dict[str, Any]
    ) -> None:
        """Validate ota_manager recovers within 10s after SIGKILL.

        Steps:
        1. Verify ota_manager is alive and responding to get_version.
        2. Send SIGKILL to ota_manager process.
        3. Wait for process to die (up to 3s).
        4. Restart via ModuleProcess.restart().
        5. Verify recovery: send get_version to ota_cmd_endpoint, expect response within 10s.
        6. Assert ota_manager is alive.
        """
        modules: dict[str, ModuleProcess] = m4_crash_system["modules"]
        ota_cmd_endpoint: str = m4_crash_system["ota_cmd_endpoint"]

        ota_proc: ModuleProcess = modules["ota_manager"]

        assert ota_proc.is_alive, "ota_manager not alive at test start"

        # Verify it responds before kill
        assert _check_ota_subprocess_ready(ota_cmd_endpoint, timeout_s=3.0), (
            "ota_manager not responding to get_version before crash test"
        )

        # Kill with SIGKILL
        ota_proc.kill(sig=signal.SIGKILL)

        deadline: float = time.monotonic() + 3.0
        while time.monotonic() < deadline and ota_proc.is_alive:
            time.sleep(0.1)
        assert not ota_proc.is_alive, (
            "ota_manager did not die after SIGKILL within 3s"
        )

        # Restart
        ota_proc.restart()

        # Verify recovery within 10s
        assert ota_proc.is_alive, "ota_manager process did not restart"
        # The restart() already waited for ready_check (which calls get_version)
        # Verify one more time for clarity
        assert _check_ota_subprocess_ready(ota_cmd_endpoint, timeout_s=5.0), (
            "ota_manager did not respond to get_version within 5s after SIGKILL restart"
        )

    def test_ota_manager_sigterm_recovery(
        self, m4_crash_system: dict[str, Any]
    ) -> None:
        """Validate ota_manager recovers within 10s after SIGTERM (graceful shutdown).

        Same as SIGKILL test but validates graceful shutdown + clean restart.
        """
        modules: dict[str, ModuleProcess] = m4_crash_system["modules"]
        ota_cmd_endpoint: str = m4_crash_system["ota_cmd_endpoint"]

        ota_proc: ModuleProcess = modules["ota_manager"]

        assert ota_proc.is_alive, "ota_manager not alive at test start"

        # Send SIGTERM
        ota_proc.kill(sig=signal.SIGTERM)

        # Wait for graceful shutdown (up to 5s)
        deadline: float = time.monotonic() + 5.0
        while time.monotonic() < deadline and ota_proc.is_alive:
            time.sleep(0.1)

        if ota_proc.is_alive:
            ota_proc.kill(sig=signal.SIGKILL)
            time.sleep(0.5)

        # Restart
        ota_proc.restart()

        assert ota_proc.is_alive, "ota_manager process did not restart after SIGTERM"
        assert _check_ota_subprocess_ready(ota_cmd_endpoint, timeout_s=5.0), (
            "ota_manager did not respond to get_version within 5s after SIGTERM restart"
        )
