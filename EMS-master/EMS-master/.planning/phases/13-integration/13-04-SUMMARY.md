---
phase: 13-integration
plan: "04"
subsystem: testing
tags: [integration-tests, crash-recovery, double-fault, gpio-continuity]
dependency_graph:
  requires: [13-01]
  provides: [crash-recovery-tests, double-fault-tests, gpio-continuity-test]
  affects: [13-05]
tech_stack:
  added: []
  patterns: [sigkill-sigterm-parametrize, rtdb-survival-check, gpio-tight-polling]
key_files:
  created:
    - tests/integration/test_crash_recovery.py
  modified: []
decisions:
  - "C modules added to CRASH_MATRIX dynamically at import time based on binary availability"
  - "GPIO continuity test uses 1ms polling thread with RtdbBackend.get_do() for DO-0 monitoring"
  - "Double-fault data+comm enforces restart ordering: data_manager_c first, then comm_manager_python"
  - "RTDB survival test verifies magic/version unchanged after SIGKILL (shm persists beyond process death)"
metrics:
  duration: "2m 11s"
  completed: "2026-03-14"
  tasks_completed: 1
  tasks_total: 1
---

# Phase 13 Plan 04: Crash Recovery Tests Summary

Parametrized SIGKILL/SIGTERM crash recovery tests for all 7 modules with RTDB integrity checks, GPIO continuity validation during safety_manager restart, and double-fault scenarios for comm+logger and data_manager+comm pairs.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create crash recovery tests (single-module and double-fault) | fb11e5e | tests/integration/test_crash_recovery.py |

## Verification Results

- Syntax check: PASSED (ast.parse valid)
- Test collection: 18 tests collected
  - 14 parametrized crash recovery tests (7 modules x SIGKILL + SIGTERM)
  - 1 RTDB survival test (data_manager_c crash)
  - 1 comm+logger double-fault test
  - 1 GPIO continuity during safety_manager restart test
  - 1 data_manager+comm double-fault with ordering test

## Key Implementation Details

### TestSingleModuleCrashRecovery
- Parametrized over CRASH_MATRIX: 8 Python module tests always present, 6 C module tests added dynamically if binaries exist
- Each test: kill with signal -> wait for death -> restart -> assert alive + RTDB fresh within 10s -> verify new PID
- `test_rtdb_survives_data_manager_crash`: verifies /dev/shm/ems_rtdb persists and magic/version unchanged after SIGKILL

### TestDoubleFault
- `test_comm_and_logger_double_fault`: kill both with SIGKILL, restart comm first then logger, verify independent recovery, safety_manager unaffected
- `test_data_manager_and_comm_double_fault`: kill both, restart data_manager_c FIRST (RTDB owner), then comm_manager_python, verify RTDB integrity
- `test_safety_gpio_continuity_during_restart`: set E-Stop DI inputs, verify DO-0 asserted, start 1ms polling thread, SIGKILL safety_manager, restart, analyze samples for zero de-assertions

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED
