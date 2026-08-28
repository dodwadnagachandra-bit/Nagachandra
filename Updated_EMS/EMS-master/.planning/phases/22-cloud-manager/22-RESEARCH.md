# Phase 22: Cloud Manager - Research

**Researched:** 2026-03-15
**Domain:** paho-mqtt 2.x, mTLS, asyncio/thread bridge, MQTT telemetry downsampling
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**MQTT Client Architecture**
| Aspect | Decision |
|--------|----------|
| MQTT loop | paho-mqtt `loop_start()` — dedicated background thread |
| ZMQ consumption | asyncio task subscribes to SOCK_TELEMETRY, batches messages |
| Bridge | `asyncio.Queue` from ZMQ task → MQTT publish (via `run_in_executor`) |
| Reconnection | paho-mqtt built-in reconnect with exponential backoff (1s → 60s cap) |
| TLS | `tls_set(ca_certs, certfile, keyfile, tls_version=ssl.PROTOCOL_TLS_CLIENT)` |
| QoS | Telemetry: QoS 0. Events: QoS 1. |
| Clean session | `clean_session=True` |

**Telemetry Downsampling Strategy**
| Aspect | Decision |
|--------|----------|
| Collection | ZMQ SUB receives all 1Hz messages, stores latest per topic in dict |
| Publish trigger | asyncio timer at `telemetry.interval_s` from cloud_config.yaml |
| Payload format | JSON object with all topic snapshots (single consolidated message) |
| MQTT topic | `{topic_prefix}/telemetry` |
| Timestamp | Publish timestamp (not individual message timestamps) |
| Missing topics | Omit topics with no data since last publish |

**Remote Command Handling**
| MQTT Command | ZMQ Target | ZMQ Action | Validation |
|-------------|-----------|------------|-----------|
| `mode_change` | control_cmd | mode_change | target_state in [idle, standby] |
| `setpoint` | control_cmd | manual_setpoint | power_kw is float |
| `priority` | control_cmd | source_priority | mode in [day, night, manual] |
| `fault_reset` | control_cmd | fault_reset | No params |
| `maintenance` | control_cmd | maintenance_enter/exit | action in [enter, exit] |
| `alarm_ack` | alarm_cmd | acknowledge | alarm_id is string |

Command payload: `{command: str, params: dict, request_id: str}`. Response published to `{prefix}/responses/{request_id}`. Rate limit: max 10 commands/minute.

### Claude's Discretion
- paho-mqtt client configuration (keepalive, max_inflight, reconnect_delay)
- MQTT topic naming conventions beyond prefix
- ZMQ socket lifecycle (create on startup vs per-command)
- Connection status ZMQ telemetry message format
- Heartbeat payload fields
- Test strategy (mock MQTT broker, or use paho-mqtt test fixtures)

### Deferred Ideas (OUT OF SCOPE)
- CLOUD-09: Per-cell telemetry (pending Decision #10.2)
- CLOUD-10: Cloud-initiated schedule push
- CLOUD-11: Fleet management
- AWS IoT Core / Azure IoT Hub specific integrations (pending Decision #10.1)
- Message compression for low-bandwidth sites
- MQTT 5.0 features (paho-mqtt v2.x)
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CLOUD-01 | MQTT/TLS client connects to broker (port 8883) with mTLS, automatic reconnection with exponential backoff | paho-mqtt 2.1 `tls_set()` + `reconnect_delay_set()` + `loop_start()` |
| CLOUD-02 | Telemetry forwarding: ZMQ PUB → downsample 1Hz to 10-60s → MQTT `{prefix}/telemetry` JSON | asyncio timer + latest-value dict + `loop.run_in_executor` for publish |
| CLOUD-03 | Event forwarding: ZMQ alarm events → MQTT `{prefix}/events` QoS 1 | ZMQ SUB on alarm_pub.sock + paho publish(qos=1) |
| CLOUD-06 | Remote command reception: MQTT `{prefix}/commands` → validate → ZMQ REQ to control/alarm | paho on_message callback → queue → asyncio dispatch → existing ZMQ REQ API |
| CLOUD-07 | Heartbeat: device status to `{prefix}/status` every 60s | asyncio periodic task, same timer pattern as telemetry |
| CLOUD-08 | Connection status on ZMQ telemetry topic "cloud" for HMI | ZMQ PUB on SOCK_TELEMETRY, on_connect/on_disconnect callbacks |
</phase_requirements>

---

## Summary

The cloud_manager is a thin bridging process between the local ZMQ bus and an MQTT broker. It has no business logic — it subscribes to existing telemetry/event ZMQ sockets, downsamples, serializes to JSON, and forwards via MQTT. In the reverse direction, it receives MQTT commands, validates them, and proxies to existing ZMQ REQ endpoints (identical to HMI command paths).

The key architectural challenge is bridging paho-mqtt's threaded network model with the project's asyncio-based Python modules. The locked decision uses `loop_start()` (background thread) plus `asyncio.Queue` as the thread boundary. Paho-mqtt's callbacks run in the network thread; asyncio tasks drain the queue and do ZMQ work. The `run_in_executor` pattern handles calling `client.publish()` from asyncio context safely.

paho-mqtt 2.x introduced a mandatory `CallbackAPIVersion` argument (use `CallbackAPIVersion.VERSION2`) and updated callback signatures. All V2 callbacks receive `reason_code` and `properties` parameters. This is the only significant API difference from pre-2.0.

**Primary recommendation:** Structure CloudLoop with three asyncio tasks: (1) ZMQ telemetry collector storing latest-value per topic, (2) periodic publish timer triggering MQTT telemetry and heartbeat, (3) MQTT command dispatcher consuming a queue from paho callbacks. paho-mqtt runs `loop_start()` independently.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| paho-mqtt | 2.1.0 | MQTT client with TLS, reconnect, callbacks | Specified in requirements; Eclipse Foundation reference implementation |
| pyzmq | 27.1+ (existing) | ZMQ PUB/SUB/REQ sockets to other modules | Already in workspace dev deps; used by all other Python modules |
| msgpack | 1.0+ (existing) | Decode ZMQ telemetry frames | Project-standard serialization for ZMQ |
| json (stdlib) | 3.12 | Encode MQTT payloads | Cloud consumers expect JSON; stdlib, no extra dep |
| ssl (stdlib) | 3.12 | TLS context for mTLS config | Used by tls_set(); no extra dep |
| asyncio (stdlib) | 3.12 | Event loop, tasks, queue bridge | Project-standard async pattern |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| jsonschema | 4.23+ (existing) | Validate cloud_config.yaml on load | Already in dev deps; same pattern as alarm_manager config |
| pyyaml | 6.0+ (existing) | Load cloud_config.yaml | Same pattern as all other modules |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| paho-mqtt loop_start() | asyncio-mqtt or gmqtt | asyncio-native wrappers are cleaner but paho is specified and its asyncio support is experimental; don't change |
| asyncio.Queue bridge | threading.Queue | Both thread-safe; asyncio.Queue integrates natively with await; preferred |
| json.dumps for MQTT payload | msgpack | Cloud consumers (Grafana, AWS IoT, etc.) expect JSON; msgpack would break downstream |

**Installation (add to cloud_manager pyproject.toml):**
```bash
uv add paho-mqtt==2.1.0
```

Full cloud_manager pyproject.toml dependency section:
```toml
dependencies = [
    "ems-common",
    "paho-mqtt>=2.1.0,<3.0",
]
```

---

## Architecture Patterns

### Recommended Project Structure

```
src/cloud_manager/src/ems_cloud_manager/
├── __init__.py          # version = "0.1.0" (exists)
├── __main__.py          # entry point, argparse, asyncio.run() — mirrors alarm_manager pattern
├── config.py            # load_cloud_config() with JSON Schema validation
├── loop.py              # CloudLoop class — main asyncio loop + paho bridge
├── publisher.py         # MqttPublisher — wraps paho Client, TLS setup, reconnect config
└── dispatcher.py        # CommandDispatcher — MQTT command → ZMQ REQ validation/routing
```

### Pattern 1: paho-mqtt 2.x Client Initialization (CallbackAPIVersion required)

**What:** paho-mqtt 2.0 made `CallbackAPIVersion` a required first argument. `VERSION2` changes callback signatures to always include `reason_code` and `properties`.
**When to use:** Always — it is required in paho-mqtt 2.x.

```python
# Source: eclipse.dev/paho/files/paho.mqtt.python/html/client.html
import ssl
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

client: mqtt.Client = mqtt.Client(
    callback_api_version=CallbackAPIVersion.VERSION2,
    client_id="ems-device-001",
    clean_session=True,
    protocol=mqtt.MQTTv311,
)

# mTLS — must be called before connect()
client.tls_set(
    ca_certs=config["auth"]["ca_cert_path"],
    certfile=config["auth"]["client_cert_path"],
    keyfile=config["auth"]["client_key_path"],
    tls_version=ssl.PROTOCOL_TLS_CLIENT,
)

# Exponential backoff: 1s → 60s
client.reconnect_delay_set(min_delay=1, max_delay=60)

# Start background network thread
client.loop_start()
client.connect(host, port, keepalive=60)
```

### Pattern 2: VERSION2 Callback Signatures

**What:** All callbacks in VERSION2 use updated signatures with `reason_code` and `properties`. Callbacks run in paho's network thread — do NOT await or call asyncio from them directly.

```python
# Source: eclipse.dev/paho/files/paho.mqtt.python/html/client.html
def on_connect(
    client: mqtt.Client,
    userdata: Any,
    connect_flags: mqtt.ConnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties,
) -> None:
    if reason_code.is_failure:
        logger.error("MQTT connect failed: %s", reason_code)
        return
    logger.info("MQTT connected to broker")
    client.subscribe(f"{topic_prefix}/commands", qos=1)
    # Signal asyncio world via thread-safe put_nowait
    _status_queue.put_nowait("connected")

def on_disconnect(
    client: mqtt.Client,
    userdata: Any,
    disconnect_flags: mqtt.DisconnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties,
) -> None:
    logger.warning("MQTT disconnected: %s", reason_code)
    _status_queue.put_nowait("disconnected")

def on_message(
    client: mqtt.Client,
    userdata: Any,
    message: mqtt.MQTTMessage,
) -> None:
    # Called in paho network thread — put onto queue, don't await
    _command_queue.put_nowait(message)
```

### Pattern 3: asyncio ↔ paho Thread Bridge

**What:** The bridge between paho's network thread and asyncio. Queues are the correct boundary. `asyncio.Queue` is used because asyncio tasks can `await queue.get()` natively. However, `queue.put_nowait()` must be used from the paho callback thread (not `await queue.put()`).

**Critical:** `run_in_executor` is NOT needed for `client.publish()` — paho's publish() is thread-safe and returns immediately (it enqueues internally). The network thread drains the queue. `run_in_executor` IS needed if you need to call any blocking paho method from asyncio.

```python
# Source: eclipse-paho/paho.mqtt.python GitHub thread safety issue #358
import asyncio
import queue  # Use threading.Queue for put_nowait from non-asyncio thread

# In __init__:
self._command_queue: queue.Queue = queue.Queue()
self._status_queue: queue.Queue = queue.Queue()

# In asyncio main loop — poll thread-safe queues:
async def _run_command_dispatcher(self) -> None:
    while not self._stop_event.is_set():
        try:
            msg = self._command_queue.get_nowait()
            await self._dispatch_command(msg)
        except queue.Empty:
            pass
        await asyncio.sleep(0.1)

# Calling publish() from asyncio task — direct call is safe:
async def _publish_telemetry(self, payload: dict) -> None:
    json_bytes: bytes = json.dumps(payload).encode()
    topic: str = f"{self._prefix}/telemetry"
    # publish() is thread-safe in paho — no run_in_executor needed
    self._client.publish(topic, json_bytes, qos=0)
```

### Pattern 4: ZMQ PUB on SOCK_TELEMETRY for Cloud Status (CLOUD-08)

**What:** cloud_manager must publish its connection state on the main telemetry PUB socket (topic "cloud") so the HMI can display connectivity. It connects (not binds) to SOCK_TELEMETRY as a PUB socket publisher — consistent with how other modules write telemetry.

Wait — checking the IPC pattern: `data_manager` binds SOCK_TELEMETRY; other modules subscribe. For cloud_manager to publish cloud status on the telemetry bus, it needs to publish to a socket that HMI subscribes to. The correct approach is to use a dedicated ZMQ PUB socket that the HMI server subscribes to, OR to use the existing SOCK_TELEMETRY if it supports multiple publishers (requires a router/proxy or the socket supports multiple connects).

**Verified from ipc.py:** SOCK_TELEMETRY is `ipc:///run/ems/telemetry.sock` — data_manager binds it as PUB. Other modules SUB to it. For cloud_manager to also publish on it, it would need to bind a separate publisher and hmi_server would subscribe to both endpoints — OR cloud_manager publishes via a ZMQ PUSH to a socket that data_manager relays.

**Recommendation (Claude's discretion):** Cloud_manager should bind its own PUB socket at `ipc:///run/ems/cloud_pub.sock` (following alarm_manager's pattern of `alarm_pub.sock`), and hmi_server subscribes to it. This avoids modifying data_manager. The CONTEXT.md says "ZMQ PUB on SOCK_TELEMETRY (topic: cloud)" — interpret as: publish using the same pattern/topic naming as other telemetry, but on a separate cloud-specific PUB endpoint. Define `SOCK_CLOUD_PUB = "ipc:///run/ems/cloud_pub.sock"` in loop.py (same precedent as alarm_manager defining SOCK_ALARM_PUB in its loop.py).

### Pattern 5: Latest-Value Telemetry Accumulator

**What:** The ZMQ telemetry subscriber stores the latest msgpack-decoded payload per topic. A periodic asyncio timer fires at `interval_s` and publishes the accumulated snapshot as a single JSON MQTT message.

```python
# Accumulator dict — updated at 1Hz from ZMQ, published at 10-60s via timer
self._telemetry_snapshot: dict[str, Any] = {}

async def _zmq_telemetry_collector(self) -> None:
    """Drain ZMQ telemetry SUB socket, store latest value per topic."""
    while not self._stop_event.is_set():
        try:
            frames: list[bytes] = self._sub.recv_multipart(zmq.NOBLOCK)
            if len(frames) >= 2:
                topic: str = frames[0].decode()
                msg: dict = msgpack.unpackb(frames[1], raw=False)
                # Store payload (not envelope) keyed by topic
                self._telemetry_snapshot[topic] = msg.get("payload", msg)
        except zmq.Again:
            pass
        await asyncio.sleep(0.0)  # yield to other tasks

async def _periodic_publish(self) -> None:
    """Publish consolidated telemetry snapshot at configured interval."""
    while not self._stop_event.is_set():
        await asyncio.sleep(self._interval_s)
        if not self._telemetry_snapshot:
            continue
        payload: dict = {
            "ts": int(time.time() * 1000),
            "data": dict(self._telemetry_snapshot),  # shallow copy
        }
        self._telemetry_snapshot.clear()  # reset after snapshot
        self._client.publish(
            f"{self._prefix}/telemetry",
            json.dumps(payload).encode(),
            qos=0,
        )
```

### Anti-Patterns to Avoid

- **Calling asyncio functions from paho callbacks:** Callbacks run in paho's network thread. Calling `asyncio.Queue.put()` (awaitable) or `loop.call_soon_threadsafe()` without the actual loop reference will deadlock or fail. Use `threading.Queue.put_nowait()` instead.
- **Using `clean_session=False`:** CONTEXT.md specifies `True`. Avoids broker accumulating queued messages for a device that sends data rather than receives it.
- **Calling `tls_set()` after `connect()`:** paho enforces that TLS must be configured before connecting. Order: `tls_set()` → `reconnect_delay_set()` → `loop_start()` → `connect()`.
- **Forgetting CallbackAPIVersion:** paho-mqtt 2.x raises `TypeError` at Client instantiation without it. Use `CallbackAPIVersion.VERSION2`.
- **Re-subscribing on every reconnect without checking:** paho with `clean_session=True` loses subscriptions on reconnect. Re-subscribe in `on_connect` callback (runs after every reconnect, not just first connect).
- **Blocking the asyncio loop with ZMQ recv:** The ZMQ SUB socket must be polled with `NOBLOCK` flag in a tight loop with `await asyncio.sleep(0.0)` yield, or use `asyncio.get_event_loop().run_in_executor()` for blocking recv. Prefer NOBLOCK pattern matching alarm_manager.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MQTT reconnection | Custom reconnect timer | paho `reconnect_delay_set()` + `loop_start()` | Built-in exponential backoff handles TCP errors, auth failures, broker restart |
| TLS mutual auth | Custom SSL socket | paho `tls_set(ca_certs, certfile, keyfile)` | paho wraps ssl.SSLContext correctly for MQTT framing |
| Thread-safe publish queue | Custom lock around socket | paho publish() is internally thread-safe | paho 2.x removed mutexes, packet queue is thread-safe by design |
| JSON schema validation | Manual type checks | jsonschema Draft202012Validator (existing pattern) | Same as every other module — config.py validates at startup |
| MQTT topic construction | Ad-hoc f-strings everywhere | Single prefix constant + topic constants | Prevents typos in topic names that are hard to debug |

**Key insight:** paho-mqtt is a complete MQTT client implementation. The cloud_manager's job is configuration and wiring, not MQTT protocol implementation.

---

## Common Pitfalls

### Pitfall 1: paho-mqtt 2.x Breaking API Without CallbackAPIVersion
**What goes wrong:** `TypeError: Client.__init__() missing 1 required positional argument: 'callback_api_version'` at startup.
**Why it happens:** paho-mqtt 2.0 made this required. Code written for 1.x will fail immediately.
**How to avoid:** Always instantiate with `mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, ...)`.
**Warning signs:** Import succeeds but Client() raises TypeError.

### Pitfall 2: VERSION2 Callback Signature Mismatch
**What goes wrong:** `on_connect` defined with old signature `(client, userdata, flags, rc)` causes TypeError when paho calls it with 5 args.
**Why it happens:** VERSION2 adds `connect_flags` and `reason_code` replaces `rc` (int).
**How to avoid:** Use exact VERSION2 signatures: `on_connect(client, userdata, connect_flags, reason_code, properties)`.
**Warning signs:** MQTT connects but `on_connect` throws unhandled exception in paho's network thread (logged but not propagated to asyncio).

### Pitfall 3: asyncio.Queue from paho Callback Thread
**What goes wrong:** Calling `await queue.put(item)` from paho's network thread crashes with "coroutine called from non-asyncio thread".
**Why it happens:** `asyncio.Queue` methods are not thread-safe; they assume they run inside the event loop.
**How to avoid:** Use `threading.Queue` (not `asyncio.Queue`) as the bridge. Asyncio side uses `queue.get_nowait()` polled in a tight loop, or `loop.call_soon_threadsafe()` to schedule work.
**Warning signs:** Intermittent `RuntimeError: There is no current event loop` in paho callbacks.

### Pitfall 4: Cert Paths Don't Exist in Dev
**What goes wrong:** `FileNotFoundError` or `ssl.SSLError` at `tls_set()` when `/etc/ems/certs/` doesn't exist on dev machine.
**Why it happens:** Production cert paths are deployment-specific.
**How to avoid:** config.py must validate cert path existence at startup and raise `FileNotFoundError` with a helpful message. Tests use `protocol: mqtt` (no TLS) via a test config override. Generate self-signed certs for integration testing with `openssl`.
**Warning signs:** Module fails to start in dev without clear error.

### Pitfall 5: Missing Re-subscribe After Reconnect
**What goes wrong:** After broker restart or network outage, commands stop being received.
**Why it happens:** `clean_session=True` means broker discards subscriptions on disconnect. paho does NOT automatically re-subscribe.
**How to avoid:** Always call `client.subscribe(...)` inside `on_connect` (not just once at startup). `on_connect` fires on every successful connection, including reconnects.
**Warning signs:** Commands work after first connect, stop working after any network blip.

### Pitfall 6: Offline Buffer Hook Point
**What goes wrong:** Phase 23 (offline buffer) has nowhere to plug in if publish path is not designed for it.
**Why it happens:** Cloud_manager Phase 22 doesn't implement buffering, but Phase 23 needs to intercept the publish path.
**How to avoid:** Extract publish into a `_publish_telemetry(payload)` method. Phase 23 wraps this with buffer logic. If MQTT disconnected, route to buffer; if connected, publish directly. The connection status flag `self._connected: bool` must be maintained.
**Warning signs:** Phase 23 has to rewrite the publish path.

### Pitfall 7: Rate Limiting Command Input
**What goes wrong:** A misconfigured cloud automation sends hundreds of commands per minute, flooding the control_cmd ZMQ REQ socket.
**Why it happens:** MQTT QoS 1 guarantees delivery but not rate. Automation bugs can cause storms.
**How to avoid:** CONTEXT.md specifies max 10 commands/minute. Implement a token bucket or simple counter with timestamp window in dispatcher.py.
**Warning signs:** control_manager logs excessive command traffic.

---

## Code Examples

Verified patterns from official sources and existing codebase:

### Cloud Manager Entry Point (mirrors alarm_manager __main__.py)
```python
# Pattern: src/alarm_manager/src/ems_alarm_manager/__main__.py
import asyncio
import signal
from ems_cloud_manager.config import load_cloud_config
from ems_cloud_manager.loop import CloudLoop

async def run(args: argparse.Namespace) -> None:
    config = load_cloud_config(args.config)
    loop_obj = CloudLoop(
        config,
        telemetry_sub_endpoint=os.environ.get("EMS_TELEMETRY_SUB_ENDPOINT"),
        control_cmd_endpoint=os.environ.get("EMS_CONTROL_CMD_ENDPOINT"),
        alarm_cmd_endpoint=os.environ.get("EMS_ALARM_CMD_ENDPOINT"),
        logger_push_endpoint=os.environ.get("EMS_LOGGER_PUSH_ENDPOINT"),
        cloud_pub_endpoint=os.environ.get("EMS_CLOUD_PUB_ENDPOINT"),
    )
    asyncio_loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, lambda: loop_obj.stop_event.set())
    await loop_obj.run()
    loop_obj.cleanup()
```

### Config Loader (mirrors alarm_manager config.py)
```python
# Source: existing pattern in src/alarm_manager/src/ems_alarm_manager/config.py
from jsonschema import Draft202012Validator

def load_cloud_config(path: Path, schema_path: Path | None = None) -> dict[str, Any]:
    # Load + validate same pattern as alarm_manager
    # Raises FileNotFoundError if cert paths absent (validate in config, not at connect)
    ...
```

### paho-mqtt Client with mTLS (paho-mqtt 2.1 API)
```python
# Source: eclipse.dev/paho/files/paho.mqtt.python/html/client.html
import ssl
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

self._client: mqtt.Client = mqtt.Client(
    callback_api_version=CallbackAPIVersion.VERSION2,
    client_id=f"ems-{device_id}",
    clean_session=True,
    protocol=mqtt.MQTTv311,
)
self._client.tls_set(
    ca_certs=auth["ca_cert_path"],
    certfile=auth["client_cert_path"],
    keyfile=auth["client_key_path"],
    tls_version=ssl.PROTOCOL_TLS_CLIENT,
)
self._client.reconnect_delay_set(min_delay=1, max_delay=60)
self._client.on_connect = self._on_connect
self._client.on_disconnect = self._on_disconnect
self._client.on_message = self._on_message
self._client.loop_start()
self._client.connect(broker["host"], broker["port"], keepalive=60)
```

### VERSION2 on_connect with re-subscribe
```python
# Source: eclipse.dev/paho/files/paho.mqtt.python/html/client.html
def _on_connect(
    self,
    client: mqtt.Client,
    userdata: Any,
    connect_flags: mqtt.ConnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties,
) -> None:
    if reason_code.is_failure:
        logger.error("MQTT connect failed: %s", reason_code)
        return
    # Re-subscribe every connect (clean_session=True loses subs on disconnect)
    client.subscribe(f"{self._prefix}/commands", qos=1)
    self._connected = True
    # Signal asyncio world via thread-safe queue
    self._status_queue.put_nowait({"state": "connected", "ts": int(time.time() * 1000)})
    logger.info("MQTT connected: %s", reason_code)
```

### Connection Status ZMQ Publish (CLOUD-08)
```python
# Publish cloud connection status on dedicated PUB socket for HMI
# topic = "cloud", payload = msgpack-encoded status dict
from ems_common.ipc import encode_telemetry

def _publish_cloud_status(self, state: str) -> None:
    payload: dict = {
        "state": state,          # "connected" | "disconnected" | "reconnecting"
        "broker": self._broker_host,
        "latency_ms": self._last_latency_ms,
    }
    raw: bytes = encode_telemetry(
        timestamp_ms=int(time.time() * 1000),
        seq=self._seq,
        source="cloud_manager",
        topic="cloud",
        payload=payload,
    )
    # Publish on SOCK_CLOUD_PUB (not SOCK_TELEMETRY — cloud_manager doesn't bind telemetry.sock)
    self._cloud_pub.send_string("cloud", zmq.SNDMORE | zmq.NOBLOCK)
    self._cloud_pub.send(raw, zmq.NOBLOCK)
    self._seq += 1
```

### Command Dispatch via ZMQ REQ
```python
# Source: existing HMI/scheduler ZMQ REQ pattern in ems_common/ipc.py
from ems_common.ipc import encode_command_request, decode_command_response, STATUS_OK

async def _dispatch_command(self, mqtt_msg: MQTTMessage) -> None:
    try:
        cmd: dict = json.loads(mqtt_msg.payload)
        command: str = cmd["command"]
        params: dict = cmd.get("params", {})
        request_id: str = cmd["request_id"]
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Invalid command payload: %s", exc)
        return

    # Rate limit check
    if not self._rate_limiter.allow():
        self._publish_response(request_id, "error", error_msg="Rate limit exceeded")
        return

    # Route to correct ZMQ endpoint
    zmq_action, zmq_endpoint = self._route_command(command, params)
    if zmq_action is None:
        self._publish_response(request_id, "error", error_msg=f"Unknown command: {command}")
        return

    # Forward via ZMQ REQ (same API as HMI)
    raw: bytes = encode_command_request(zmq_action, params)
    await asyncio.get_event_loop().run_in_executor(
        None, self._zmq_req_send, zmq_endpoint, raw, request_id
    )
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `mqtt.Client()` (no version arg) | `mqtt.Client(CallbackAPIVersion.VERSION2)` | paho-mqtt 2.0.0 (2024) | All existing code using 1.x API must update |
| `on_connect(client, userdata, flags, rc)` | `on_connect(client, userdata, connect_flags, reason_code, properties)` | paho-mqtt 2.0.0 | Callback must use 5-arg signature with VERSION2 |
| Internal mutexes in paho publish | Thread-safe packet queue | paho-mqtt 2.0.0 | `publish()` safe to call from any thread |
| QoS>0 message retry during connection | Retry only on reconnect | paho-mqtt 2.0.0 | Simplifies retry semantics; less unexpected behavior |

**Deprecated/outdated:**
- `CallbackAPIVersion.VERSION1`: Deprecated in 2.0, planned removal in 3.0. Do NOT use.
- `max_packets` argument in `loop()`: Removed in 2.0.
- `force` argument in `loop_stop()`: Removed in 2.0.
- `tls_insecure_set()` before `tls_set()`: Invalid since 1.3.0 (must be after). Still applies.

---

## Open Questions

1. **SOCK_TELEMETRY and cloud status publication (CLOUD-08)**
   - What we know: CONTEXT.md says "ZMQ PUB on SOCK_TELEMETRY (topic: cloud)". data_manager binds SOCK_TELEMETRY as PUB — ZMQ PUB sockets can have multiple binders only via XPUB proxy.
   - What's unclear: Whether hmi_server already subscribes to a separate cloud_pub socket, or expects cloud status on the main telemetry socket.
   - Recommendation: Define `SOCK_CLOUD_PUB = "ipc:///run/ems/cloud_pub.sock"` in loop.py (same pattern as alarm_manager's SOCK_ALARM_PUB). hmi_server subscribes to both. If hmi_server already connects to SOCK_TELEMETRY, this is a Phase 22 extension point; coordinate with Phase 25 (hmi_server cloud screen) if needed.

2. **Test TLS without production certs**
   - What we know: `/etc/ems/certs/` won't exist in dev/CI.
   - What's unclear: Whether to generate self-signed certs in a test fixture or use `protocol: mqtt` (no TLS) for unit tests.
   - Recommendation: Use `protocol: mqtt` in test config override and a local Mosquitto instance in Docker for integration tests. Unit tests mock the paho client entirely (test CloudLoop logic without real MQTT). CI does not require TLS cert setup.

3. **ZMQ REQ socket lifecycle for command dispatch**
   - What we know: CONTEXT.md marks this as Claude's discretion.
   - What's unclear: Whether to create one persistent REQ socket per target (control_cmd, alarm_cmd) and keep it open, or create per-command.
   - Recommendation: Create one REQ socket per target at startup (same as HMI pattern). ZMQ REQ sockets are not connection-oriented at the application level — they work as long as the endpoint process is running. Persistent sockets avoid reconnect overhead on every command.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.3.x |
| Config file | `/home/overlord/EMS/pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/test_cloud_manager.py -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLOUD-01 | mTLS client connects with cert paths, reconnects on failure | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_mtls_connect -x` | ❌ Wave 0 |
| CLOUD-01 | Exponential backoff configuration applied (min=1s, max=60s) | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_reconnect_backoff -x` | ❌ Wave 0 |
| CLOUD-02 | Latest-value accumulator stores correct value per topic | unit | `uv run pytest tests/test_cloud_manager.py::test_telemetry_accumulator -x` | ❌ Wave 0 |
| CLOUD-02 | Periodic publish fires at configured interval | unit (mock timer) | `uv run pytest tests/test_cloud_manager.py::test_periodic_publish -x` | ❌ Wave 0 |
| CLOUD-02 | Missing topics omitted from payload (not sent as null) | unit | `uv run pytest tests/test_cloud_manager.py::test_missing_topics_omitted -x` | ❌ Wave 0 |
| CLOUD-03 | Alarm events from ZMQ published to MQTT with QoS 1 | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_event_qos1 -x` | ❌ Wave 0 |
| CLOUD-06 | Valid command JSON → correct ZMQ REQ forwarded | unit (mock ZMQ) | `uv run pytest tests/test_cloud_manager.py::test_command_dispatch -x` | ❌ Wave 0 |
| CLOUD-06 | Invalid command payload rejected (no ZMQ forward) | unit | `uv run pytest tests/test_cloud_manager.py::test_command_invalid_rejected -x` | ❌ Wave 0 |
| CLOUD-06 | Rate limit (>10/min) blocks excess commands | unit | `uv run pytest tests/test_cloud_manager.py::test_command_rate_limit -x` | ❌ Wave 0 |
| CLOUD-07 | Heartbeat published at 60s interval with required fields | unit (mock timer) | `uv run pytest tests/test_cloud_manager.py::test_heartbeat_payload -x` | ❌ Wave 0 |
| CLOUD-08 | Connection status published on ZMQ cloud_pub after connect/disconnect | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_connection_status_zmq -x` | ❌ Wave 0 |
| CLOUD-01 | Config load rejects missing cert paths (FileNotFoundError) | unit | `uv run pytest tests/test_cloud_manager.py::test_config_cert_validation -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_cloud_manager.py -x`
- **Per wave merge:** `uv run pytest tests/ -x -m 'not integration and not slow and not rtu'`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_cloud_manager.py` — all CLOUD-01 through CLOUD-08 unit tests (mock paho.mqtt.client)
- [ ] `tests/conftest.py` — already exists; may need mock_paho_client fixture added
- [ ] paho-mqtt install: `uv add paho-mqtt>=2.1.0,<3.0` in `src/cloud_manager/pyproject.toml`

---

## Sources

### Primary (HIGH confidence)
- [eclipse.dev paho-mqtt client docs](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html) — CallbackAPIVersion, tls_set, reconnect_delay_set, loop_start, VERSION2 callback signatures
- [eclipse.dev paho-mqtt changelog](https://eclipse.dev/paho/files/paho.mqtt.python/html/changelog.html) — 2.0 breaking changes, removed mutexes, thread safety
- Existing codebase: `src/alarm_manager/src/ems_alarm_manager/` — `__main__.py`, `loop.py`, `config.py` — established module patterns (HIGH confidence, directly inspected)
- Existing codebase: `src/common/python/src/ems_common/ipc.py` — ZMQ socket constants, encode/decode helpers (HIGH confidence, directly inspected)
- Existing codebase: `config/cloud_config.yaml`, `config/schemas/cloud_config.schema.json` — config structure and constraints (HIGH confidence, directly inspected)

### Secondary (MEDIUM confidence)
- [PyPI paho-mqtt 2.1.0](https://pypi.org/project/paho-mqtt/) — current stable version confirmed 2.1.0 (April 2024)
- [GitHub eclipse-paho/paho.mqtt.python](https://github.com/eclipse-paho/paho.mqtt.python) — thread safety of publish(), CallbackAPIVersion migration requirement
- [EMQ Python MQTT guide 2025](https://www.emqx.com/en/blog/how-to-use-mqtt-in-python) — mTLS tls_set usage patterns verified against official docs

### Tertiary (LOW confidence)
- WebSearch results on asyncio+paho bridge patterns — patterns verified against official threading.Queue approach; community examples consistent with official thread safety guidance

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — paho-mqtt 2.1.0 current stable, confirmed via PyPI; all supporting libraries already in workspace
- Architecture: HIGH — patterns are direct extensions of existing alarm_manager/scheduler module patterns, verified in codebase
- paho-mqtt 2.x API: HIGH — verified via official eclipse.dev docs and changelog
- Thread bridge pattern: MEDIUM — threading.Queue approach confirmed correct by multiple sources and GitHub issue #358; asyncio.Queue cross-thread limitation is well-documented Python behavior
- Pitfalls: HIGH — all identified pitfalls are directly verifiable from paho changelog or codebase inspection

**Research date:** 2026-03-15
**Valid until:** 2026-06-15 (paho-mqtt 2.x is stable; no major changes expected in 90 days)
