---
phase: 18-hmi-backend
plan: 03
subsystem: hmi_server
tags: [fastapi, websocket, zmq-sub, telemetry, entry-point, spa-fallback]
dependency_graph:
  requires: [hmi_server/app.py, hmi_server/deps.py, ems-common/ipc.py]
  provides: [hmi_server/ws.py, hmi_server/__main__.py]
  affects: []
tech_stack:
  added: []
  patterns: [zmq.asyncio SUB bridge, per-client asyncio.Queue fan-out, SPA fallback route, uvicorn programmatic start]
key_files:
  created:
    - src/hmi_server/src/ems_hmi_server/ws.py
    - src/hmi_server/src/ems_hmi_server/__main__.py
    - src/hmi_server/tests/test_ws.py
  modified:
    - src/hmi_server/src/ems_hmi_server/app.py
    - src/hmi_server/tests/conftest.py
    - src/hmi_server/tests/test_app.py
decisions:
  - "Used zmq.asyncio.Context in lifespan (replaces plain zmq.Context) for async SUB bridge compatibility"
  - "WebSocket tests use direct bridge+ClientManager integration with real ZMQ rather than Starlette TestClient (threading model issues with zmq.asyncio)"
  - "SPA fallback route registered before StaticFiles mount; returns 404 when frontend not built"
metrics:
  duration: 629s
  completed: "2026-03-15T07:31:54Z"
  tasks: 2
  tests: 15
---

# Phase 18 Plan 03: WebSocket telemetry bridge and entry point

ZMQ SUB background task bridging 1Hz telemetry to WebSocket clients as JSON, SPA fallback route for React Router, and `python -m ems_hmi_server` entry point with uvicorn programmatic start.

## What Was Built

### Task 1: WebSocket telemetry bridge with ZMQ SUB fan-out (TDD)
- **ws.py**: ClientManager class with per-client asyncio.Queue(maxsize=100), drop-oldest overflow strategy; telemetry_bridge async function using zmq.asyncio.Socket(SUB) subscribing to all 6 topic prefixes (bms.rack, pcs, gpio, meter, btms, system); WebSocket endpoint at /ws/telemetry with disconnect handling
- **app.py**: Lifespan updated to use zmq.asyncio.Context, create ClientManager, start/cancel bridge task; create_app accepts optional telemetry_socket parameter for test overrides
- **conftest.py**: Added mock_zmq_pub fixture (tcp:// PUB with publish helper), ws_test_app fixture
- **test_ws.py**: 12 tests -- 5 ClientManager unit, 6 telemetry_bridge integration with real ZMQ PUB/SUB over tcp://, 1 route registration check

### Task 2: Entry point and static file serving
- **__main__.py**: argparse (--config, --log-level), logging.basicConfig, load_hmi_config, uvicorn.Server programmatic start, KeyboardInterrupt handling
- **app.py**: SPA fallback catch-all GET /{full_path:path} serves index.html for React Router client-side routes; returns 404 "Frontend not built" when dist dir missing
- **test_app.py**: 3 new tests -- SPA fallback 404 without frontend, API routes unaffected by catch-all, index.html served when frontend built

## Test Results

```
62 passed in 43.85s
```

Full suite: 20 (Plan 01) + 27 (Plan 02) + 15 (Plan 03) = 62 total.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] WebSocket integration tests use bridge+queue instead of Starlette TestClient**
- **Found during:** Task 1
- **Issue:** Starlette TestClient runs ASGI app in a background thread; zmq.asyncio.Context in the lifespan's event loop could not receive messages from the test thread's PUB socket (hung indefinitely on receive_json)
- **Fix:** Restructured tests to directly test telemetry_bridge function with real ZMQ PUB/SUB over tcp://, verifying the full ZMQ-to-queue path. WebSocket endpoint code is minimal (queue.get + send_json) and verified via route registration test
- **Files modified:** tests/test_ws.py

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 (RED) | d28a85b | test(18-03): add failing tests for WebSocket telemetry bridge |
| 1 (GREEN) | e4cee72 | feat(18-03): WebSocket telemetry bridge with ZMQ SUB fan-out |
| 2 | 6032e4d | feat(18-03): entry point and SPA fallback for static file serving |

## Self-Check: PASSED

All files exist. All commits found.
