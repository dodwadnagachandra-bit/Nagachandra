# Phase 21: Integration and Hardening - Research

**Researched:** 2026-03-15
**Domain:** Full-stack integration testing (HMI + scheduler + M1+M2 modules)
**Confidence:** HIGH

## Summary

Phase 21 is a validation-only phase -- no new features, no new requirements. It proves that all M3 modules (hmi_server, scheduler) work correctly with all M1+M2 modules in an end-to-end system. The codebase already has a mature integration test infrastructure from Phase 13/17 (`tests/integration/conftest.py`) with `ModuleProcess` subprocess management, `MetricsCollector`, RTDB health checks, and ZMQ helpers. Phase 21 tests should follow the TCP port isolation pattern established in `test_m2_integration.py`.

The key challenge is launching 11+ modules (M1 core + M2 control/alarm + hmi_server + scheduler + simulators) in correct dependency order and verifying three cross-cutting data flows: (1) HTTP command -> ZMQ -> RTDB -> PCS, (2) simulator -> RTDB -> ZMQ -> WebSocket -> JSON, and (3) scheduler clock-mocked -> ZMQ -> control_manager -> PCS. The SchedulerLoop already accepts a `now_func` parameter for clock injection, and hmi_server's `create_app()` accepts `telemetry_socket` override for test isolation.

**Primary recommendation:** Build on existing `test_m2_integration.py` fixture pattern -- allocate TCP ports for ZMQ isolation, launch modules via `ModuleProcess`, add hmi_server (via `create_app()` + httpx) and scheduler (via subprocess with env var overrides) to the system fixture. Use httpx AsyncClient for REST API testing and Python `websockets` library for WebSocket testing.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

**E2E Command Flow Test Methodology:**
- Automated test using httpx (async HTTP client) against a running FastAPI server with all backend modules active
- Steps: start all modules -> login with operator PIN -> POST /api/control/mode -> verify PCS state in RTDB -> POST /api/control/setpoint -> verify PCS register 0x500E -> GET /api/alarm/active
- Tests use httpx.AsyncClient with base_url pointing to running hmi_server
- Auth token obtained at test start, reused for all requests
- PCS verification reads RTDB directly (not via HMI)
- Each step has explicit timeout

**E2E Telemetry Flow Test:**
- WebSocket client test connects to hmi_server /ws endpoint, receives messages, validates content against simulator values
- WebSocket test uses Python `websockets` library (not browser)
- Messages are JSON -- parse with json.loads
- Test validates message shape (required fields present) and value ranges (not exact values)
- 3+ messages in 5 seconds confirms 1Hz streaming rate

**Schedule-to-Dispatch Flow Test:**
- Mock system clock in scheduler to simulate time window transitions, then verify PCS register
- 6 scenarios: discharge window, charge window, between windows, curve mode, manual mode, day/night transition
- Clock mocking via environment variable or monkeypatch
- Each scenario is a separate test case
- Verify both ZMQ command sent by scheduler AND resulting PCS register value

**WebSocket Reconnection Test:**
- Kill hmi_server process, verify WebSocket clients can reconnect within 30 seconds after restart
- Test at Python WebSocket client level (not browser)
- 5-second gap between kill and restart simulates systemd RestartSec=5

**Crash Recovery Additions:**
- Add hmi_server and scheduler to existing CRASH_MATRIX pattern
- hmi_server crash loses all auth tokens (in-memory) -- clients must re-login
- Scheduler crash: on restart, immediately evaluates current time and sends appropriate command

### Claude's Discretion

- Test infrastructure reuse from Phase 13/17 (conftest.py, ModuleProcess)
- httpx vs requests for REST API testing
- WebSocket test library (websockets vs aiohttp)
- Clock mocking approach for scheduler tests
- Makefile target naming (test-integration-m3)
- Whether to include HMI visual regression tests (likely skip for M3)

### Deferred Ideas (OUT OF SCOPE)

- Browser-based E2E tests (Playwright/Cypress) -- deferred, Python tests sufficient for M3
- Visual regression testing -- deferred to M5 production hardening
- Load testing (multiple WebSocket clients) -- embedded kiosk is single-user
- HMI accessibility testing (screen reader, contrast) -- future requirement
- Performance profiling on ARM display -- deferred to ECU hardware testing (PLAT-01)

</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | >=0.28 | Async HTTP client for testing FastAPI | Already in hmi_server dev deps; FastAPI official recommendation |
| websockets | >=14.0 | WebSocket client for telemetry flow testing | Standard Python WebSocket library; clean async API |
| pytest | >=8.0 | Test framework | Already in workspace dev deps |
| pytest-asyncio | >=0.24 | Async test support | Already in hmi_server dev deps |
| pytest-timeout | >=2.4.0 | Test timeout enforcement | Already in workspace dev deps |
| psutil | >=7.2.2 | Process monitoring (RSS, alive check) | Already in workspace dev deps; used by conftest.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pyzmq | >=27.1.0 | ZMQ command/verify | Already in workspace; used for direct command verification |
| msgpack | >=1.0 | Message encoding/decoding | Already in workspace; used by ipc.py |
| pyyaml | >=6.0 | Config file manipulation | Already in workspace; needed for test-specific configs |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| websockets | aiohttp.ClientSession.ws_connect | aiohttp is heavier; websockets is purpose-built |
| httpx | requests | requests is sync-only; httpx supports async and FastAPI test client |

**Installation:**
```bash
uv add --dev websockets
```

Note: httpx is already a dev dependency of hmi_server. websockets needs to be added to the root workspace dev deps or the test dependencies.

## Architecture Patterns

### Recommended Test Structure
```
tests/integration/
    conftest.py              # Existing -- ModuleProcess, helpers
    test_startup.py          # Existing -- M1 startup tests
    test_crash_recovery.py   # Existing -- extend with hmi_server + scheduler
    test_m2_integration.py   # Existing -- M2 protection/dispatch flow
    test_m3_integration.py   # NEW -- E2E command, telemetry, schedule flows
```

### Pattern 1: Full System Fixture with TCP Port Isolation (from test_m2_integration.py)

**What:** Launch all modules as subprocesses with randomized TCP ports for ZMQ endpoints, avoiding conflicts with system services and other test classes.

**When to use:** Every integration test class that needs a running system.

**Example:**
```python
# Source: tests/integration/test_m2_integration.py lines 284-502
@pytest.fixture(scope="class")
def m3_system(self) -> Any:
    # Allocate TCP ports for ZMQ isolation
    ports: list[int] = _allocate_tcp_ports(12)  # need more for scheduler + hmi
    control_cmd_port, alarm_cmd_port, telemetry_port = ports[0], ports[1], ports[2]
    scheduler_pub_port, hmi_http_port = ports[3], ports[4]
    # ... (same pattern as m2_system)

    # hmi_server: use create_app() directly with httpx for in-process testing
    # OR launch as subprocess for WebSocket/reconnection tests
```

### Pattern 2: httpx AsyncClient for REST API Testing

**What:** Use httpx.AsyncClient with ASGITransport to test FastAPI endpoints in-process (no subprocess needed for REST-only tests).

**When to use:** E2E command flow tests where hmi_server REST API is the entry point.

**Example:**
```python
from httpx import ASGITransport, AsyncClient
from ems_hmi_server.app import create_app

app = create_app(config, telemetry_socket=f"tcp://127.0.0.1:{telemetry_port}")
async with AsyncClient(
    transport=ASGITransport(app=app),
    base_url="http://test"
) as client:
    resp = await client.post("/api/auth/login", json={"pin": "1234"})
    token = resp.json()["token"]
    resp = await client.post(
        "/api/control/mode",
        json={"target_state": "standby"},
        headers={"Authorization": f"Bearer {token}"},
    )
```

**Important caveat:** httpx ASGITransport does NOT start the lifespan events by default. For full lifespan (ZMQ bridge, token cleanup), pass `raise_app_exceptions=False` or use the subprocess approach for WebSocket tests.

### Pattern 3: Subprocess hmi_server for WebSocket/Reconnection Tests

**What:** Launch hmi_server as a real subprocess via `ModuleProcess` for tests that need WebSocket connections or process kill/restart.

**When to use:** WebSocket telemetry flow test, WebSocket reconnection test.

**Example:**
```python
hmi = ModuleProcess(
    name="hmi_server",
    cmd=[
        "uv", "run", "python", "-m", "ems_hmi_server",
        "--config", str(test_hmi_config_path),
    ],
    ready_check=lambda: _check_http_health(hmi_port),
    env={
        "EMS_TELEMETRY_ENDPOINT": f"tcp://127.0.0.1:{telemetry_port}",
    },
)
```

### Pattern 4: SchedulerLoop now_func for Clock Mocking

**What:** SchedulerLoop accepts a `now_func: Callable[[], datetime]` parameter. For subprocess-based tests, the scheduler reads env var overrides for ZMQ endpoints.

**When to use:** Schedule-to-dispatch flow tests.

**Two approaches:**
1. **In-process (preferred for clock mocking):** Instantiate `SchedulerLoop` directly with custom `now_func`, run a few ticks manually.
2. **Subprocess with env overrides:** Set `EMS_CONTROL_CMD_ENDPOINT`, `EMS_CONFIG_SUB_ENDPOINT`, `EMS_SCHEDULER_PUB_ENDPOINT` env vars (already supported by `__main__.py`).

```python
from datetime import datetime
from ems_scheduler.loop import SchedulerLoop
from ems_scheduler.config import load_schedule_config

config = load_schedule_config(schedule_config_path)
mock_time = datetime(2026, 3, 15, 12, 0, 0)  # Noon -- inside discharge window

loop = SchedulerLoop(
    config,
    config_path=schedule_config_path,
    req_endpoint=f"tcp://127.0.0.1:{control_cmd_port}",
    config_sub_endpoint=f"tcp://127.0.0.1:{config_sub_port}",
    pub_endpoint=f"tcp://127.0.0.1:{scheduler_pub_port}",
    now_func=lambda: mock_time,
)
```

### Pattern 5: CRASH_MATRIX Extension

**What:** Add hmi_server and scheduler to the existing CRASH_MATRIX parametrized tests.

**When to use:** Crash recovery validation for M3 modules.

```python
CRASH_MATRIX_M3: list[tuple[str, int]] = [
    ("hmi_server", signal.SIGKILL),
    ("hmi_server", signal.SIGTERM),
    ("scheduler", signal.SIGKILL),
    ("scheduler", signal.SIGTERM),
]
```

### Anti-Patterns to Avoid

- **Using ipc:// sockets in tests:** Always use `tcp://127.0.0.1:{random_port}` for ZMQ isolation. The M2 tests established this pattern explicitly (see test_m2_integration.py line 22: "No ipc://run/ems/ paths anywhere in this file").
- **Testing exact telemetry values:** WebSocket telemetry values come from simulators with noise. Test ranges and field presence, not exact numbers.
- **Long-running clock-wait tests:** Never wait for real time windows. Use `now_func` injection or mock the clock.
- **Shared test state between classes:** Each test class should have its own class-scoped system fixture with independent port allocations.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Process management | Custom subprocess wrapper | `ModuleProcess` from conftest.py | Already handles ready_check, signal, RSS, cleanup |
| Multi-criteria polling | Custom while loops | `wait_for_criteria()` from conftest.py | Already handles timeout, multiple checks, poll intervals |
| RTDB health checks | Manual shm attach/detach | `check_rtdb_exists()`, `check_rtdb_fresh()` from conftest.py | Already handles errors, stale data detection |
| ZMQ command send | Manual socket create/send/recv | `send_control_command()` from test_m2_integration.py | Already handles linger, timeout, cleanup |
| TCP port allocation | Hardcoded ports | `_allocate_tcp_ports()` from test_m2_integration.py | Avoids port conflicts between test classes |
| HTTP health check | curl/requests oneshot | Small `_check_http_health(port)` helper with httpx | Need for ready_check callback |

**Key insight:** The existing test infrastructure is comprehensive. Phase 21 adds only the M3-specific layer (HTTP client, WebSocket client, scheduler clock mocking) on top of the existing M1+M2 system fixture.

## Common Pitfalls

### Pitfall 1: hmi_server ZMQ Socket Path Not Overridable
**What goes wrong:** hmi_server's `deps.py` `zmq_command()` hardcodes `SOCK_CONTROL_CMD` as `ipc:///run/ems/control_cmd.sock`. In integration tests with TCP isolation, the hmi_server subprocess would connect to the wrong endpoint.
**Why it happens:** The `create_app()` function accepts `telemetry_socket` override but `zmq_command()` in deps.py gets the socket path from the route handler (e.g., `control.py` passes `SOCK_CONTROL_CMD`).
**How to avoid:** Either (a) add env var overrides to hmi_server for ZMQ control/alarm sockets (like scheduler already has), or (b) run hmi_server against ipc:// sockets that the control_manager also binds. Option (a) is cleaner; option (b) requires /run/ems/ directory.
**Warning signs:** HTTP 504 "Backend service timeout" from hmi_server REST API in integration tests.

### Pitfall 2: httpx ASGITransport Lifespan
**What goes wrong:** httpx's `ASGITransport` does not trigger FastAPI lifespan by default. The ZMQ context, telemetry bridge, and token cleanup tasks are not started.
**Why it happens:** ASGITransport is designed for unit testing; lifespan is separate.
**How to avoid:** For tests that need the telemetry bridge (WebSocket tests), run hmi_server as a subprocess. For REST-only tests, the lifespan issue is manageable because `zmq_command()` in deps.py creates per-call ZMQ contexts. The `token_store` is set on `app.state` during `create_app()` so auth works without lifespan.
**Warning signs:** WebSocket endpoint accepts connection but never sends messages.

### Pitfall 3: Auth PIN Hash in Test Config
**What goes wrong:** Tests need to POST `/api/auth/login` with a PIN, but the default hmi_config.yaml has `$2b$12$placeholder` which is not a valid bcrypt hash of any known PIN.
**Why it happens:** Production config uses real hashed PINs; test config needs known PINs.
**How to avoid:** Create a test-specific `hmi_config.yaml` with known bcrypt hashes. Generate with: `python3 -c "import bcrypt; print(bcrypt.hashpw(b'1234', bcrypt.gensalt()).decode())"`.
**Warning signs:** 401 "Invalid PIN" from login endpoint.

### Pitfall 4: WebSocket Endpoint Path
**What goes wrong:** Tests connect to `/ws` but the actual WebSocket endpoint is `/ws/telemetry`.
**Why it happens:** CONTEXT.md mentions `/ws` but the code in `ws.py` line 123 uses `@router.websocket("/ws/telemetry")`.
**How to avoid:** Use `/ws/telemetry` as the WebSocket path in tests.
**Warning signs:** 403 or connection refused on WebSocket connect.

### Pitfall 5: Module Start Order for hmi_server and scheduler
**What goes wrong:** hmi_server and scheduler crash on startup because their ZMQ dependencies (control_manager, data_manager) are not ready.
**Why it happens:** Missing dependency ordering in test fixture.
**How to avoid:** Follow systemd ordering from service files:
- hmi_server: After data_manager (needs telemetry SUB)
- scheduler: After control_manager and config_manager (needs REQ to control_cmd)
**Warning signs:** ZMQ connection refused, empty telemetry.

### Pitfall 6: PCS Command Verification via RTDB
**What goes wrong:** Test reads `rtdb.pcs.active_power` expecting the setpoint, but the setpoint is written to `rtdb.system.active_setpoint_kw` and the PCS command goes via `rtdb.system.pcs_command` / `rtdb.system.pcs_command_seq`.
**Why it happens:** Confusion between PCS telemetry (read from PCS) and PCS commands (written by control_manager).
**How to avoid:** To verify the command was sent:
- Read `rtdb.system.active_setpoint_kw` for the setpoint value
- Read `rtdb.system.pcs_command` for ON/OFF/FAULT_RESET
- Read `rtdb.system.pcs_command_seq` for monotonic command counter
- The actual 0x500E register verification requires checking the Modbus simulator state (if it stores last written register)

## Code Examples

### Example 1: HTTP Health Check for ModuleProcess ready_check

```python
# Use as ready_check callback for ModuleProcess
import httpx

def _check_http_health(port: int) -> bool:
    """Return True if hmi_server health endpoint responds 200."""
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/api/health/", timeout=1.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.ReadTimeout):
        return False
```

### Example 2: Auth Token Acquisition

```python
# Source: src/hmi_server/src/ems_hmi_server/auth.py
async def get_auth_token(client: httpx.AsyncClient, pin: str = "1234") -> str:
    """Login and return bearer token."""
    resp = await client.post("/api/auth/login", json={"pin": pin})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["token"]
```

### Example 3: WebSocket Telemetry Receive

```python
import json
import websockets

async def receive_telemetry(uri: str, count: int = 3, timeout: float = 7.0) -> list[dict]:
    """Connect to WebSocket, receive `count` messages within `timeout`."""
    messages: list[dict] = []
    async with websockets.connect(uri) as ws:
        for _ in range(count):
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            messages.append(json.loads(raw))
    return messages
```

### Example 4: RTDB Setpoint Verification

```python
# Source: src/common/python/src/ems_common/rtdb.py
from ems_common.rtdb import attach_rtdb, detach_rtdb

def read_active_setpoint_kw() -> float:
    """Read the current active setpoint from RTDB system section."""
    shm, rtdb = attach_rtdb()
    try:
        return float(rtdb.system.active_setpoint_kw)
    finally:
        del rtdb
        detach_rtdb(shm)

def read_pcs_command() -> int:
    """Read PCS command byte: 0=NONE, 1=ON, 2=OFF, 3=FAULT_RESET."""
    shm, rtdb = attach_rtdb()
    try:
        return int(rtdb.system.pcs_command)
    finally:
        del rtdb
        detach_rtdb(shm)

def read_control_state() -> int:
    """Read control state machine state."""
    shm, rtdb = attach_rtdb()
    try:
        return int(rtdb.system.control_state)
    finally:
        del rtdb
        detach_rtdb(shm)
```

### Example 5: Scheduler now_func Clock Mocking

```python
from datetime import datetime
from ems_scheduler.loop import SchedulerLoop

# Create loop with fixed mock time
mock_time = datetime(2026, 3, 15, 12, 0, 0)
loop = SchedulerLoop(
    config=schedule_config,
    config_path=schedule_config_path,
    req_endpoint=control_cmd_ep,
    config_sub_endpoint=config_sub_ep,
    pub_endpoint=scheduler_pub_ep,
    now_func=lambda: mock_time,
)

# Run one tick manually (evaluate + send command)
loop._evaluate_tick(mock_time)
```

## Key Integration Paths

### Path 1: E2E Command Flow
```
HMI Button Click
  -> POST /api/control/mode {target_state: "standby"}
  -> auth.require_auth() validates Bearer token
  -> control.change_mode() calls deps.zmq_command(SOCK_CONTROL_CMD, ...)
  -> ZMQ REQ -> control_manager REP socket
  -> control_manager state machine transitions IDLE -> STANDBY
  -> Writes rtdb.system.control_state = 2 (STANDBY)
  -> Writes rtdb.system.pcs_command = 1 (ON)
  -> comm_manager_python reads pcs_command_seq change
  -> Writes Modbus register 0x0291 = 1 (PCS ON)
  -> POST /api/control/setpoint {power_kw: 15.0}
  -> control_manager sets rtdb.system.active_setpoint_kw = 15.0
  -> comm_manager_python writes register 0x500E = 150
```

### Path 2: E2E Telemetry Flow
```
CAN/Modbus Simulator generates data
  -> comm_manager_c/python writes to RTDB (cell voltages, PCS state, etc.)
  -> data_manager_python reads RTDB at 1Hz
  -> Publishes ZMQ PUB multipart [topic_bytes, msgpack_envelope]
  -> hmi_server telemetry_bridge() SUB socket receives
  -> Decodes msgpack -> JSON dict {topic, data, ts}
  -> ClientManager.broadcast() pushes to all client queues
  -> websocket_endpoint() sends JSON to browser
```

### Path 3: Schedule-to-Dispatch Flow
```
Scheduler 1Hz tick:
  -> SchedulerLoop._evaluate_tick(now)
  -> evaluate_time_of_day(now, config["time_windows"])
  -> Returns WindowResult(action="discharge", power_kw=10.0)
  -> If state changed from last tick: _send_command("manual_setpoint", {power_kw: 10.0})
  -> ZMQ REQ -> control_manager REP
  -> control_manager applies setpoint -> RTDB
  -> comm_manager -> PCS register 0x500E = 100
```

## Module Start Order (Full M3 System)

Based on systemd `After=` dependencies:

| Order | Module | After | Launch |
|-------|--------|-------|--------|
| 1 | data_manager_c | (none) | C binary with topology args |
| 2 | data_manager_python | data_manager | `uv run python -m ems_data_manager` |
| 3 | config_manager | data_manager | `uv run python -m ems_config_manager` |
| 4 | safety_manager | data_manager | C binary, env: EMS_GPIO_BACKEND=rtdb |
| 5 | comm_manager_c | safety, data | C binary (requires vcan0) |
| 6 | comm_manager_python | safety, data | `uv run python -m ems_comm_manager` |
| 7 | logger | data, comm | `uv run python -m ems_logger` |
| 8 | control_manager | logger, data | `uv run python -m ems_control_manager` |
| 9 | alarm_manager | control, data | `uv run python -m ems_alarm_manager` |
| 10 | hmi_server | data (needs telemetry) | `uv run python -m ems_hmi_server` |
| 11 | scheduler | control, config | `uv run python -m ems_scheduler` |
| -- | CAN simulator | (parallel with modules) | Subprocess |
| -- | Modbus simulator | (parallel with modules) | Subprocess |

## Dependencies Needed

| Dependency | Status | Action |
|------------|--------|--------|
| httpx | Already in hmi_server dev deps | Ensure available at workspace level: `uv add --dev httpx` |
| websockets | NOT in any pyproject.toml | Add: `uv add --dev websockets` |
| pytest-asyncio | Already in workspace and hmi_server dev deps | No action |
| bcrypt | Already in hmi_server deps | Need for generating test PIN hashes |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >= 8.0 + pytest-asyncio >= 0.24 + pytest-timeout >= 2.4.0 |
| Config file | `pyproject.toml` (root, `[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/integration/test_m3_integration.py -v -m integration --timeout=300 -x` |
| Full suite command | `uv run pytest tests/integration/ -v -m integration --timeout=900` |

### Phase Requirements -> Test Map

Phase 21 validates all Phase 18-20 requirements in integration (no new requirement IDs). Mapping to test scenarios:

| Scenario | Validates | Test Type | Automated Command | File Exists? |
|----------|-----------|-----------|-------------------|-------------|
| Full systemd startup | HMI-01, SCHED-01 | integration | `uv run pytest tests/integration/test_m3_integration.py::TestFullSystemStartup -x` | No -- Wave 0 |
| E2E command flow | HMI-03 | integration | `uv run pytest tests/integration/test_m3_integration.py::TestCommandFlow -x` | No -- Wave 0 |
| E2E telemetry flow | HMI-02 | integration | `uv run pytest tests/integration/test_m3_integration.py::TestTelemetryFlow -x` | No -- Wave 0 |
| Schedule-to-dispatch | SCHED-01, SCHED-03, SCHED-04, SCHED-05 | integration | `uv run pytest tests/integration/test_m3_integration.py::TestScheduleDispatch -x` | No -- Wave 0 |
| WebSocket reconnection | HMI-13 | integration | `uv run pytest tests/integration/test_m3_integration.py::TestWebSocketReconnection -x` | No -- Wave 0 |
| Crash recovery (hmi+sched) | (robustness) | integration | `uv run pytest tests/integration/test_crash_recovery.py -x` (extended) | Partially -- extend existing |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/integration/test_m3_integration.py -v -m integration --timeout=300 -x`
- **Per wave merge:** `uv run pytest tests/integration/ -v -m integration --timeout=900`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/integration/test_m3_integration.py` -- all M3 E2E test classes
- [ ] `config/profiles/residential/hmi_config_test.yaml` -- test config with known bcrypt PIN hashes
- [ ] `websockets` dev dependency -- `uv add --dev websockets`
- [ ] `httpx` in workspace dev deps -- `uv add --dev httpx`
- [ ] `Makefile` target `test-integration-m3`

## Patterns to Follow

### From test_m2_integration.py (Phase 17)
1. **TCP port isolation:** `_allocate_tcp_ports(count)` for all ZMQ endpoints
2. **Temp config directory:** Copy profile configs to tmpdir, modify for test scenarios
3. **Class-scoped fixtures:** One system fixture per test class, shared across methods
4. **Sequential assertions with explicit identification:** "Step 1: ...", "Step 2: ..."
5. **Module cleanup in reverse order:** Simulators first, then modules in reverse startup order

### From test_crash_recovery.py (Phase 13/17)
1. **CRASH_MATRIX parametrize:** `(module_name, signal_num)` tuples with readable IDs
2. **Recovery criteria polling:** `wait_for_criteria()` with named checks and timeout
3. **RTDB integrity verification:** Magic + version check after every crash
4. **PID change assertion:** Verify new PID differs from pre-crash PID

### From conftest.py
1. **ModuleProcess:** Use for all subprocess management (not raw subprocess.Popen)
2. **cleanup_shm fixture:** Autouse, removes /dev/shm/ems_rtdb before/after tests
3. **cleanup_ipc_sockets fixture:** Autouse, removes /run/ems/*.sock after tests
4. **Profile selection:** `--profile` option for topology selection

## Sources

### Primary (HIGH confidence)
- `tests/integration/conftest.py` -- ModuleProcess, helpers, fixtures (read directly)
- `tests/integration/test_m2_integration.py` -- TCP port isolation pattern, system fixture, command verification (read directly)
- `tests/integration/test_crash_recovery.py` -- CRASH_MATRIX pattern, recovery verification (read directly)
- `src/hmi_server/src/ems_hmi_server/app.py` -- create_app(), telemetry_socket override (read directly)
- `src/hmi_server/src/ems_hmi_server/ws.py` -- WebSocket endpoint path `/ws/telemetry`, ClientManager (read directly)
- `src/hmi_server/src/ems_hmi_server/control.py` -- REST API routes, ZMQ proxy (read directly)
- `src/hmi_server/src/ems_hmi_server/auth.py` -- TokenStore, login flow (read directly)
- `src/hmi_server/src/ems_hmi_server/deps.py` -- zmq_command(), require_auth() (read directly)
- `src/scheduler/src/ems_scheduler/loop.py` -- SchedulerLoop, now_func, env var overrides (read directly)
- `src/scheduler/src/ems_scheduler/__main__.py` -- env var endpoint overrides (read directly)
- `src/common/python/src/ems_common/rtdb.py` -- EmsSystem struct, pcs_command fields (read directly)
- `src/common/python/src/ems_common/ipc.py` -- socket paths, topics, encode/decode helpers (read directly)
- `deploy/systemd/hmi_server.service` -- After=data_manager, RestartSec=5 (read directly)
- `deploy/systemd/scheduler.service` -- After=control_manager+config_manager, RestartSec=5 (read directly)

### Secondary (MEDIUM confidence)
- httpx ASGITransport behavior -- based on httpx documentation and FastAPI testing patterns

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in workspace or well-established
- Architecture: HIGH -- directly extends proven M2 integration test patterns
- Pitfalls: HIGH -- identified from reading actual source code (socket paths, auth hashes, endpoint routing)
- Test verification points: HIGH -- RTDB struct fields and WebSocket message format verified from source

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable -- testing infrastructure, not fast-moving libraries)
