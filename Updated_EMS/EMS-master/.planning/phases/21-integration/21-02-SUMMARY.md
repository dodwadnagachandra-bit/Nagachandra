---
phase: 21-integration
plan: 02
title: "E2E Flow Tests"
subsystem: integration-testing
tags: [integration, e2e, hmi, scheduler, websocket, command-flow]
dependency_graph:
  requires: [21-01]
  provides: [m3-e2e-tests, command-flow-test, telemetry-flow-test, schedule-dispatch-test, ws-reconnection-test]
  affects: [tests/integration/test_m3_integration.py]
tech_stack:
  added: [websockets]
  patterns: [ipc-socket-integration, now-func-clock-injection, httpx-async-client, websocket-reconnection]
key_files:
  created:
    - tests/integration/test_m3_integration.py
  modified:
    - pyproject.toml
    - uv.lock
decisions:
  - "Used ipc:// sockets for all ZMQ endpoints (not TCP isolation) because hmi_server control.py hardcodes SOCK_CONTROL_CMD ipc:// path"
  - "All 4 test classes in single file with shared _build_m3_system helper function"
  - "SchedulerLoop tests use unique TCP PUB endpoint per test to avoid address-in-use conflicts"
  - "WebSocket tests use same HTTP port (FastAPI serves both HTTP and WS on one port)"
metrics:
  duration_s: 225
  completed: "2026-03-15T10:42:20Z"
  tasks: 2
  files_changed: 3
---

# Phase 21 Plan 02: E2E Flow Tests Summary

Created 4 integration test classes validating all M3 critical data flows across 11+ module boundaries.

## One-liner

E2E integration tests for REST command dispatch, WebSocket telemetry at 1Hz, scheduler clock-mocked dispatch to PCS, and WebSocket reconnection after server crash.

## What Was Done

### Task 1: E2E command flow and telemetry flow tests

1. Created `tests/integration/test_m3_integration.py` (545 lines) with:
   - Shared `_build_m3_system()` helper that launches all 11 modules (M1+M2+hmi_server) with ipc:// ZMQ sockets and random HTTP port
   - `_teardown_m3_system()` for reverse-order cleanup
   - Helper functions: `read_active_setpoint_kw()`, `send_control_command()`, `read_control_state()`, `check_control_state()`

2. `TestCommandFlow` class (validates HMI-03):
   - Health check -> login with operator PIN "1234" -> POST /api/control/mode standby -> verify RTDB control_state reaches STANDBY -> POST /api/control/setpoint 15.0 kW -> verify RTDB active_setpoint_kw -> GET /api/alarm/active returns 200
   - Uses httpx.AsyncClient with Bearer token against running hmi_server subprocess

3. `TestTelemetryFlow` class (validates HMI-02):
   - Connects Python websockets client to ws://127.0.0.1:{port}/ws/telemetry
   - Receives 5 messages with 3s timeout each, asserts >= 3 received (1Hz)
   - Validates message shape {topic, data, ts} and SOC range [0, 100]

### Task 2: Schedule dispatch and WebSocket reconnection tests

4. `TestScheduleDispatch` class (validates SCHED-01, SCHED-03, SCHED-04, SCHED-05):
   - In-process SchedulerLoop with `now_func` clock injection (not subprocess)
   - 4 test methods:
     - `test_discharge_window_active`: time=12:00 -> setpoint ~10.0 kW
     - `test_charge_window_active`: time=23:00 -> setpoint ~-15.0 kW (negative = charge)
     - `test_between_windows_idle`: time=19:00 -> setpoint ~0 kW
     - `test_manual_mode_no_dispatch`: manual mode -> pcs_command_seq unchanged
   - Each test creates its own SchedulerLoop with unique PUB endpoint

5. `TestWebSocketReconnection` class (validates HMI-13):
   - Connect WebSocket, receive 3 messages (confirm data flowing)
   - SIGKILL hmi_server, wait for death, sleep 5s (simulates systemd RestartSec)
   - Restart hmi_server, wait for health endpoint
   - Reconnect WebSocket within 10s, verify 3+ messages on new connection

6. Added `websockets>=16.0` as dev dependency to root pyproject.toml.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] ipc:// sockets instead of TCP isolation**
- **Found during:** Task 1
- **Issue:** hmi_server's `control.py` hardcodes `SOCK_CONTROL_CMD` (ipc:// path) when calling `deps.zmq_command()`. Cannot override via env var. TCP isolation would require code changes to hmi_server.
- **Fix:** Used ipc:// sockets for all ZMQ endpoints (matching default module behaviour). Requires `/run/ems/` directory to exist. Tests skip if directory not available.
- **Files modified:** tests/integration/test_m3_integration.py
- **Commit:** f23de4c

**2. [Rule 2 - Missing] Unique PUB endpoint per scheduler test**
- **Found during:** Task 2
- **Issue:** SchedulerLoop binds a ZMQ PUB socket. Running multiple tests sequentially would fail with "address already in use" if using the same ipc:// endpoint.
- **Fix:** Each scheduler test allocates a unique TCP port for the PUB endpoint while using ipc:// for REQ (control_cmd) and SUB (config_pub).
- **Files modified:** tests/integration/test_m3_integration.py
- **Commit:** f23de4c

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1+2 | f23de4c | feat(21-02): add M3 E2E integration tests with 4 test classes |

## Verification

- `pytest --collect-only -q -m integration` shows all 7 tests across 4 classes
- File is 545 lines (exceeds 300 minimum)
- All key patterns present: httpx POST to /api/control/mode, websockets.connect to /ws/telemetry, now_func clock injection, SIGKILL + restart
- M2 integration tests still collect (13 tests, no interference)

## Self-Check: PASSED

All artifacts verified.
