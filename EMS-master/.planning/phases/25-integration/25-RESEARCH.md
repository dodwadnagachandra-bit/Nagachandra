# Phase 25: Integration and Hardening - Research

**Researched:** 2026-03-15
**Domain:** Integration testing of cloud_manager + ota_manager against the full EMS module stack
**Confidence:** HIGH

## Summary

Phase 25 is a validation-only phase: no new functional modules are built. The goal is to prove that cloud_manager and ota_manager (built in Phases 22-24) work correctly together and alongside all M1-M3 modules under real-world conditions: startup ordering, E2E command flow, offline/online transitions, OTA update cycle, and crash recovery.

All code under test already exists. The primary deliverable is a new integration test file `tests/integration/test_m4_integration.py` following the established patterns from `test_m3_integration.py`. A Mosquitto subprocess fixture replaces the real cloud broker. OTA partition operations are abstracted via a mock backend. Three supporting fixtures — Mosquitto subprocess manager, OTA package builder, and mock partition backend — are the key new infrastructure pieces.

The existing `conftest.py` (ModuleProcess, wait_for_criteria, check_rtdb_fresh) and `test_crash_recovery.py` (CRASH_MATRIX pattern, STARTUP_ORDER) are reused directly. cloud_manager and ota_manager are launched as ModuleProcess subprocesses with env-var endpoint overrides to redirect ZMQ sockets from `/run/ems/` IPC paths to TCP for isolation.

**Primary recommendation:** One test file (`test_m4_integration.py`) with four test classes: `TestM4Startup`, `TestE2ERemoteCommand`, `TestOfflineTransition`, `TestM4CrashRecovery`. Add `test-integration-m4` Makefile target. Mosquitto is available as a package (`mosquitto` 2.0.18-1) but not currently installed — fixture must handle `pytest.skip` gracefully.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Cloud Connectivity Test Methodology**

| Aspect | Decision |
|--------|----------|
| Broker | Mosquitto started as subprocess with minimal config (no auth, no TLS) |
| Port | Random available port (avoid conflicts with other tests) |
| TLS | Disabled for integration tests (TLS tested in unit tests with mock certs) |
| Cleanup | Kill mosquitto after test, remove temp config |
| Assertions | Subscribe to MQTT topics from test, verify published messages match expected |

**E2E Remote Command Test**

| Step | Action | Verification | Timeout |
|------|--------|-------------|---------|
| 1 | Start all modules + cloud_manager + Mosquitto | Cloud_manager connected to broker | 15s |
| 2 | Publish `{prefix}/commands` with `{command: "mode_change", params: {target_state: "standby"}}` | — | — |
| 3 | Subscribe to `{prefix}/responses/{request_id}` | Response: `{status: "ok"}` | 5s |
| 4 | Verify RTDB | control_state == STANDBY | 15s |

**Offline/Online Transition Test**

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Start all modules + cloud_manager + Mosquitto | Telemetry flowing to MQTT |
| 2 | Kill Mosquitto (simulate network loss) | cloud_manager detects disconnect, buffer activates |
| 3 | Wait 30 seconds (accumulate buffer) | Buffer files created in `data/cloud_buffer/` |
| 4 | Restart Mosquitto | cloud_manager reconnects, replay starts |
| 5 | Wait for replay to complete | Buffer files deleted, live telemetry resumes |
| 6 | Subscribe to MQTT, verify both replay and live messages received | Messages have correct timestamps (old and current) |

**OTA Update Cycle Test**

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Create test OTA package (tar with manifest + dummy firmware + Ed25519 signature) | Package valid, signature verifies |
| 2 | Start ota_manager with mock partition backend | Status: idle |
| 3 | Serve package via local HTTP server (Python http.server) | — |
| 4 | Send OTA notification via MQTT (or ZMQ command) | Status: downloading → verifying → applying |
| 5 | Mock partition write completes | Boot flag swapped in mock |
| 6 | Simulate health check pass | Status: success, version updated |
| 7 | Test rollback: send another update, mock health check fail | Status: rolled_back, previous version restored |

**Crash Recovery**

| Module | Recovery Behavior |
|--------|------------------|
| cloud_manager | Restart within 10s, reconnect to MQTT broker, resume telemetry forwarding |
| ota_manager | Restart within 10s, check boot flag; rollback if mid-update; idle if clean |

### Claude's Discretion

- Mosquitto test fixture implementation (subprocess, config file, port allocation)
- Mock partition backend design
- Ed25519 test key pair generation (in conftest.py or fixture)
- OTA package builder helper for tests
- Makefile target naming (test-integration-m4)
- Whether to extend existing crash_recovery.py or create new test file

### Deferred Ideas (OUT OF SCOPE)

- Performance testing under high MQTT publish rate — deferred to M5
- TLS certificate rotation testing — deferred
- Multi-broker failover testing — deferred
- Real partition testing on ECU hardware — deferred to PLAT-01 resolution
- Long-duration connectivity soak test — deferred to M5
</user_constraints>

---

## Standard Stack

### Core (already in workspace, no new installs)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `paho-mqtt` | 2.x (MQTTv311) | MQTT client in cloud_manager | Already in ems_cloud_manager deps |
| `mosquitto` | 2.0.18 (Ubuntu pkg) | Local broker subprocess for tests | Self-hosted Mosquitto is the production broker (Decision #10.1) |
| `cryptography` | current | Ed25519 key pair generation for test packages | Already in ems_ota_manager deps (used by PackageVerifier) |
| `zmq` / `pyzmq` | 27.1.0+ | ZMQ REQ sockets for OTA command injection | Already in workspace |
| `pytest` + `pytest-asyncio` | 8.0 / 1.3.0 | Test framework | Project standard |
| `httpx` | current | HTTP health checks and OTA package HTTP server client | Already in workspace (used in conftest.py) |
| `http.server` (stdlib) | Python 3.12 | Serve OTA packages in tests (no extra dep) | stdlib, zero-dep, already pattern in OTA CONTEXT |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `psutil` | 7.2.2 | RSS monitoring, process inspection | Already in workspace, used in conftest |
| `msgpack` | 1.0+ | Decoding ZMQ telemetry frames | Already in workspace |
| `pyyaml` | 6.0+ | Writing temp cloud_config.yaml for test | Already in workspace |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `mosquitto` subprocess | `asyncio-mqtt` in-process broker | Mosquitto tests the real protocol; in-process mock doesn't validate paho reconnect behavior |
| `http.server` stdlib | `aiohttp` test server | stdlib requires zero new deps; aiohttp is overkill for static file serving |
| `cryptography` for Ed25519 | `PyNaCl` | cryptography is already used by PackageVerifier — use the same library |

**Installation:**

Mosquitto is NOT installed in the current dev environment. Fixture must handle gracefully:
```bash
sudo apt-get install -y mosquitto
```
Or in the fixture itself, skip with `pytest.skip("mosquitto not installed")` if binary not found.

---

## Architecture Patterns

### Recommended Project Structure

```
tests/integration/
├── conftest.py                  # EXISTING — ModuleProcess, wait_for_criteria, etc.
├── test_startup.py              # EXISTING — build_start_order, full system startup
├── test_crash_recovery.py       # EXISTING — CRASH_MATRIX, STARTUP_ORDER
├── test_m2_integration.py       # EXISTING
├── test_m3_integration.py       # EXISTING — pattern to follow
└── test_m4_integration.py       # NEW — this phase
```

### Pattern 1: Mosquitto Subprocess Fixture

**What:** Start `mosquitto` as a subprocess with a temp config file pointing to a random free port. Yield the port and process. Kill and clean up in teardown.

**When to use:** Every test class that needs real MQTT broker connectivity.

```python
# Source: derived from existing ModuleProcess pattern in conftest.py

import socket
import subprocess
import tempfile
import time
from pathlib import Path

def _free_port() -> int:
    """Allocate one free TCP port (same pattern as _allocate_tcp_ports in test_startup.py)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

@pytest.fixture(scope="class")
def mosquitto_broker():
    """Start mosquitto on a random port, yield (host, port), kill on teardown.

    Skips if mosquitto binary is not found.
    """
    import shutil
    if not shutil.which("mosquitto"):
        pytest.skip("mosquitto not installed -- run: sudo apt-get install -y mosquitto")

    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ems_mosquitto_"))
    config_file = tmpdir / "mosquitto.conf"
    config_file.write_text(
        f"listener {port} 127.0.0.1\n"
        f"allow_anonymous true\n"
        f"log_type error\n"
    )
    proc = subprocess.Popen(
        ["mosquitto", "-c", str(config_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for broker to become available
    deadline = time.monotonic() + 5.0
    import paho.mqtt.client as mqtt
    while time.monotonic() < deadline:
        try:
            c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
            c.connect("127.0.0.1", port, keepalive=1)
            c.disconnect()
            break
        except Exception:
            time.sleep(0.1)

    yield ("127.0.0.1", port)

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    import shutil as _shutil
    _shutil.rmtree(tmpdir, ignore_errors=True)
```

### Pattern 2: cloud_manager as ModuleProcess with Env-Var Overrides

**What:** Launch `ems_cloud_manager` as a subprocess with env vars that redirect ZMQ sockets from `/run/ems/` IPC paths to TCP ports, and point the MQTT broker at the local Mosquitto instance.

**When to use:** Any test that needs cloud_manager connected to the system.

The cloud_manager `__main__.py` reads these env vars:
- `EMS_TELEMETRY_SUB_ENDPOINT` — connects to data_manager telemetry PUB
- `EMS_ALARM_SUB_ENDPOINT` — connects to alarm_manager PUB
- `EMS_LOGGER_PUSH_ENDPOINT` — connects to logger PUSH
- `EMS_CONTROL_CMD_ENDPOINT` — connects to control_manager REQ/REP
- `EMS_ALARM_CMD_ENDPOINT` — connects to alarm_manager command socket
- `EMS_CLOUD_PUB_ENDPOINT` — binds the cloud status PUB (must use TCP for test isolation)
- `EMS_CLOUD_BUFFER_DIR` — temp dir for offline buffer files

Cloud config must use `auth.method: token` (not mtls) and `broker.host: 127.0.0.1` / `broker.port: {mosquitto_port}`.

```python
# Write temp cloud_config.yaml with test settings
cloud_cfg = {
    "_schema_version": "1.0",
    "broker": {"host": "127.0.0.1", "port": mqtt_port, "protocol": "mqtt"},
    "auth": {"method": "token"},
    "telemetry": {"interval_s": 10, "topic_prefix": "ems/TEST-001"},
    "offline_buffer": {"enabled": True, "max_hours": 1, "max_mb": 10},
}

cloud_proc = ModuleProcess(
    name="cloud_manager",
    cmd=["uv", "run", "python", "-m", "ems_cloud_manager",
         "--config", str(cloud_config_path)],
    ready_check=lambda: _check_mqtt_connected(mqtt_port, "ems/TEST-001/status"),
    env={
        "EMS_TELEMETRY_SUB_ENDPOINT": telemetry_tcp_endpoint,
        "EMS_ALARM_SUB_ENDPOINT": alarm_tcp_endpoint,
        "EMS_CLOUD_PUB_ENDPOINT": cloud_pub_tcp_endpoint,
        "EMS_CONTROL_CMD_ENDPOINT": control_cmd_endpoint,
        "EMS_CLOUD_BUFFER_DIR": str(buffer_dir),
    },
)
```

**Readiness check for cloud_manager:** Subscribe to `{prefix}/status` MQTT topic. cloud_manager publishes a heartbeat to `{prefix}/status` when connected. Alternatively, watch for the initial `connected` state message on the ZMQ cloud_pub PUB socket.

A simpler ready_check: try to subscribe and receive any MQTT message from the broker on `{prefix}/#` within 15 seconds.

### Pattern 3: OTA Package Builder for Tests

**What:** Python function that builds a valid OTA package (tar.gz) with manifest.json, dummy firmware file, Ed25519 signature, and SHA-256 hash.

**When to use:** OTA cycle test and rollback test.

```python
# Source: based on PackageVerifier.extract_package() and OtaStateMachine.start_update()
import hashlib, json, os, tarfile, tempfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def build_test_ota_package(
    staging_dir: Path,
    private_key: Ed25519PrivateKey,
    version: str = "1.2.3",
) -> tuple[Path, str, str]:
    """Build a minimal valid OTA tar.gz package.

    Returns: (package_path, sha256_hex, version)
    """
    workdir = Path(tempfile.mkdtemp(dir=staging_dir))

    # Dummy firmware
    firmware_path = workdir / "firmware.img"
    firmware_path.write_bytes(os.urandom(1024))
    fw_sha256 = hashlib.sha256(firmware_path.read_bytes()).hexdigest()

    # Manifest (no 'signature' key yet — sign the manifest bytes)
    manifest = {
        "version": version,
        "firmware": "firmware.img",
        "sha256": fw_sha256,
        "min_version": "0.0.0",
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    sig_bytes = private_key.sign(manifest_bytes)
    manifest["signature"] = sig_bytes.hex()

    manifest_path = workdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    # Create tar.gz
    package_path = staging_dir / f"ota_{version}.tar.gz"
    with tarfile.open(package_path, "w:gz") as tf:
        tf.add(manifest_path, arcname="manifest.json")
        tf.add(firmware_path, arcname="firmware.img")

    # SHA-256 of the tar.gz itself (for HttpDownloader integrity check)
    pkg_sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()

    return package_path, pkg_sha256, version
```

**Ed25519 fixture (session-scoped):**
```python
@pytest.fixture(scope="session")
def ed25519_keypair():
    """Generate a fresh Ed25519 key pair for the test session."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pub_hex = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private_key, pub_hex
```

### Pattern 4: Mock Partition Backend

**What:** Subclass or duck-type replacement for `PartitionBackend` that uses temp directories instead of block devices. Implements the same interface: `read_boot_flag()`, `write_boot_flag()`, `write_image_to_standby()`, `reboot()`, `get_standby_partition()`.

**When to use:** OTA integration tests — avoids touching `/dev/mmcblk0p*` and calling `systemctl reboot`.

The OTA manager accepts a `PartitionBackend` in `__main__.py`. For integration tests that launch ota_manager as a subprocess, the mock must be injected via a different mechanism (e.g., a temp `ota_config.yaml` that points `partition.boot_flag_path` to a temp file, and `active_device`/`standby_device` to `/dev/null` — since `write_image_to_standby` calls `dd if=... of=/dev/null` which succeeds and is harmless). The `reboot()` method calls `systemctl reboot` — test must either intercept via mock or the OTA test should not trigger actual reboot (stop test before REBOOTING state, or mock the partition in a unit-style invocation).

**Preferred approach for integration tests:** Test the OTA state machine in-process (not as a subprocess) to allow direct injection of mock partition. The `OtaManager` class accepts `ota_pub_socket` and `ota_rep_socket` pre-built sockets for test isolation — use this pattern. Run `asyncio.run()` in a thread, inject commands via ZMQ REQ.

### Pattern 5: M4 Startup Order Extension

**What:** Extend the existing `STARTUP_ORDER` from `test_crash_recovery.py` with cloud_manager and ota_manager.

Current M3 STARTUP_ORDER (from `test_startup.py::build_start_order` + `test_crash_recovery.py`):
```
data_manager_c → data_manager_python → config_manager → safety_manager →
comm_manager_c → comm_manager_python → logger → control_manager →
alarm_manager → hmi_server → scheduler
```

M4 extends to:
```
... → scheduler → cloud_manager → ota_manager
```

Systemd ordering (`cloud_manager.service`): `After=network.target data_manager.service`
Systemd ordering (`ota_manager.service`): `After=network.target cloud_manager.service`

For integration tests: cloud_manager and ota_manager are always Python modules — no C binaries required. They require Mosquitto to be running before cloud_manager starts (otherwise it will keep retrying with backoff).

### Anti-Patterns to Avoid

- **Using ipc:// ZMQ endpoints for cloud_pub in tests:** `/run/ems/cloud_pub.sock` binding requires the directory to exist. Use TCP with random ports (same as M3 pattern in `test_m3_integration.py::_allocate_tcp_ports`).
- **Not re-subscribing after Mosquitto restart:** paho-mqtt with `clean_session=True` loses subscriptions on reconnect — cloud_manager's `_on_connect` callback re-subscribes to `{prefix}/commands` automatically (already implemented in `publisher.py`). Tests must also re-subscribe their test MQTT clients after Mosquitto restarts.
- **Blocking asyncio in test with ZMQ REQ:** The `CommandDispatcher._send_recv` uses `run_in_executor` with 5-second timeout (`RCVTIMEO=5000`). Test must ensure control_manager is running and its REP socket is bound before sending commands.
- **Assuming buffer files are in a fixed location:** cloud_manager buffer dir is set via `EMS_CLOUD_BUFFER_DIR` env var. Always use a temp dir in tests and assert on that dir.
- **Triggering real `systemctl reboot` in OTA tests:** Either stop the test at APPLYING state, or override `partition.reboot()` via mock. The OTA state machine itself calls `reboot()` after writing boot flag — tests that go all the way to REBOOTING will actually attempt to reboot the machine unless mocked.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MQTT broker for tests | Custom in-process mock broker | Mosquitto subprocess | Tests paho's actual TCP reconnect behavior; already the prod broker |
| Random port allocation | Custom port scanner | `socket.bind(("127.0.0.1", 0))` | OS-assigned, guaranteed free, already used in `_allocate_tcp_ports` |
| Ed25519 key pair generation | Custom crypto impl | `cryptography.Ed25519PrivateKey.generate()` | Same library as PackageVerifier, correct 32-byte format |
| OTA tar package creation | Custom archive format | Python `tarfile` stdlib | PackageVerifier.extract_package() expects tarfile format |
| Process lifecycle management | Custom subprocess wrapper | `ModuleProcess` from conftest.py | Already handles ready_check, restart, kill, cleanup |
| Multi-criteria polling | Custom sleep loop | `wait_for_criteria()` from conftest.py | Already handles timeout, returns pass/fail per criterion |

**Key insight:** All process management, port allocation, and polling infrastructure is already built. The integration test file is primarily fixture wiring + assertion logic.

---

## Common Pitfalls

### Pitfall 1: cloud_manager Config Requires mtls Cert Files

**What goes wrong:** `load_cloud_config()` raises `FileNotFoundError` for cert files when `auth.method=mtls`. The production config at `config/cloud_config.yaml` uses `auth.method: mtls`.

**Why it happens:** The config loader explicitly checks cert file existence for mTLS (line 101-117 of `config.py`).

**How to avoid:** Always write a temp cloud_config.yaml for tests with `auth.method: token` (no cert files needed). Never pass the production config file to test subprocess.

**Warning signs:** `FileNotFoundError: cloud_config: cert file not found for 'ca_cert_path'` in subprocess output.

### Pitfall 2: Mosquitto Not Installed

**What goes wrong:** `subprocess.Popen(["mosquitto", ...])` raises `FileNotFoundError`.

**Why it happens:** Mosquitto is in apt package list but not in the `make setup` target (only `can-utils socat cmake ninja-build gcc-aarch64-linux-gnu clang-format libgpiod-dev libzmq3-dev` are installed).

**How to avoid:** Use `shutil.which("mosquitto")` before starting. Call `pytest.skip(...)` if not found. Add `mosquitto` to the `make setup` apt install line in the Makefile (or add a comment). Consider adding `pytest.importorskip` pattern.

**Warning signs:** `FileNotFoundError: [Errno 2] No such file or directory: 'mosquitto'`.

### Pitfall 3: OTA Reboot During Tests

**What goes wrong:** `OtaStateMachine.start_update()` calls `partition.reboot()` → `systemctl reboot` → machine actually reboots.

**Why it happens:** The real `PartitionBackend.reboot()` calls `systemctl reboot`. If OTA test reaches REBOOTING state with the real partition backend, the dev machine reboots.

**How to avoid:** For OTA cycle tests: test the `OtaStateMachine` in-process (not as a subprocess) with a mock `PartitionBackend` where `reboot()` is a no-op coroutine. The `OtaManager` already supports injected sockets — use this for test isolation. Alternatively, stop assertion at APPLYING state (before reboot is called).

**Warning signs:** Machine shuts down during test run. Or: `systemctl reboot` appearing in test logs.

### Pitfall 4: paho-mqtt Slow-Start / ZMQ Slow-Join

**What goes wrong:** Test subscribes to MQTT topic, cloud_manager is already publishing, test misses first few messages.

**Why it happens:** paho-mqtt subscriptions have the same slow-join property as ZMQ SUB — the broker doesn't buffer QoS 0 messages for clients that weren't subscribed yet.

**How to avoid:** Subscribe MQTT test client BEFORE waiting for cloud_manager to publish. For telemetry (QoS 0): poll with timeout rather than expecting the first publish. For events/commands (QoS 1): ensure subscription is in place before the event is triggered.

**Warning signs:** Test times out waiting for messages that were definitely published.

### Pitfall 5: cloud_manager ZMQ PUB Binding Conflict

**What goes wrong:** `MqttPublisher` binds `SOCK_CLOUD_PUB = "ipc:///run/ems/cloud_pub.sock"` in `__init__`. If multiple test processes run in parallel, or a previous test left a stale socket file, binding fails.

**Why it happens:** ZMQ PUB sockets bind (not connect) — only one process can bind to an address. IPC socket files persist after a crash.

**How to avoid:** Always override `EMS_CLOUD_PUB_ENDPOINT` with a TCP address on a random port. The `MqttPublisher.__init__` reads `cloud_pub_endpoint` param and falls back to env var in `__main__.py`. The `cleanup_ipc_sockets` autouse fixture in conftest.py removes `/run/ems/*.sock` after each test but not during.

**Warning signs:** `zmq.error.ZMQError: Address already in use` in cloud_manager output.

### Pitfall 6: Buffer Replay Timing

**What goes wrong:** The offline/online test expects buffer files to be deleted immediately after reconnect, but buffer replay runs at 10 msg/s with a 5-second poll if nothing to replay.

**Why it happens:** `_buffer_replay_task` in `BufferedCloudLoop` sleeps 5s when `replayed_this_cycle == 0`. At 10s telemetry interval × 30s offline = ~3 buffered messages, replay takes 0.3s. But the task may already be in its 5-second sleep when the broker reconnects.

**How to avoid:** Poll buffer directory with timeout (e.g., 60 seconds) rather than asserting immediate deletion. Use `wait_for_criteria()` with `lambda: len(list(buffer_dir.rglob("*.jsonl"))) == 0`.

---

## Code Examples

### Verified patterns from existing integration tests

### Full System Startup with M4 Modules

```python
# Source: Derived from test_startup.py::TestFullSystemStartup and test_crash_recovery.py::STARTUP_ORDER

STARTUP_ORDER_M4: list[str] = [
    "data_manager_c",
    "data_manager_python",
    "config_manager",
    "safety_manager",
    "comm_manager_c",
    "comm_manager_python",
    "logger",
    "control_manager",
    "alarm_manager",
    "hmi_server",
    "scheduler",
    "cloud_manager",   # M4 — requires Mosquitto running first
    "ota_manager",     # M4 — After=cloud_manager.service
]
```

### MQTT Subscribe-and-Receive Helper

```python
def wait_for_mqtt_message(
    host: str,
    port: int,
    topic: str,
    timeout: float = 15.0,
) -> dict | None:
    """Subscribe to topic and wait for first message. Returns parsed JSON or None."""
    import paho.mqtt.client as mqtt
    from paho.mqtt.client import CallbackAPIVersion
    received: list[dict] = []

    def _on_message(client, userdata, message):
        import json
        try:
            received.append(json.loads(message.payload.decode()))
        except Exception:
            pass

    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    client.on_message = _on_message
    client.connect(host, port, keepalive=10)
    client.subscribe(topic, qos=1)
    client.loop_start()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not received:
        time.sleep(0.1)

    client.loop_stop()
    client.disconnect()
    return received[0] if received else None
```

### OTA In-Process Test (avoiding real reboot)

```python
# Source: OtaManager accepts pre-built sockets for test isolation (STATE.md decision)
import asyncio
import zmq.asyncio

async def run_ota_in_process(state_machine, config, timeout=30):
    ctx = zmq.asyncio.Context()
    pub_sock = ctx.socket(zmq.PUB)
    pub_port = free_port()
    pub_sock.bind(f"tcp://127.0.0.1:{pub_port}")

    rep_sock = ctx.socket(zmq.REP)
    rep_port = free_port()
    rep_sock.bind(f"tcp://127.0.0.1:{rep_port}")

    manager = OtaManager(
        config=config,
        state_machine=state_machine,
        partition=state_machine._partition,
        ota_pub_socket=pub_sock,
        ota_rep_socket=rep_sock,
    )

    task = asyncio.create_task(manager.run())
    try:
        yield manager, pub_port, rep_port
    finally:
        manager.stop_event.set()
        await asyncio.gather(task, return_exceptions=True)
        manager.cleanup()
        ctx.term()
```

### Crash Recovery for M4 Modules

```python
# Source: CRASH_MATRIX pattern from test_crash_recovery.py

M4_CRASH_MATRIX: list[tuple[str, int]] = [
    ("cloud_manager", signal.SIGKILL),
    ("cloud_manager", signal.SIGTERM),
    ("ota_manager", signal.SIGKILL),
    ("ota_manager", signal.SIGTERM),
]

# Recovery check for cloud_manager: MQTT reconnect within 10s
# After restart, cloud_manager must reconnect to the still-running Mosquitto
# and resume publishing heartbeats to {prefix}/status

def check_cloud_reconnected(host: str, port: int, prefix: str, timeout: float = 10.0) -> bool:
    msg = wait_for_mqtt_message(host, port, f"{prefix}/status", timeout=timeout)
    return msg is not None
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|-----------------|--------------|--------|
| Mock MQTT clients in integration tests | Real Mosquitto subprocess | Phase 25 decision | Tests actual TCP reconnect and protocol behavior |
| paho-mqtt 1.x callback API | paho-mqtt 2.x CallbackAPIVersion.VERSION2 | paho 2.0 | All callbacks have different signatures; old tutorials are wrong |
| ZMQ PUB/SUB with IPC paths | TCP with random ports for test isolation | M3 pattern | No /run/ems dependency in CI |

**Deprecated/outdated:**
- `paho.mqtt.client.MQTTv5` for new code: the project uses `MQTTv311` (MQTT 3.1.1) — consistent with existing publisher.py code.
- paho 1.x callback signatures (only 3 args for on_connect): use VERSION2 (5 args) throughout.

---

## Open Questions

1. **Mosquitto in Makefile `make setup`**
   - What we know: `mosquitto 2.0.18` is in Ubuntu apt cache, not currently in `make setup`.
   - What's unclear: Whether the CI environment or dev machines have it pre-installed.
   - Recommendation: Add `mosquitto` to the `make setup` apt-get line. The fixture already skips gracefully if absent.

2. **cloud_manager readiness check without HTTP endpoint**
   - What we know: cloud_manager has no HTTP health endpoint (unlike hmi_server). The only observable readiness signal is successful MQTT connection (heartbeat to `{prefix}/status`).
   - What's unclear: How fast paho-mqtt connects to a local Mosquitto (usually <100ms, but with ZMQ binding overhead could be 1-2s).
   - Recommendation: Use `wait_for_mqtt_message(host, port, f"{prefix}/status", timeout=15)` as the ready_check for ModuleProcess.

3. **ota_manager readiness check**
   - What we know: ota_manager publishes `OtaState.IDLE` on startup via ZMQ PUB to `SOCK_OTA_PUB`. The ZMQ slow-join problem means a subscriber might miss this.
   - What's unclear: Whether `OtaManager.run()` re-publishes idle state periodically.
   - Recommendation: Use a ZMQ REQ to `SOCK_OTA_CMD` with action `get_version` as the readiness check — if it responds, the manager is up.

4. **Buffer dir cleanup between tests**
   - What we know: `EMS_CLOUD_BUFFER_DIR` is set per-test to a temp dir.
   - Recommendation: Each test fixture creates its own temp dir and cleans it up in teardown.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.0 + pytest-asyncio 1.3.0 |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/integration/test_m4_integration.py -v -m integration --timeout=300` |
| Full suite command | `uv run pytest tests/integration/ -v -m integration --timeout=900` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLOUD-01 | MQTT/TLS client connects with reconnect backoff | integration | `pytest tests/integration/test_m4_integration.py::TestM4Startup::test_cloud_manager_connects -x` | ❌ Wave 0 |
| CLOUD-02 | Telemetry forwarding (ZMQ PUB → MQTT) | integration | `pytest tests/integration/test_m4_integration.py::TestM4Startup::test_telemetry_reaches_mqtt -x` | ❌ Wave 0 |
| CLOUD-03 | Event forwarding (QoS 1) | integration | `pytest tests/integration/test_m4_integration.py::TestE2ERemoteCommand::test_e2e_command_flow -x` | ❌ Wave 0 |
| CLOUD-04 | Offline buffer fills on disconnect | integration | `pytest tests/integration/test_m4_integration.py::TestOfflineTransition::test_buffer_fills_when_offline -x` | ❌ Wave 0 |
| CLOUD-05 | Buffer replay on reconnect | integration | `pytest tests/integration/test_m4_integration.py::TestOfflineTransition::test_buffer_drains_on_reconnect -x` | ❌ Wave 0 |
| CLOUD-06 | Remote command MQTT → ZMQ → control_manager | integration | `pytest tests/integration/test_m4_integration.py::TestE2ERemoteCommand::test_e2e_command_flow -x` | ❌ Wave 0 |
| CLOUD-07 | Heartbeat to `{prefix}/status` | integration | `pytest tests/integration/test_m4_integration.py::TestM4Startup::test_cloud_heartbeat_published -x` | ❌ Wave 0 |
| CLOUD-08 | Cloud status on ZMQ PUB | integration | `pytest tests/integration/test_m4_integration.py::TestM4Startup::test_cloud_zmq_status -x` | ❌ Wave 0 |
| OTA-01 | Firmware download with SHA-256 | integration (in-process) | `pytest tests/integration/test_m4_integration.py::TestOtaCycle::test_ota_download_verifying -x` | ❌ Wave 0 |
| OTA-02 | Ed25519 signature verification | integration (in-process) | `pytest tests/integration/test_m4_integration.py::TestOtaCycle::test_ota_signature_verified -x` | ❌ Wave 0 |
| OTA-03 | A/B partition management | integration (in-process, mock) | `pytest tests/integration/test_m4_integration.py::TestOtaCycle::test_ota_boot_flag_swap -x` | ❌ Wave 0 |
| OTA-04 | Automatic rollback on health failure | integration (in-process, mock) | `pytest tests/integration/test_m4_integration.py::TestOtaCycle::test_ota_rollback -x` | ❌ Wave 0 |
| OTA-05 | Update status on ZMQ PUB | integration | `pytest tests/integration/test_m4_integration.py::TestOtaCycle::test_ota_status_published -x` | ❌ Wave 0 |
| OTA-06 | Version tracking in RTDB | integration | `pytest tests/integration/test_m4_integration.py::TestOtaCycle::test_version_query -x` | ❌ Wave 0 |
| — | cloud_manager crash recovery (10s) | integration | `pytest tests/integration/test_m4_integration.py::TestM4CrashRecovery::test_cloud_manager_crash_recovery -x` | ❌ Wave 0 |
| — | ota_manager crash recovery (10s) | integration | `pytest tests/integration/test_m4_integration.py::TestM4CrashRecovery::test_ota_manager_crash_recovery -x` | ❌ Wave 0 |
| — | Full M4 startup (all 13 modules alive) | integration | `pytest tests/integration/test_m4_integration.py::TestM4Startup::test_all_modules_alive -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/integration/test_m4_integration.py -v -m integration --timeout=300`
- **Per wave merge:** `uv run pytest tests/integration/ -v -m integration --timeout=900`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/integration/test_m4_integration.py` — covers all CLOUD-01 through CLOUD-08, OTA-01 through OTA-06, crash recovery for M4 modules
- [ ] Mosquitto in `make setup` apt-get line — add `mosquitto` to system deps

*(Existing test infrastructure in `tests/integration/conftest.py` covers most supporting functionality. No new conftest fixtures are strictly required — they can be local to test_m4_integration.py.)*

---

## Sources

### Primary (HIGH confidence)

- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/__main__.py` — env var overrides, component wiring
- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/publisher.py` — MQTT client, ZMQ PUB binding, callback signatures
- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/loop.py` — CloudLoop async tasks, hook points
- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/buffered_loop.py` — BufferedCloudLoop, replay task, 5s backoff behavior
- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/dispatcher.py` — CommandDispatcher, COMMAND_ROUTES, ZMQ REQ sockets
- `/home/overlord/EMS/src/ota_manager/src/ems_ota_manager/__main__.py` — OTA entry point, env var overrides
- `/home/overlord/EMS/src/ota_manager/src/ems_ota_manager/loop.py` — OtaManager, pre-built socket injection, command handling
- `/home/overlord/EMS/src/ota_manager/src/ems_ota_manager/state_machine.py` — OtaStateMachine, 6 states, reboot call path
- `/home/overlord/EMS/src/ota_manager/src/ems_ota_manager/partition.py` — PartitionBackend, `reboot()` calls systemctl
- `/home/overlord/EMS/src/ota_manager/src/ems_ota_manager/verifier.py` — PackageVerifier, Ed25519, tar extraction
- `/home/overlord/EMS/src/ota_manager/src/ems_ota_manager/health.py` — HealthChecker, pluggable check_fn
- `/home/overlord/EMS/tests/integration/conftest.py` — ModuleProcess, MetricsCollector, wait_for_criteria, port allocation
- `/home/overlord/EMS/tests/integration/test_crash_recovery.py` — CRASH_MATRIX, STARTUP_ORDER, recovery pattern
- `/home/overlord/EMS/tests/integration/test_startup.py` — build_start_order, _delay_ready, full system fixture
- `/home/overlord/EMS/tests/integration/test_m3_integration.py` — M3 integration test pattern to follow
- `/home/overlord/EMS/deploy/systemd/cloud_manager.service` — `After=network.target data_manager.service`
- `/home/overlord/EMS/deploy/systemd/ota_manager.service` — `After=network.target cloud_manager.service`, `RestartSec=5`
- `/home/overlord/EMS/.planning/STATE.md` — all key design decisions including ZMQ framing, socket names, buffer decisions
- `apt-cache show mosquitto` — confirms `mosquitto 2.0.18-1build3` available in Ubuntu package cache

### Secondary (MEDIUM confidence)

- `uv run python -c "import paho.mqtt.client"` — paho-mqtt confirmed available in workspace
- `uv run python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey"` — cryptography library confirmed available for Ed25519 key generation

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in workspace, confirmed via direct imports
- Architecture: HIGH — patterns derived directly from reading existing code, not assumptions
- Pitfalls: HIGH — identified from reading actual implementation (reboot in state_machine.py, cert check in config.py, ZMQ PUB bind in publisher.py)
- Test design: HIGH — directly follows existing patterns in test_m3_integration.py and test_crash_recovery.py

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable codebase, no fast-moving dependencies)
