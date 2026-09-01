---
phase: 18-hmi-backend
plan: 01
subsystem: hmi_server
tags: [fastapi, auth, health, pydantic, bcrypt]
dependency_graph:
  requires: [ems-common/ipc.py, config/hmi_config.yaml]
  provides: [hmi_server/app.py, hmi_server/auth.py, hmi_server/deps.py, hmi_server/health.py]
  affects: [18-02-PLAN, 18-03-PLAN]
tech_stack:
  added: [fastapi, uvicorn, bcrypt, pyzmq, msgpack, pyyaml, httpx, pytest-asyncio]
  patterns: [FastAPI app factory, lifespan context, bearer token auth, TDD]
key_files:
  created:
    - src/hmi_server/src/ems_hmi_server/app.py
    - src/hmi_server/src/ems_hmi_server/auth.py
    - src/hmi_server/src/ems_hmi_server/config.py
    - src/hmi_server/src/ems_hmi_server/deps.py
    - src/hmi_server/src/ems_hmi_server/health.py
    - src/hmi_server/src/ems_hmi_server/models.py
    - src/hmi_server/tests/__init__.py
    - src/hmi_server/tests/conftest.py
    - src/hmi_server/tests/test_auth.py
    - src/hmi_server/tests/test_health.py
    - src/hmi_server/tests/test_app.py
  modified:
    - src/hmi_server/pyproject.toml
    - uv.lock
decisions:
  - "Set app.state defaults in create_app for test compatibility (lifespan overrides in production)"
  - "Added pyzmq and msgpack as direct dependencies (needed by deps.py zmq_command)"
metrics:
  duration: 316s
  completed: "2026-03-15T07:10:59Z"
  tasks: 2
  tests: 20
---

# Phase 18 Plan 01: Foundation -- deps, config, models, app factory, health, PIN auth

FastAPI app factory with lifespan, YAML config loader, 12 Pydantic models, health endpoint, and full PIN authentication with bcrypt validation and in-memory token store (max 10 sessions, lazy + periodic expiry cleanup).

## What Was Built

### Task 1: Project setup, config loader, models, app factory, health endpoint
- **pyproject.toml**: Added fastapi, uvicorn, bcrypt, pyzmq, msgpack, pyyaml as runtime deps; httpx, pytest, pytest-asyncio as dev deps; asyncio_mode=auto
- **config.py**: YAML config loader with `load_hmi_config(path)` function
- **models.py**: 12 Pydantic v2 models covering health, auth, control, alarm, query, and ZMQ response schemas
- **health.py**: GET /api/health router returning `{status: "ok", uptime_s: int}`
- **deps.py**: Shared dependencies -- `require_auth` (401 for missing/invalid, 403 for expired), `require_admin`, `get_config`, `zmq_command` (REQ/REP with 5s timeout via run_in_executor)
- **app.py**: FastAPI factory with lifespan context, CORS for Vite dev (localhost:5173), conditional StaticFiles mount at "/"
- **Test suite**: conftest with bcrypt fixtures, 3 health tests, 4 app/CORS tests

### Task 2: PIN authentication module
- **auth.py**: TokenStore class with create/validate/remove/cleanup; login endpoint (operator PIN first, admin wins if both match); logout endpoint with require_auth dependency
- **app.py update**: TokenStore initialization, auth router inclusion, periodic cleanup background task (every 60s)
- **test_auth.py**: 13 tests covering login (operator/admin/wrong PIN), logout (valid/invalid), empty PIN rejection, token format (64-char hex), expired token (403), max 10 sessions (429), cleanup behavior

## Test Results

```
20 passed in 23.30s
```

All 20 tests pass: 3 health, 4 app, 13 auth.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added pyzmq and msgpack as direct dependencies**
- **Found during:** Task 1
- **Issue:** deps.py imports zmq and app.py imports zmq, but pyzmq was only a workspace dev dependency (not available at import time for hmi_server)
- **Fix:** Added pyzmq>=27.1.0 and msgpack>=1.0 to pyproject.toml runtime dependencies
- **Files modified:** src/hmi_server/pyproject.toml

**2. [Rule 1 - Bug] Set app.state defaults in create_app for test compatibility**
- **Found during:** Task 1
- **Issue:** httpx ASGITransport does not trigger lifespan, so start_time was not set when health endpoint tried to access it
- **Fix:** Set app.state.start_time and app.state.zmq_ctx defaults in create_app (lifespan overrides in production)
- **Files modified:** src/hmi_server/src/ems_hmi_server/app.py

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | e0497e4 | feat(18-01): project setup, config loader, models, app factory, health endpoint |
| 2 | 34ac198 | feat(18-01): PIN authentication module with token store and login/logout |
