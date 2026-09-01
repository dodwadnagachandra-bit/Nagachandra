# Phase 21: Integration and Hardening - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Full stack validation: HMI + scheduler + all M1+M2 modules running together. Validated WebSocket reliability, command flows, schedule-to-control dispatch, and crash recovery. No new requirements — validates all Phase 18-20 requirements in integration.

</domain>

<decisions>
## Implementation Decisions

### E2E Command Flow Test Methodology

How to verify: HMI button → REST API → ZMQ → control_manager → RTDB → PCS simulator?

**Decision:** Automated test using httpx (async HTTP client) against a running FastAPI server with all backend modules active.

| Step | Action | Verification | Timeout |
|------|--------|-------------|---------|
| 1 | Start all M1+M2 modules + hmi_server + scheduler + simulators | Health endpoint returns 200 | 30s |
| 2 | POST `/api/auth/login` with operator PIN | Receive token with level=operator | 2s |
| 3 | POST `/api/control/mode` with `{target_state: "standby"}` + Bearer token | Response: `{status: "ok", from: "idle", to: "standby"}` | 10s |
| 4 | Verify PCS simulator received ON command | PCS state == RUNNING in RTDB | 15s |
| 5 | POST `/api/control/setpoint` with `{power_kw: 15.0}` | Response: `{status: "ok", accepted_kw: 15.0}` | 5s |
| 6 | Verify PCS simulator register | 0x500E == 150 (15.0 × 10) | 3s |
| 7 | POST `/api/alarm/active` (GET) | Response: `{status: "ok", alarms: []}` | 2s |

Key rules:
- Tests use httpx.AsyncClient with base_url pointing to the running hmi_server
- Auth token obtained at test start, reused for all requests
- PCS verification reads RTDB or Modbus simulator state directly (not via HMI)
- Each step has an explicit timeout — failures identify which hop broke

**Rationale:** httpx is the standard async HTTP test client for FastAPI. Testing through the full REST→ZMQ→control→RTDB→PCS chain validates all M3 wiring in one test. Step-by-step with timeouts isolates failures (if step 4 fails, it's PCS sequencing; if step 6 fails, it's the RTDB→Modbus path).

### E2E Telemetry Flow Test

How to verify: simulator → RTDB → ZMQ PUB → WebSocket → JSON in browser?

**Decision:** WebSocket client test connects to hmi_server WebSocket endpoint, receives messages, and validates content against known simulator values.

| Step | Action | Verification | Timeout |
|------|--------|-------------|---------|
| 1 | Start all modules + simulators | HMI server healthy | 30s |
| 2 | Connect WebSocket client to `/ws` | Connection established | 5s |
| 3 | Receive messages for 5 seconds | At least 3 messages received (1Hz) | 7s |
| 4 | Parse first "system" topic message | Contains `total_soc`, `total_power_kw`, `control_state` fields | — |
| 5 | Parse first "pcs" topic message | Contains `active_power`, `dc_voltage`, `state` fields | — |
| 6 | Verify SOC value matches simulator | SOC between 20-80% (simulator range) | — |

Key rules:
- WebSocket test uses Python `websockets` library (not browser)
- Messages are JSON (Phase 18 decision) — parse with json.loads
- Test validates message shape (required fields present) and value ranges (not exact values)
- Connection indicator not tested here — that's a frontend visual concern

**Rationale:** WebSocket testing from Python validates the backend bridge without needing a browser. Value range validation (not exact) accounts for simulator signal generator noise. 3+ messages in 5 seconds confirms the 1Hz streaming rate.

### Schedule-to-Dispatch Flow Test

How to verify: scheduler evaluates time window → sends command → control_manager applies setpoint → PCS receives?

**Decision:** Mock the system clock in the scheduler to simulate time window transitions, then verify the PCS register reflects the scheduled power.

| Scenario | Mock Time | Schedule Config | Expected PCS 0x500E |
|----------|-----------|----------------|-------------------|
| Discharge window active | 12:00 (inside 06:00-18:00 discharge) | time_of_day, 10 kW discharge | 100 (10.0 × 10) |
| Charge window active | 23:00 (inside 22:00-06:00 charge) | time_of_day, 15 kW charge | -150 (signed) |
| Between windows | 19:00 (outside all windows) | time_of_day | 0 (idle) |
| Curve mode | Index 48 (12:00) | curve, power_curve[48] = 20.0 | 200 |
| Manual mode | Any time | manual | No command sent |
| Day→Night transition | 18:00 | day_night.night_start = "18:00" | source_priority → NIGHT |

Key rules:
- Clock mocking via environment variable or monkeypatch — scheduler reads mocked time instead of real clock
- Each scenario is a separate test case — not a single long test
- Verify both the ZMQ command sent by scheduler AND the resulting PCS register value
- Day→Night test verifies source_priority command, not power setpoint

**Rationale:** Mocking the clock is essential — real-time tests that wait for actual window transitions would take hours. The 6 scenarios cover all schedule modes and the day/night transition. Verifying both the command and the PCS register catches wiring failures at both ends.

### WebSocket Reconnection Test

How to verify HMI-13 (auto-reconnect with exponential backoff)?

**Decision:** Kill hmi_server process, verify frontend reconnects within 30 seconds after restart. Test at the Python WebSocket client level (not browser).

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Connect WebSocket, receive 3+ messages | Connection established, data flowing |
| 2 | Kill hmi_server (SIGKILL) | WebSocket receives close frame or timeout |
| 3 | Wait 5 seconds | — |
| 4 | Restart hmi_server | Health endpoint returns 200 |
| 5 | Reconnect WebSocket | New connection established within 10s |
| 6 | Receive messages on new connection | Data flowing (3+ messages in 5s) |

Key rules:
- Test validates the server-side recovery (can accept new connections after restart)
- Client-side reconnection logic (exponential backoff) is tested in frontend unit tests, not integration
- The 5-second gap between kill and restart simulates real-world restart time (systemd RestartSec=5)
- Total reconnection window: 30 seconds max (includes server restart + client backoff)

**Rationale:** WebSocket reconnection needs integration testing because it crosses process boundaries. Testing at the Python client level validates server recovery without needing a browser. Frontend backoff logic is unit-testable (mock WebSocket).

### Crash Recovery Additions

| Module | Recovery Behavior | Test |
|--------|-------------------|------|
| hmi_server | Restart within 10s, WebSocket clients reconnect, auth tokens lost (re-login required) | SIGKILL + verify health endpoint returns 200 |
| scheduler | Restart within 10s, re-evaluate current window, send correct command | SIGKILL + verify PCS register reflects scheduled setpoint |

Key rules:
- Add hmi_server and scheduler to existing CRASH_MATRIX (Phase 13/17 pattern)
- hmi_server crash loses all auth tokens (in-memory) — clients must re-login. This is acceptable for embedded kiosk.
- Scheduler crash: on restart, immediately evaluates current time and sends appropriate command — no "wait for next window" delay.

### Claude's Discretion

- Test infrastructure reuse from Phase 13/17 (conftest.py, ModuleProcess)
- httpx vs requests for REST API testing
- WebSocket test library (websockets vs aiohttp)
- Clock mocking approach for scheduler tests
- Makefile target naming (test-integration-m3)
- Whether to include HMI visual regression tests (likely skip for M3)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/integration/conftest.py` — ModuleProcess, MetricsCollector, build_start_order
- `tests/integration/test_crash_recovery.py` — CRASH_MATRIX pattern
- `tests/integration/test_startup.py` — Startup ordering tests
- `tests/integration/test_m2_integration.py` — Protection flow and dispatch flow patterns
- All M1+M2 modules — running as integration test fixtures
- CAN/Modbus/GPIO simulators — test stimulus

### Integration Points
- Phase 18 hmi_server: HTTP on port 8080, WebSocket on port 8081
- Phase 19 frontend: served as static files by FastAPI
- Phase 20 scheduler: ZMQ REQ to control_cmd
- M2 control_manager: ZMQ REP on control_cmd, PUB on telemetry
- M2 alarm_manager: ZMQ REP on alarm_cmd, PUB on alarm
- M1 logger: ZMQ REP on logger_query (for energy/history queries via HMI proxy)

</code_context>

<specifics>
## Specific Ideas

- E2E command flow test is the highest-value integration test — crosses HTTP → ZMQ → RTDB → Modbus
- WebSocket telemetry flow validates the entire data pipeline from simulator to browser-ready JSON
- Schedule-to-dispatch test validates the scheduler → control_manager → PCS chain
- Crash recovery for hmi_server is straightforward — stateless REST server, only auth tokens lost
- Frontend visual testing deferred — React components tested in unit tests, not integration

</specifics>

<deferred>
## Deferred Ideas

- Browser-based E2E tests (Playwright/Cypress) — deferred, Python tests sufficient for M3
- Visual regression testing — deferred to M5 production hardening
- Load testing (multiple WebSocket clients) — embedded kiosk is single-user
- HMI accessibility testing (screen reader, contrast) — future requirement
- Performance profiling on ARM display — deferred to ECU hardware testing (PLAT-01)

</deferred>

---

*Phase: 21-integration*
*Context gathered: 2026-03-15*
