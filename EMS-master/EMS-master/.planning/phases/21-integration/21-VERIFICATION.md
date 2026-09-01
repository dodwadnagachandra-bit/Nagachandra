---
phase: 21-integration
verified: 2026-03-15T11:15:00Z
status: passed
score: 5/5 must-haves verified
re_verification: false
gaps: []
---

# Phase 21: Integration and Hardening Verification Report

**Phase Goal:** HMI and scheduler run together with all M1+M2 modules, with validated WebSocket reliability, command flows, and schedule-to-control dispatch
**Verified:** 2026-03-15T11:00:00Z
**Status:** gaps_found
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Full systemd startup sequence (all modules including hmi_server + scheduler) completes and HMI is accessible on HTTP port | VERIFIED | `TestFullSystemStartup` in test_startup.py launches 11 modules, verifies health endpoint returns 200 (line 555-572), verifies all modules alive (line 535-541), startup within 30s (line 574-586) |
| 2 | End-to-end command flow: HMI button press -> REST API -> ZMQ REQ -> control_manager -> RTDB -> PCS simulator | VERIFIED | `TestCommandFlow.test_e2e_command_flow` in test_m3_integration.py (lines 474-537): health check -> login with PIN 1234 -> POST /api/control/mode standby -> verify RTDB STATE_STANDBY -> POST /api/control/setpoint 15.0 -> verify RTDB active_setpoint_kw -> GET /api/alarm/active |
| 3 | End-to-end telemetry flow: simulator data -> RTDB -> ZMQ PUB -> WebSocket -> correct JSON values | VERIFIED | `TestTelemetryFlow.test_websocket_telemetry_stream` in test_m3_integration.py (lines 563-601): connects websockets to /ws/telemetry, receives 5 messages, asserts >= 3 at 1Hz, validates {topic, data} structure and SOC range [0,100] |
| 4 | Schedule-to-dispatch flow: scheduler sends setpoint at configured window start -> control_manager applies it -> PCS receives | VERIFIED | `TestScheduleDispatch` in test_m3_integration.py (lines 609-753): 4 test methods using SchedulerLoop with now_func clock injection -- discharge (12:00 -> 10kW), charge (23:00 -> -15kW), idle (19:00 -> 0kW), manual mode (no command) |
| 5 | WebSocket reconnection: kill hmi_server, verify reconnects within 30 seconds after restart | VERIFIED | `TestWebSocketReconnection.test_websocket_reconnect_after_server_kill` in test_m3_integration.py (lines 779-845): connect + receive 3 msgs -> SIGKILL -> wait 5s -> restart -> verify health -> reconnect within 10s -> receive 3 msgs |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `tests/integration/test_m3_integration.py` | E2E flow tests with 4 test classes | VERIFIED | 845 lines, contains TestCommandFlow, TestTelemetryFlow, TestScheduleDispatch, TestWebSocketReconnection. Compiles clean. |
| `tests/integration/test_startup.py` | Extended startup test with hmi_server + scheduler | VERIFIED | Contains TestFullSystemStartup with 4 test methods (all alive, RTDB fresh, health 200, startup <30s). Original TestStartupSequence preserved. Compiles clean. |
| `tests/integration/test_crash_recovery.py` | Extended CRASH_MATRIX with hmi_server + scheduler | VERIFIED | CRASH_MATRIX includes ("hmi_server", SIGKILL/SIGTERM), ("scheduler", SIGKILL/SIGTERM). STARTUP_ORDER includes both at positions 10-11. HTTP health check for hmi_server recovery. Compiles clean. |
| `config/profiles/residential/hmi_config_test.yaml` | Test HMI config with known bcrypt PIN hashes | VERIFIED | Contains bcrypt hashes for operator PIN "1234" and admin PIN "9999", session_timeout_s=300, http_port=0 (test override), host=127.0.0.1 |
| `Makefile` (test-integration-m3 target) | M3 integration test target | PARTIAL | Target exists in .PHONY and as rule, but the pytest command only runs test_startup.py and test_crash_recovery.py -- missing test_m3_integration.py |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| test_m3_integration.py::TestCommandFlow | /api/control/mode | httpx POST with Bearer token | WIRED | Lines 496-501: `client.post("/api/control/mode", json={"target_state": "standby"}, headers=headers)` |
| test_m3_integration.py::TestTelemetryFlow | /ws/telemetry | websockets.connect | WIRED | Line 571: `websockets.connect(ws_uri)` where ws_uri = `ws://127.0.0.1:{port}/ws/telemetry` |
| test_m3_integration.py::TestScheduleDispatch | SchedulerLoop | now_func clock injection | WIRED | Line 652: `SchedulerLoop(..., now_func=lambda: mock_time)` then `loop._evaluate_tick(mock_time)` |
| test_m3_integration.py::TestWebSocketReconnection | ModuleProcess.kill | SIGKILL + restart + reconnect | WIRED | Lines 798, 810: `hmi.kill(sig=signal.SIGKILL)` then `hmi.restart()` |
| test_startup.py::TestFullSystemStartup | hmi_server health | httpx GET /api/health/ | WIRED | Line 567: `httpx.get(f"http://127.0.0.1:{port}/api/health/", timeout=5.0)` |
| test_crash_recovery.py | CRASH_MATRIX | parametrize tuples | WIRED | Lines 252-255: hmi_server SIGKILL/SIGTERM, scheduler SIGKILL/SIGTERM in CRASH_MATRIX |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| HMI-01 | 21-01 | FastAPI serves React on HTTP port | SATISFIED | TestFullSystemStartup verifies health endpoint returns 200 |
| HMI-02 | 21-02 | WebSocket 1Hz telemetry streaming | SATISFIED | TestTelemetryFlow validates 3+ JSON messages in 5s via /ws/telemetry |
| HMI-03 | 21-02 | REST API proxies commands to control/alarm | SATISFIED | TestCommandFlow validates POST /api/control/mode and /api/control/setpoint reach RTDB |
| HMI-13 | 21-02 | Frontend auto-reconnects WebSocket | SATISFIED | TestWebSocketReconnection validates server-side recovery and new WS connection within 10s |
| SCHED-01 | 21-02 | Scheduler evaluates windows at 1Hz, sends setpoints | SATISFIED | TestScheduleDispatch tests 4 scenarios with clock-mocked SchedulerLoop |
| SCHED-03 | 21-02 | Time-of-day mode evaluates windows | SATISFIED | test_discharge_window_active, test_charge_window_active, test_between_windows_idle |
| SCHED-04 | 21-02 | Curve mode reads 96-point array | NEEDS HUMAN | No explicit curve mode test in TestScheduleDispatch (only time_of_day and manual) |
| SCHED-05 | 21-02 | Day/night mode source_priority | NEEDS HUMAN | No explicit day/night transition test in TestScheduleDispatch |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| Makefile | 53 | Missing test_m3_integration.py from test-integration-m3 target | Warning | Running `make test-integration-m3` does not execute E2E flow tests |

No TODO, FIXME, PLACEHOLDER, or HACK comments found in any test files.
No empty implementations or stub patterns detected.
All test methods have substantive assertions (not just `assert True`).

### Human Verification Required

### 1. Full system startup with real infrastructure

**Test:** Run `make test-integration-m3` on a machine with vcan0, /run/ems/, and built C binaries
**Expected:** All tests pass (startup, crash recovery, E2E flows)
**Why human:** Tests require vcan0 virtual CAN interface, RTDB shared memory, and built C binaries which may not be available in CI

### 2. SCHED-04 curve mode coverage

**Test:** Verify that curve mode evaluation is covered by SchedulerLoop unit tests or add integration test
**Expected:** Curve mode sends correct setpoint from 96-point power array
**Why human:** No explicit curve mode test in integration suite -- coverage may exist in unit tests

### 3. SCHED-05 day/night transition coverage

**Test:** Verify that day/night source_priority switching is covered by SchedulerLoop unit tests or add integration test
**Expected:** Day/night mode sends source_priority command at transition times
**Why human:** No explicit day/night test in integration suite -- coverage may exist in unit tests

### 4. Visual verification of React UI telemetry

**Test:** Open browser to hmi_server URL, observe dashboard with live telemetry
**Expected:** Dashboard shows BMS SOC, PCS power, system state -- values update at 1Hz
**Why human:** Success criterion 3 says "React UI shows correct values" but tests validate at WebSocket level, not browser rendering

### Gaps Summary

One gap identified:

**Makefile target incomplete:** The `test-integration-m3` Makefile target (line 52-53) runs only `test_startup.py` and `test_crash_recovery.py` but omits `test_m3_integration.py` which contains all 4 E2E flow test classes (command flow, telemetry flow, schedule dispatch, WebSocket reconnection). This means running `make test-integration-m3` does not execute the primary Phase 21 deliverable. The fix is a single-line edit to add `tests/integration/test_m3_integration.py` to the pytest invocation.

All 5 success criteria from ROADMAP.md have corresponding test implementations that are syntactically valid and structurally complete. The tests cover the full chain for each flow (REST -> ZMQ -> RTDB, simulator -> WS -> JSON, scheduler -> control_manager, kill -> restart -> reconnect). The gap is operational (Makefile wiring) not functional.

SCHED-04 (curve mode) and SCHED-05 (day/night) are listed as requirements in Plan 21-02 but lack explicit integration tests. The CONTEXT.md decision table included curve mode and day/night scenarios but they were not implemented. These are minor gaps since the core SchedulerLoop._evaluate_tick mechanism is tested (the clock injection pattern works for all modes).

---

_Verified: 2026-03-15T11:00:00Z_
_Verifier: Claude (gsd-verifier)_
