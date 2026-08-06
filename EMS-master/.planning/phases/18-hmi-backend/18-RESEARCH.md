# Phase 18: HMI Backend - Research

**Researched:** 2026-03-15
**Domain:** FastAPI backend, WebSocket telemetry, ZMQ proxy, PIN authentication
**Confidence:** HIGH

## Summary

Phase 18 builds the HMI backend: a FastAPI application that serves the React frontend as static files, bridges 1Hz ZMQ telemetry to browser WebSocket clients as JSON, proxies REST commands to control_manager and alarm_manager via ZMQ REQ/REP, and authenticates users via bcrypt PIN with in-memory bearer tokens.

The project has strong existing patterns to follow. All Python modules use the same async structure (asyncio.run, SIGTERM/SIGINT handlers, cleanup), ZMQ socket patterns are well established (SUB for telemetry, REQ/REP for commands), and the IPC contract is fully defined in `ems_common/ipc.py`. The hmi_server stub exists as a near-empty package with only `__init__.py`. The frontend scaffold (React 19 + Vite 6 + Tailwind 4) is already set up in `src/hmi_server/frontend/`.

**Primary recommendation:** Follow the modular FastAPI structure defined in CONTEXT.md decisions, reuse `ems_common/ipc.py` encode/decode helpers exactly as alarm_manager and control_manager do, and use FastAPI's lifespan context manager for ZMQ lifecycle.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **WebSocket telemetry:** Single ZMQ SUB background task subscribes to all topics, broadcasts JSON to per-client asyncio.Queue (max 100, drop oldest on overflow). Message format: `{topic: str, data: dict, ts: int}`.
- **REST command proxy:** Thin 1:1 mapping of REST endpoints to ZMQ commands. Dedicated REQ socket per request with 5s timeout. HTTP 504 on ZMQ timeout. `run_in_executor` for blocking ZMQ.
- **PIN authentication:** Bearer token (random 32-byte hex, not JWT). In-memory token store `{token: {level, expires_at}}`. bcrypt.checkpw against config hashes. Max 10 concurrent sessions. POST `/api/auth/login` and `/api/auth/logout`.
- **App structure:** Modular routers -- `app.py` (factory + lifespan), `auth.py`, `control.py`, `alarm.py`, `query.py`, `ws.py`, `health.py`, shared `deps.py`.
- **REST endpoint table:** 10 endpoints defined (see CONTEXT.md for full table).
- **Static files:** Mounted at `/` with `StaticFiles(directory="frontend/dist")`, index.html fallback for SPA routing.

### Claude's Discretion
- Uvicorn configuration (workers, host binding, log level)
- ZMQ socket creation pattern in async context (run_in_executor vs asyncio poller)
- WebSocket message batching (send every message vs aggregate per-topic per second)
- Pydantic model definitions for request/response validation
- Error response format (FastAPI HTTPException vs custom error handler)
- Test strategy (httpx.AsyncClient for FastAPI testing, WebSocket test patterns)

### Deferred Ideas (OUT OF SCOPE)
- HTTPS/TLS for HMI server (M4)
- WebSocket topic filtering per client
- Rate limiting on REST endpoints
- Audit logging of HMI commands
- Multi-language support (HMI-14)
- PDF report generation (HMI-15)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| HMI-01 | FastAPI serves React static files on configurable HTTP port (default 8080), CORS and security headers | Config structure in hmi_config.yaml fully documented; StaticFiles mount pattern verified; CORS via FastAPI CORSMiddleware |
| HMI-02 | WebSocket streams 1Hz telemetry from ZMQ SUB to clients as JSON | ZMQ PUB format is multipart [topic_str, msgpack_envelope]; decode_telemetry helper exists; logger's TelemetryWriter shows SUB subscription pattern |
| HMI-03 | REST API proxies commands to control/alarm managers via ZMQ REQ/REP | encode_command_request/decode_command_response helpers exist; control_manager dispatches mode_change/manual_setpoint/fault_reset/maintenance_enter/maintenance_exit/source_priority; alarm_manager dispatches acknowledge/get_active_alarms/get_alarm_config; logger QueryServer dispatches query with type param |
| HMI-04 | PIN auth with operator/admin tiers, bcrypt hashed, configurable session timeout | hmi_config.yaml has auth.operator_pin_hash, auth.admin_pin_hash, auth.session_timeout_s; bcrypt needs adding to pyproject.toml |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | >=0.115 | Async web framework | Project standard (CLAUDE.md) |
| uvicorn | >=0.34 | ASGI server | Standard FastAPI server |
| pyzmq | >=27.1.0 | ZMQ bindings | Already in workspace dev deps |
| msgpack | >=1.0 | MessagePack serialization | Already in workspace dev deps |
| bcrypt | >=4.0 | PIN hashing | Specified in CONTEXT.md decisions |
| pyyaml | >=6.0 | Config loading | Already in workspace dev deps |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| ems-common | workspace | IPC defs, RTDB structs | Always -- single source of truth for ZMQ paths and message encoding |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| bcrypt | passlib | passlib adds unnecessary abstraction for simple PIN check |
| opaque tokens | PyJWT | JWT overkill for single-process embedded kiosk (decision locked) |

**Installation:**
```bash
cd src/hmi_server
uv add fastapi uvicorn bcrypt
```

Note: pyzmq, msgpack, pyyaml are workspace dev dependencies and available at runtime. The `ems-common` workspace dependency is already declared in pyproject.toml.

## Architecture Patterns

### Recommended Project Structure
```
src/hmi_server/
  src/ems_hmi_server/
    __init__.py          # Package init (exists)
    __main__.py          # Entry point: parse args, asyncio.run, signal handlers
    app.py               # FastAPI app factory, lifespan context, static mount
    auth.py              # /api/auth router, token store, auth dependency
    control.py           # /api/control router, ZMQ REQ proxy
    alarm.py             # /api/alarm router, ZMQ REQ proxy
    query.py             # /api/query router, ZMQ REQ proxy to logger_query
    ws.py                # /ws WebSocket endpoint, ZMQ SUB bridge
    health.py            # /api/health router
    deps.py              # Shared dependencies: ZMQ socket factory, auth injection
    models.py            # Pydantic request/response models
    config.py            # Load and validate hmi_config.yaml
  tests/
    __init__.py
    conftest.py          # Shared fixtures (test app, mock ZMQ)
    test_auth.py
    test_control.py
    test_alarm.py
    test_query.py
    test_ws.py
    test_health.py
    test_app.py
  pyproject.toml
  frontend/             # React app (exists, Phase 19 scope)
```

### Pattern 1: Entry Point Structure (from alarm_manager/__main__.py)
**What:** Async entry point with signal handling
**When to use:** The `__main__.py` module
**Example:**
```python
# Source: src/alarm_manager/src/ems_alarm_manager/__main__.py
async def run(args: argparse.Namespace) -> None:
    config = load_hmi_config(args.config)
    # Uvicorn programmatic start with the FastAPI app
    server = uvicorn.Server(uvicorn.Config(
        app=create_app(config),
        host=config["server"]["host"],
        port=config["server"]["http_port"],
        log_level="info",
    ))
    await server.serve()

def main() -> None:
    args = parse_args()
    logging.basicConfig(...)
    asyncio.run(run(args))
```

### Pattern 2: ZMQ REQ Proxy (from control_manager command dispatch)
**What:** Send ZMQ REQ, await REP, translate to HTTP response
**When to use:** All REST command endpoints
**Example:**
```python
# Based on: control_manager/loop.py _poll_commands + alarm_manager/loop.py _dispatch_command
async def zmq_command(socket_path: str, action: str, params: dict) -> dict:
    """Send a ZMQ REQ and return decoded response. Runs in executor."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, 5000)  # 5s timeout
    sock.connect(socket_path)
    try:
        sock.send(encode_command_request(action, params))
        reply = sock.recv()
        return decode_command_response(reply)
    except zmq.Again:
        raise HTTPException(status_code=504, detail="Backend service timeout")
    finally:
        sock.close()
        ctx.term()
```

### Pattern 3: ZMQ SUB Telemetry Bridge (from logger TelemetryWriter)
**What:** Subscribe to ZMQ PUB, decode multipart, fan-out to WebSocket clients
**When to use:** The ws.py module background task
**Example:**
```python
# Based on: logger/telemetry_writer.py recv_multipart + data_manager/publisher.py send format
# ZMQ PUB format: [topic_string_frame, msgpack_envelope_frame]
# Envelope: {ts: int, seq: int, src: str, topic: str, payload: dict}
parts = await sub_socket.recv_multipart()
topic_str = parts[0].decode("utf-8")
envelope = msgpack.unpackb(parts[1], raw=False)
# Convert to JSON-friendly format for browser
ws_message = {
    "topic": envelope["topic"],
    "data": envelope["payload"],
    "ts": envelope["ts"],
}
```

### Pattern 4: FastAPI Lifespan Context Manager
**What:** Modern startup/shutdown lifecycle
**When to use:** app.py for ZMQ context and background task management
**Example:**
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create ZMQ context, start telemetry bridge task
    zmq_ctx = zmq.Context()
    bridge_task = asyncio.create_task(telemetry_bridge(zmq_ctx, app.state.clients))
    app.state.zmq_ctx = zmq_ctx
    yield
    # Shutdown: cancel bridge, close ZMQ
    bridge_task.cancel()
    zmq_ctx.term()

app = FastAPI(lifespan=lifespan)
```

### Anti-Patterns to Avoid
- **Sharing a ZMQ REQ socket across requests:** REQ is synchronous -- must be one socket per concurrent request. Create per-request, close after use.
- **Using zmq.asyncio.Socket for REQ:** The REQ pattern is inherently blocking (send then recv). Use `run_in_executor` with sync zmq.Socket instead. The logger QueryServer uses zmq.asyncio but it processes one request at a time via a single recv loop -- the HMI proxy handles concurrent HTTP requests.
- **Sending MessagePack to browser:** Always serialize to JSON for WebSocket. Browsers have native JSON.parse() but need a library for MessagePack.
- **Using @app.on_event("startup"):** Deprecated in FastAPI. Use the lifespan context manager.
- **Mounting StaticFiles before API routers:** FastAPI matches routes in order. Mount StaticFiles last (at "/") so API routes take precedence.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CORS headers | Manual header middleware | `fastapi.middleware.cors.CORSMiddleware` | Handles preflight, origin matching, credentials |
| Request validation | Manual dict checking | Pydantic models as FastAPI dependencies | Automatic 422 responses, type coercion, OpenAPI docs |
| WebSocket lifecycle | Manual try/except around ws.receive | Starlette WebSocket context patterns | Handles disconnect, protocol errors |
| Token generation | Custom random | `secrets.token_hex(32)` | Cryptographically secure, standard library |
| bcrypt password check | Raw bcrypt calls | `bcrypt.checkpw()` | Handles encoding, timing-safe comparison |
| Static file serving | Custom file response handler | `starlette.staticfiles.StaticFiles` | Directory listing prevention, mime types, caching headers |

## Common Pitfalls

### Pitfall 1: ZMQ Multipart Frame Format
**What goes wrong:** Subscribing to ZMQ telemetry and calling `recv()` instead of `recv_multipart()`, or misinterpreting the frame structure.
**Why it happens:** The data_manager PUB sends `[topic_string, msgpack_envelope]` as two frames via `send_string(topic, SNDMORE)` + `send(envelope)`.
**How to avoid:** Always use `recv_multipart()`. Frame 0 is the topic (UTF-8 bytes), frame 1 is the msgpack-encoded envelope containing `{ts, seq, src, topic, payload}`.
**Warning signs:** Receiving garbled data, missing messages, or deserialization errors.

### Pitfall 2: ZMQ SUB Topic Prefix Matching
**What goes wrong:** Subscribing to "bms" also matches "btms" because ZMQ SUB does prefix matching.
**Why it happens:** ZMQ subscribe filters are prefix-based, not exact.
**How to avoid:** Use exact topic strings with the dot separator: subscribe to "bms.rack" (not "bms"), "system", "pcs", "gpio", "meter", "btms". The logger TelemetryWriter shows the correct subscriptions.
**Warning signs:** Receiving unexpected messages on a subscription.

### Pitfall 3: FastAPI Static Files + SPA Routing
**What goes wrong:** React Router paths (e.g., `/alarms`, `/control`) return 404 instead of serving index.html.
**Why it happens:** StaticFiles serves literal files, doesn't know about client-side routing.
**How to avoid:** Add a catch-all route that serves index.html for any path not matching `/api/*` or `/ws`. Use `html=True` parameter on StaticFiles mount OR add an explicit fallback route.
**Warning signs:** Refreshing a non-root React page returns 404.

### Pitfall 4: Blocking ZMQ in Async Context
**What goes wrong:** Calling synchronous `zmq.Socket.send()/recv()` from an async route handler blocks the event loop.
**Why it happens:** Standard zmq.Socket operations are blocking.
**How to avoid:** Wrap in `asyncio.get_event_loop().run_in_executor(None, ...)` for REQ/REP operations. For the SUB bridge, use zmq.asyncio.Socket (like the logger does) since it's a long-running receive loop.
**Warning signs:** All HTTP requests freeze when a ZMQ operation is slow.

### Pitfall 5: WebSocket Client Disconnect Handling
**What goes wrong:** Server tries to send to a disconnected WebSocket, raises WebSocketDisconnect, crashes the broadcast loop.
**Why it happens:** Client can disconnect at any time between queue put and ws.send.
**How to avoid:** Wrap each `ws.send_json()` in try/except WebSocketDisconnect, remove client from the set on disconnect.
**Warning signs:** "Connection reset" errors in logs, telemetry bridge task dying.

### Pitfall 6: bcrypt Bytes vs String
**What goes wrong:** `bcrypt.checkpw()` raises TypeError because it receives str instead of bytes.
**Why it happens:** YAML loads the hash as str, PIN input is str. bcrypt requires bytes.
**How to avoid:** Always encode: `bcrypt.checkpw(pin.encode("utf-8"), stored_hash.encode("utf-8"))`.
**Warning signs:** TypeError on login attempt.

## Code Examples

### ZMQ Socket Paths and Message Encoding (ems_common/ipc.py)
```python
# Source: src/common/python/src/ems_common/ipc.py
SOCK_TELEMETRY = "ipc:///run/ems/telemetry.sock"      # PUB by data_manager, SUB by hmi
SOCK_CONTROL_CMD = "ipc:///run/ems/control_cmd.sock"   # REP by control_manager, REQ by hmi
SOCK_ALARM_CMD = "ipc:///run/ems/alarm_cmd.sock"       # REP by alarm_manager, REQ by hmi
SOCK_LOGGER_QUERY = "ipc:///run/ems/logger_query.sock" # REP by logger, REQ by hmi

# Encoding:
encode_command_request(action: str, params: dict) -> bytes  # {action, params} msgpack
decode_command_response(data: bytes) -> dict                 # {status, result, error_msg}
decode_telemetry(data: bytes) -> dict                        # {ts, seq, src, topic, payload}
```

### Telemetry PUB/SUB Frame Format (data_manager publisher)
```python
# Source: src/data_manager/python/src/ems_data_manager/publisher.py lines 233-238
# Publisher sends multipart: [topic_string_frame, msgpack_envelope_frame]
self._pub.send_string(topic, zmq.SNDMORE)  # Frame 0: topic as string
self._pub.send(envelope)                    # Frame 1: msgpack bytes

# Topics published at 1Hz:
# "bms.rack.0", "bms.rack.1", ..., "pcs", "gpio", "meter", "btms", "system"
```

### Subscriber Pattern (logger TelemetryWriter)
```python
# Source: src/logger/python/src/ems_logger/telemetry_writer.py lines 227-277
sub = zmq_ctx.socket(zmq.SUB)
sub.setsockopt_string(zmq.SUBSCRIBE, "bms.rack")
sub.setsockopt_string(zmq.SUBSCRIBE, "pcs")
sub.setsockopt_string(zmq.SUBSCRIBE, "gpio")
sub.setsockopt_string(zmq.SUBSCRIBE, "meter")
sub.setsockopt_string(zmq.SUBSCRIBE, "btms")
sub.setsockopt_string(zmq.SUBSCRIBE, "system")
sub.connect(SOCK_TELEMETRY)

# Receiving:
parts = await sub.recv_multipart()
topic_str = parts[0].decode("utf-8")
envelope = msgpack.unpackb(parts[1], raw=False)
# envelope = {ts: int, seq: int, src: str, topic: str, payload: dict}
```

### Command REQ/REP Pattern (control_manager dispatch actions)
```python
# Source: src/control_manager/python/src/ems_control_manager/loop.py lines 338-379
# Available control_manager actions:
#   "mode_change"       params: {target_state: str}
#   "manual_setpoint"   params: {power_kw: float}
#   "fault_reset"       params: {}
#   "maintenance_enter" params: {}
#   "maintenance_exit"  params: {}
#   "source_priority"   params: {mode: str}  # "manual", "day", "night"

# Available alarm_manager actions (src/alarm_manager/loop.py lines 286-312):
#   "get_active_alarms" params: {} -> result: {alarms: list[dict]}
#   "acknowledge"       params: {alarm_id: str} -> result: {alarm_id, from_state, to_state}
#   "get_alarm_config"  params: {} -> result: {rules: list[dict]}

# Logger query actions (src/logger/query_handler.py lines 635-718):
#   "query" params: {type: "time_series|latest|range_stats|event_log|energy_totals|cell_snapshot", ...}
```

### HMI Config Structure (hmi_config.yaml)
```yaml
# Source: config/hmi_config.yaml
server:
  http_port: 8080          # range: 1024-65535
  websocket_port: 8081     # separate port (may not be needed if WS shares HTTP port)
  host: "0.0.0.0"

auth:
  operator_pin_hash: "$2b$12$placeholder"
  admin_pin_hash: "$2b$12$placeholder"
  session_timeout_s: 1800  # range: 60-7200

display:
  refresh_interval_ms: 1000
  kiosk_mode: false
  screen_size: "10inch"
```

## Integration Points

### ZMQ Sockets Used by HMI Backend
| Socket | Type | Direction | Endpoint | Purpose |
|--------|------|-----------|----------|---------|
| Telemetry SUB | SUB | connect | `ipc:///run/ems/telemetry.sock` | 1Hz telemetry from data_manager |
| Control CMD | REQ | connect | `ipc:///run/ems/control_cmd.sock` | Commands to control_manager |
| Alarm CMD | REQ | connect | `ipc:///run/ems/alarm_cmd.sock` | Commands to alarm_manager |
| Logger Query | REQ | connect | `ipc:///run/ems/logger_query.sock` | Data queries to logger |

### Config Schema Notes
- The config schema defines a separate `websocket_port` (8081), but the CONTEXT.md decision has WebSocket on the same FastAPI app. The WebSocket endpoint `/ws` runs on the same HTTP port. The `websocket_port` config field may be unused or could be used for a future standalone WebSocket server. **Recommendation:** Use the `http_port` for everything (FastAPI serves both HTTP and WebSocket on the same port). Document that `websocket_port` is reserved for future use.

### RTDB Data Shapes (for telemetry payload reference)
The telemetry payloads sent by data_manager contain these fields per topic:
- **system:** control_state, source_priority, active_setpoint_kw, total_soc, total_power_kw, total_energy_kwh, ems_uptime_s, pcs_command, active_derating_pct
- **pcs:** ac_voltage, ac_current, active_power, reactive_power, dc_voltage, dc_current, frequency, temperature, state, fault_code
- **gpio:** di[0-7], do_state[0-7]
- **meter:** voltage, current, active_power, reactive_power, frequency, power_factor, energy_import, energy_export
- **btms:** inlet_temp, outlet_temp, fan_speed_pct, cooling_active
- **bms.rack.N:** pack_v, pack_i, pack_soc, pack_soh, min/max/avg_cell_v, min/max/avg_cell_t, fault_code, online

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")` | `lifespan` context manager | FastAPI 0.93+ | Must use lifespan for startup/shutdown |
| Socket.IO for WebSocket | Native Starlette WebSocket | N/A | No extra dependency needed |
| JWT for embedded auth | Opaque token (decision locked) | N/A | Simpler for single-process kiosk |

## Open Questions

1. **WebSocket port vs HTTP port**
   - What we know: Config schema defines separate `websocket_port: 8081`, but CONTEXT.md decision puts WebSocket on the FastAPI app (same port).
   - What's unclear: Whether to use the separate port or ignore it.
   - Recommendation: Use single port (http_port). FastAPI/Starlette WebSocket runs on the same ASGI server. The `websocket_port` config field can be ignored for now (don't remove from schema -- just don't use it).

2. **ZMQ async pattern for REQ sockets**
   - What we know: CONTEXT.md says "use run_in_executor for blocking ZMQ send/recv". Logger QueryServer uses zmq.asyncio for its single-threaded REP loop.
   - What's unclear: Whether to use zmq.asyncio.Context or plain zmq.Context for the REQ proxy.
   - Recommendation: Use plain `zmq.Context` with `run_in_executor` for REQ sockets (create-send-recv-close per request). Use `zmq.asyncio.Context` + `zmq.asyncio.Socket` for the long-running SUB bridge task (matches logger pattern).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-asyncio |
| Config file | `src/hmi_server/pyproject.toml` (needs [tool.pytest.ini_options]) |
| Quick run command | `cd src/hmi_server && uv run pytest tests/ -x -q` |
| Full suite command | `cd src/hmi_server && uv run pytest tests/ -v --timeout=30` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HMI-01 | Static file serving, CORS headers, configurable port | integration | `uv run pytest tests/test_app.py -x` | No -- Wave 0 |
| HMI-02 | WebSocket streams 1Hz telemetry as JSON | integration | `uv run pytest tests/test_ws.py -x` | No -- Wave 0 |
| HMI-03 | REST proxy to control/alarm/logger via ZMQ | integration | `uv run pytest tests/test_control.py tests/test_alarm.py tests/test_query.py -x` | No -- Wave 0 |
| HMI-04 | PIN auth, token lifecycle, session timeout | unit | `uv run pytest tests/test_auth.py -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `cd src/hmi_server && uv run pytest tests/ -x -q`
- **Per wave merge:** `cd src/hmi_server && uv run pytest tests/ -v --timeout=30`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `src/hmi_server/tests/__init__.py` -- package init
- [ ] `src/hmi_server/tests/conftest.py` -- shared fixtures (test FastAPI app via httpx.AsyncClient, mock ZMQ endpoints using tcp://)
- [ ] `src/hmi_server/tests/test_auth.py` -- covers HMI-04
- [ ] `src/hmi_server/tests/test_app.py` -- covers HMI-01
- [ ] `src/hmi_server/tests/test_ws.py` -- covers HMI-02
- [ ] `src/hmi_server/tests/test_control.py` -- covers HMI-03 (control commands)
- [ ] `src/hmi_server/tests/test_alarm.py` -- covers HMI-03 (alarm commands)
- [ ] `src/hmi_server/tests/test_query.py` -- covers HMI-03 (logger queries)
- [ ] `src/hmi_server/tests/test_health.py` -- covers health endpoint
- [ ] pyproject.toml dependencies: add fastapi, uvicorn, bcrypt, httpx (test), pytest-asyncio (test)
- [ ] pytest config in pyproject.toml: `[tool.pytest.ini_options]` with asyncio_mode = "auto"

### Test Pattern Notes
- **FastAPI testing:** Use `httpx.AsyncClient` with `ASGITransport(app=app)` -- no real server needed.
- **WebSocket testing:** Use `httpx` or Starlette `TestClient` with `with client.websocket_connect("/ws")`.
- **ZMQ mocking:** Use tcp:// endpoints (same as alarm_manager/control_manager tests). Create mock REP/PUB sockets in conftest that return canned responses. 50ms connect sleep for TCP handshake (established pattern in test_loop.py).
- **Auth testing:** Create test hashes with `bcrypt.hashpw(b"1234", bcrypt.gensalt())` in conftest.

## Sources

### Primary (HIGH confidence)
- `src/common/python/src/ems_common/ipc.py` -- All ZMQ socket paths, topics, encode/decode helpers
- `src/common/python/src/ems_common/rtdb.py` -- RTDB struct definitions (telemetry data shapes)
- `config/hmi_config.yaml` -- HMI configuration structure
- `config/schemas/hmi_config.schema.json` -- Full config schema with validation rules
- `src/alarm_manager/src/ems_alarm_manager/loop.py` -- ZMQ REP command dispatch pattern, available alarm actions
- `src/control_manager/python/src/ems_control_manager/loop.py` -- ZMQ REP command dispatch pattern, available control actions
- `src/logger/python/src/ems_logger/query_handler.py` -- Logger query types and REQ/REP protocol
- `src/data_manager/python/src/ems_data_manager/publisher.py` -- Telemetry PUB frame format
- `src/logger/python/src/ems_logger/telemetry_writer.py` -- Telemetry SUB receive pattern

### Secondary (MEDIUM confidence)
- FastAPI lifespan documentation -- verified against project FastAPI version requirements
- Starlette StaticFiles and WebSocket -- standard Starlette/FastAPI patterns

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in workspace or well-established
- Architecture: HIGH -- all patterns derived from existing codebase modules
- Integration points: HIGH -- ZMQ socket paths, topics, and message formats read directly from source
- Pitfalls: HIGH -- derived from actual codebase patterns and frame formats

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable -- internal codebase patterns unlikely to change)
