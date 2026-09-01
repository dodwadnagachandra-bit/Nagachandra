---
phase: 17-integration
plan: "02"
subsystem: integration
tags: [integration-tests, control-manager, alarm-manager, protection-flow, dispatch-flow, zmq, modbus, vcan, rtdb, iec-62682]

# Dependency graph
requires:
  - phase: 16-02
    provides: ControlLoop+AlarmLoop fully wired with alarm protection, config hot-reload
  - phase: 14-01
    provides: RTDB command path, PCS Modbus write_setpoint/process_command
  - phase: 15-03
    provides: AlarmLoop with ZMQ PUB alarm events (severity: protection/action/warning)
  - phase: 13-integration
    provides: ModuleProcess, wait_for_criteria, conftest.py test infrastructure

provides:
  - tests/integration/test_m2_integration.py with TestProtectionFlow and TestDispatchFlow
  - End-to-end proof of 8-step BMS protection chain (cell voltage -> alarm -> FAULT -> PCS stop)
  - End-to-end proof of 5 dispatch scenarios (normal, SOC cutoff, temp derating, manual, no-source)
  - Safety interlock validation (CTRL-09): emergency blocks DISCHARGING
  - ZMQ telemetry rate validation (CTRL-12): >= 3 messages/4s on control.state PUB
  - Env var ZMQ endpoint override in both __main__.py files for test isolation

affects: [CI, all-integration-test-runs, REQUIREMENTS.md CTRL-01..06/09/10/12, ALM-01/02/05/08]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Random TCP port allocation: bind temporary sockets, collect ports, release, use for ZMQ"
    - "CAN sim restart pattern: kill old Popen, replace sims[0] reference, start new Popen"
    - "Env var endpoint injection: modules read EMS_CONTROL_CMD_ENDPOINT etc at startup"
    - "Adaptive dispatch assertion: read RTDB SOC first, compute expected setpoint, verify PCS register"
    - "ZMQ slow joiner fix: 500ms sleep after SUB connect before collecting messages"

key-files:
  created:
    - tests/integration/test_m2_integration.py
  modified:
    - src/control_manager/python/src/ems_control_manager/__main__.py
    - src/alarm_manager/src/ems_alarm_manager/__main__.py

key-decisions:
  - "Env var ZMQ endpoint override added to both __main__.py (Rule 3 auto-fix — blocked test isolation)"
  - "Dispatch tests use adaptive assertion: read actual RTDB SOC, verify PCS register matches computed expected"
  - "Protection flow test uses CAN sim restart to inject low voltage (kill old Popen, start new with fault_injection config)"
  - "Telemetry test subscribes with both 'control.state' topic and empty string to handle multipart vs prefix framing"
  - "Dispatch fixture pre-transitions to STANDBY after stabilization — individual tests handle subsequent transitions"

patterns-established:
  - "M2 integration test pattern: class-scoped fixture launches all modules + simulators, tests share the running system"
  - "Port conflict prevention: each test class allocates its own set of random TCP ports"
  - "Graceful prerequisite skip: skip if vcan0 unavailable or C binaries not built"

metrics:
  duration: "5 minutes"
  completed: "2026-03-15"
  tasks_completed: 1
  files_created: 1
  files_modified: 2
  test_methods: 9
  lines_written: 1526
---

# Phase 17 Plan 02: M2 Integration Tests Summary

**One-liner:** M2 graduation tests — 8-step BMS protection chain + 5 dispatch scenarios proving control_manager and alarm_manager work end-to-end across 5 module boundaries.

## What Was Built

A 1526-line integration test file (`tests/integration/test_m2_integration.py`) covering two test classes:

**TestProtectionFlow** — validates the alarm-to-control protection chain:
- `test_step1_all_modules_healthy`: verifies all 11 module processes alive + RTDB populated
- `test_step2_to_step8_protection_chain`: full 8-step chain (STANDBY -> low voltage CAN inject -> alarm delay 5s -> FAULT -> PCS register 0x500E=0 -> voltage restore -> fault_reset -> IDLE)
- `test_interlock_blocks_dispatch_on_emergency`: CTRL-09 — E-Stop via RTDB GPIO DI-2 -> STATE_EMERGENCY -> DISCHARGING command rejected
- `test_control_telemetry_at_1hz`: CTRL-12 — subscribes to control.state PUB, asserts >= 3 messages in 4 seconds

**TestDispatchFlow** — validates 5 dispatch scenarios:
- `test_normal_discharge`: SOC > 10%, NIGHT mode -> PCS register > 0, <= 250 (25 kW)
- `test_soc_cutoff`: CTRL-05 — fault_injection soc_base=10% -> IDLE, PCS=0
- `test_temperature_derating`: CTRL-06 — fault_injection cell_temp_base=48°C -> derated PCS register < 250
- `test_manual_override`: CTRL-10 — manual_setpoint(15.0 kW) -> PCS register ≈ 150 (±10)
- `test_no_source_available`: CTRL-04 — low SOC + grid offline (RTDB DI-0=0) -> IDLE, PCS=0

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking Fix] Added ZMQ endpoint env var support to both __main__.py files**

- **Found during:** Task 1 implementation — plan required `EMS_CONTROL_CMD_ENDPOINT` etc. env vars to reach modules launched as subprocesses, but neither `__main__.py` read from `os.environ`
- **Issue:** `ControlLoop(config)` called with no endpoint arguments, so always bound to default `ipc:///run/ems/` paths; integration tests that pass TCP endpoints via env vars would have no effect
- **Fix:** Added `os.environ.get("EMS_CONTROL_CMD_ENDPOINT")` etc. in both `__main__.py` files; non-null env var values passed as keyword args to `ControlLoop`/`AlarmLoop`; also wired `config_path=args.config` so hot-reload works in integration tests
- **Files modified:** `src/control_manager/python/src/ems_control_manager/__main__.py`, `src/alarm_manager/src/ems_alarm_manager/__main__.py`
- **Commit:** 9c3254b (included in the task commit)

## Commits

| Commit | Description |
|--------|-------------|
| 9c3254b | feat(17-02): add M2 integration tests + env var endpoint fix in both __main__.py |

## Self-Check: PASSED

- FOUND: tests/integration/test_m2_integration.py (1526 lines, 9 test methods)
- FOUND: src/control_manager/python/src/ems_control_manager/__main__.py (env var support)
- FOUND: src/alarm_manager/src/ems_alarm_manager/__main__.py (env var support)
- FOUND commit 9c3254b in git log
