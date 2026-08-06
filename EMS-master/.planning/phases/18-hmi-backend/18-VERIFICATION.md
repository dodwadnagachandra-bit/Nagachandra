---
phase: 18-hmi-backend
verified: 2026-03-15T08:00:00Z
status: passed
score: 5/5 must-haves verified
re_verification: false
---

# Phase 18: HMI Backend Verification Report

**Phase Goal:** FastAPI backend serves React build, streams 1Hz telemetry via WebSocket, proxies commands to control/alarm managers via ZMQ, and authenticates users via PIN
**Verified:** 2026-03-15T08:00:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | FastAPI serves React build as static files on configurable HTTP port; health endpoint returns 200 | VERIFIED | `app.py` mounts StaticFiles at "/" from `frontend/dist`, SPA fallback serves `index.html` for client-side routing; `health.py` returns `{status: "ok", uptime_s: int}` at GET `/api/health/`; `__main__.py` reads `config["server"]["http_port"]` for uvicorn; 3 health tests + 3 SPA tests pass |
| 2 | WebSocket endpoint streams 1Hz telemetry (system, bms, pcs, gpio, meter, btms) as JSON to connected clients | VERIFIED | `ws.py` has `telemetry_bridge` subscribing to all 6 topic prefixes via zmq.asyncio SUB, decodes MessagePack via `decode_telemetry`, broadcasts `{topic, data, ts}` JSON to per-client `asyncio.Queue(maxsize=100)`; WebSocket endpoint at `/ws/telemetry` reads from queue and calls `send_json`; 12 tests including real ZMQ PUB/SUB integration and all-6-topics test pass |
| 3 | REST POST endpoints proxy commands to control_cmd and alarm_cmd ZMQ sockets and return responses | VERIFIED | `control.py` has 5 endpoints (mode, setpoint, priority, fault-reset, maintenance) proxying to `SOCK_CONTROL_CMD`; `alarm.py` has 3 endpoints (acknowledge, active, config) proxying to `SOCK_ALARM_CMD`; `query.py` has 1 endpoint with 6 query types proxying to `SOCK_LOGGER_QUERY`; all use `deps.zmq_command` (REQ socket, 5s timeout, `run_in_executor`); 27 proxy tests pass |
| 4 | PIN authentication validates bcrypt-hashed operator/admin PINs, issues session tokens with configurable timeout | VERIFIED | `auth.py` `login` endpoint checks `bcrypt.checkpw` against `config["auth"]["operator_pin_hash"]` and `admin_pin_hash`; issues 64-char hex token via `secrets.token_hex(32)`; `TokenStore` stores `{level, expires_at}` with configurable `session_timeout_s`; max 10 concurrent sessions with cleanup; 13 auth tests pass |
| 5 | Unauthorized requests to protected endpoints return 401; expired sessions return 403 | VERIFIED | `deps.py` `require_auth` returns 401 for missing/invalid token, 403 for expired; `require_admin` returns 403 for non-admin; tests verify 401 on all control/alarm/query endpoints without auth, 403 on expired token, 403 on operator-attempts-maintenance |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/hmi_server/src/ems_hmi_server/app.py` | FastAPI factory with lifespan, CORS, static files, SPA fallback | VERIFIED | 145 lines, lifespan manages ZMQ context + telemetry bridge + token cleanup, CORS for Vite dev, includes all 6 routers |
| `src/hmi_server/src/ems_hmi_server/auth.py` | TokenStore + login/logout endpoints | VERIFIED | 147 lines, bcrypt validation, token creation/validation/removal/cleanup, max 10 sessions |
| `src/hmi_server/src/ems_hmi_server/deps.py` | Shared auth + ZMQ command helper | VERIFIED | 95 lines, require_auth (401/403), require_admin (403), zmq_command (REQ/5s timeout/run_in_executor) |
| `src/hmi_server/src/ems_hmi_server/health.py` | GET /api/health | VERIFIED | 20 lines, returns {status, uptime_s} |
| `src/hmi_server/src/ems_hmi_server/control.py` | 5 control command endpoints | VERIFIED | 75 lines, mode/setpoint/priority/fault-reset/maintenance, maintenance requires admin |
| `src/hmi_server/src/ems_hmi_server/alarm.py` | 3 alarm endpoints | VERIFIED | 49 lines, acknowledge/active/config, all require auth |
| `src/hmi_server/src/ems_hmi_server/query.py` | 1 query endpoint with 6 types | VERIFIED | 45 lines, Literal type validation, params forwarding |
| `src/hmi_server/src/ems_hmi_server/ws.py` | WebSocket bridge + ClientManager | VERIFIED | 141 lines, ZMQ SUB to asyncio.Queue fan-out, drop-oldest overflow, 6 topic subscriptions |
| `src/hmi_server/src/ems_hmi_server/models.py` | Pydantic v2 request/response models | VERIFIED | 110 lines, 12 models covering health, auth, control, alarm, query, ZMQ response |
| `src/hmi_server/src/ems_hmi_server/config.py` | YAML config loader | VERIFIED | 22 lines, yaml.safe_load |
| `src/hmi_server/src/ems_hmi_server/__main__.py` | Entry point with argparse + uvicorn | VERIFIED | 70 lines, --config, --log-level, programmatic uvicorn.Server start |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `app.py` | `health.py`, `auth.py`, `control.py`, `alarm.py`, `query.py`, `ws.py` | `include_router()` | WIRED | All 6 routers included in create_app |
| `control.py` | `deps.zmq_command` | `deps.zmq_command(SOCK_CONTROL_CMD, ...)` | WIRED | Module-level import for monkeypatch compatibility |
| `alarm.py` | `deps.zmq_command` | `deps.zmq_command(SOCK_ALARM_CMD, ...)` | WIRED | Same pattern as control |
| `query.py` | `deps.zmq_command` | `deps.zmq_command(SOCK_LOGGER_QUERY, ...)` | WIRED | Same pattern as control |
| `deps.zmq_command` | `ems_common.ipc` | `encode_command_request`, `decode_command_response` | WIRED | Uses ems-common IPC helpers for MessagePack encode/decode |
| `ws.py` | `ems_common.ipc` | `decode_telemetry`, topic constants | WIRED | Imports TOPIC_BMS_RACK, TOPIC_PCS, etc. and decode_telemetry |
| `auth.py` | `deps.require_auth` | `Depends(require_auth)` in logout | WIRED | FastAPI dependency injection |
| All protected endpoints | `deps.require_auth` | `Depends(require_auth)` | WIRED | Control (5), alarm (3), query (1) endpoints use auth dependency |
| `app.py` lifespan | `ws.telemetry_bridge` | `asyncio.create_task(telemetry_bridge(...))` | WIRED | Bridge started on startup, cancelled on shutdown |
| `__main__.py` | `app.create_app` + `config.load_hmi_config` | Direct import and call | WIRED | Entry point loads config, creates app, starts uvicorn |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| HMI-01 | 18-01-PLAN | FastAPI serves React static files, configurable port, CORS | SATISFIED | StaticFiles mount, SPA fallback, CORS middleware, configurable http_port in __main__.py |
| HMI-02 | 18-03-PLAN | WebSocket streams 1Hz telemetry from ZMQ SUB as JSON | SATISFIED | telemetry_bridge with all 6 topic subscriptions, JSON broadcast to clients |
| HMI-03 | 18-02-PLAN | REST API proxies commands to control/alarm via ZMQ REQ/REP | SATISFIED | 9 REST endpoints across control/alarm/query routers, ZMQ REQ with 5s timeout |
| HMI-04 | 18-01-PLAN | PIN auth with operator/admin, bcrypt, configurable timeout | SATISFIED | bcrypt.checkpw validation, 2-tier tokens, session_timeout_s from config |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | - | - | - | No anti-patterns found. Zero TODO/FIXME/PLACEHOLDER/stub patterns in source. |

### Human Verification Required

### 1. WebSocket Real-Time Streaming

**Test:** Start the full EMS stack (data_manager + hmi_server), open browser to ws://localhost:8080/ws/telemetry, observe messages
**Expected:** JSON messages arrive at ~1Hz with `{topic, data, ts}` format for system/bms/pcs/gpio/meter/btms topics
**Why human:** Integration requires multiple running services and real ZMQ PUB data flow; unit tests use mock PUB sockets

### 2. Static File Serving with React Build

**Test:** Run `cd frontend && bun run build`, then start hmi_server, visit http://localhost:8080/
**Expected:** React app loads; navigating to /alarms, /control etc. returns index.html (SPA routing works)
**Why human:** Requires actual React build artifacts and browser rendering verification

### 3. PIN Login Flow End-to-End

**Test:** Start hmi_server with real hmi_config.yaml, POST /api/auth/login with known operator/admin PINs
**Expected:** Token returned, subsequent API calls with Bearer token succeed, calls without token return 401
**Why human:** Verifies bcrypt hash matching with production config values (tests use generated hashes)

### Gaps Summary

No gaps found. All 5 success criteria are verified with comprehensive test coverage (62 tests). All 4 requirements (HMI-01 through HMI-04) are satisfied. All CONTEXT.md locked decisions are correctly implemented: single ZMQ SUB with per-client queue fan-out, 1:1 REST-to-ZMQ proxy, opaque bearer tokens with bcrypt PIN validation, modular router-based app structure.

---

_Verified: 2026-03-15T08:00:00Z_
_Verifier: Claude (gsd-verifier)_
