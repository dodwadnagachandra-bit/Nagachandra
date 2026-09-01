---
phase: 18-hmi-backend
plan: 02
subsystem: hmi_server
tags: [fastapi, zmq-proxy, control, alarm, query, rest-api]
dependency_graph:
  requires: [hmi_server/deps.py, hmi_server/models.py, hmi_server/auth.py, ems-common/ipc.py]
  provides: [hmi_server/control.py, hmi_server/alarm.py, hmi_server/query.py]
  affects: [18-03-PLAN]
tech_stack:
  added: []
  patterns: [ZMQ REQ proxy via deps.zmq_command, monkeypatch-based test mocking, Literal path parameter validation]
key_files:
  created:
    - src/hmi_server/src/ems_hmi_server/control.py
    - src/hmi_server/src/ems_hmi_server/alarm.py
    - src/hmi_server/src/ems_hmi_server/query.py
    - src/hmi_server/tests/test_control.py
    - src/hmi_server/tests/test_alarm.py
    - src/hmi_server/tests/test_query.py
  modified:
    - src/hmi_server/src/ems_hmi_server/app.py
decisions:
  - "Routers call deps.zmq_command via module reference (not direct import) to enable monkeypatching in tests"
  - "Query endpoint uses Literal path parameter for type validation (422 on invalid) rather than Pydantic body model"
metrics:
  duration: 307s
  completed: "2026-03-15T07:18:42Z"
  tasks: 2
  tests: 27
---

# Phase 18 Plan 02: Command Proxy -- control, alarm, and query routers

Three FastAPI routers that proxy 10 REST endpoints to backend EMS modules (control_manager, alarm_manager, logger) via ZMQ REQ/REP using the existing zmq_command helper from deps.py.

## What Was Built

### Task 1: Control and alarm routers with ZMQ REQ proxy
- **control.py**: APIRouter with 5 endpoints:
  - POST `/api/control/mode` -- sends `mode_change` to control_manager
  - POST `/api/control/setpoint` -- sends `manual_setpoint`
  - POST `/api/control/priority` -- sends `source_priority`
  - POST `/api/control/fault-reset` -- sends `fault_reset`
  - POST `/api/control/maintenance` -- sends `maintenance_enter`/`maintenance_exit` (admin only)
- **alarm.py**: APIRouter with 3 endpoints:
  - POST `/api/alarm/acknowledge` -- sends `acknowledge` to alarm_manager
  - GET `/api/alarm/active` -- sends `get_active_alarms`
  - GET `/api/alarm/config` -- sends `get_alarm_config`
- **app.py**: Updated to include control and alarm routers
- **test_control.py**: 11 tests (5 commands, maintenance enter/exit, operator 403, auth, timeout 504, error 400)
- **test_alarm.py**: 5 tests (acknowledge, active, config, auth, timeout 504, error 400)

### Task 2: Query router for logger data queries
- **query.py**: APIRouter with 1 endpoint:
  - POST `/api/query/{query_type}` -- sends `query` to logger with type parameter
  - 6 valid types: time_series, latest, range_stats, event_log, energy_totals, cell_snapshot
  - Path parameter validated via `Literal` type (422 for invalid)
  - Additional query params forwarded from request body
- **app.py**: Updated to include query router
- **test_query.py**: 11 tests (6 query types parametrized, invalid type 422, param forwarding, auth, timeout 504, error 400)

## Test Results

```
47 passed in 43.20s
```

Full suite: 20 from Plan 01 + 27 from Plan 02 = 47 total.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Routers use module-level reference for zmq_command**
- **Found during:** Task 1
- **Issue:** Monkeypatching `deps.zmq_command` did not affect the already-imported function reference in control.py/alarm.py, causing tests to hit real ZMQ sockets and timeout
- **Fix:** Changed routers to `import ems_hmi_server.deps as deps` and call `deps.zmq_command()` instead of importing the function directly
- **Files modified:** control.py, alarm.py, query.py

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 0e4a6df | feat(18-02): control and alarm routers with ZMQ REQ proxy |
| 2 | c073fc4 | feat(18-02): query router for logger data queries via ZMQ |
