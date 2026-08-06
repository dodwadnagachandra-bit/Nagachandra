---
phase: 17-integration
plan: "01"
subsystem: integration-tests
tags: [integration, control_manager, alarm_manager, crash-recovery, startup]
dependency_graph:
  requires: [14-01, 14-02, 14-03, 15-01, 15-02, 15-03, 16-01, 16-02]
  provides: [SC-1, SC-4]
  affects: [test_startup.py, test_crash_recovery.py, Makefile]
tech_stack:
  added: []
  patterns: [parametrized-crash-matrix, startup-order-fixture, delay-ready-check]
key_files:
  created: []
  modified:
    - tests/integration/test_startup.py
    - tests/integration/test_crash_recovery.py
    - Makefile
decisions:
  - control_manager and alarm_manager use _delay_ready(2.0) in startup test (ZMQ REP bind + RTDB attach latency)
  - control_manager always restarts to IDLE (per 14-02); no special recovery criterion beyond alive
  - alarm_manager re-evaluates all alarms on first tick; alive check is sufficient
  - M2 crash entries placed in CRASH_MATRIX before C modules to keep Python modules grouped
  - test-integration-m2 timeout set to 600s (protection flow 60s + dispatch 30s + hot-reload 15s + startup 30s + crash recovery 60s per module)
metrics:
  duration: "2 minutes"
  completed_date: "2026-03-15"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 3
---

# Phase 17 Plan 01: M2 Integration Test Extensions Summary

Extend existing M1 integration tests to include control_manager and alarm_manager, and add a Makefile target for M2 integration tests.

## What Was Built

Extended M1 integration test infrastructure (test_startup.py and test_crash_recovery.py) with M2 module specs, and added a `test-integration-m2` Makefile target that runs the full M2 integration suite.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extend test_startup.py and test_crash_recovery.py with M2 module specs | 26c1b95 | tests/integration/test_startup.py, tests/integration/test_crash_recovery.py |
| 2 | Add test-integration-m2 Makefile target | d2a07d6 | Makefile |

## Key Changes

### test_startup.py

- Updated module docstring to list startup steps 8 (control_manager) and 9 (alarm_manager)
- Added two entries to `build_start_order()` after the logger entry:
  - `control_manager`: `uv run python -m ems_control_manager --config control_config.yaml` with `_delay_ready(2.0)`
  - `alarm_manager`: `uv run python -m ems_alarm_manager --config alarms_config.yaml` with `_delay_ready(2.0)`
- Startup ordering enforced: M1 modules -> control_manager -> alarm_manager

### test_crash_recovery.py

- Updated module docstring to mention M2 crash recovery testing
- Added `control_manager` and `alarm_manager` to `_MODULE_SPECS` with `requires_c=False, requires_vcan=False`
- Added both to `STARTUP_ORDER` after logger: `[..., "logger", "control_manager", "alarm_manager"]`
- Added 4 entries to `CRASH_MATRIX` before C module conditional block:
  - `("control_manager", signal.SIGKILL)`
  - `("control_manager", signal.SIGTERM)`
  - `("alarm_manager", signal.SIGKILL)`
  - `("alarm_manager", signal.SIGTERM)`

### Makefile

- Added `test-integration-m2` to `.PHONY` list
- Added new target after `test-integration`:
  ```makefile
  test-integration-m2: ## Run M2 integration tests (control + alarm)
  	uv run pytest tests/integration/test_m2_integration.py tests/integration/test_startup.py tests/integration/test_crash_recovery.py -v -m integration --timeout=600
  ```

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

All three plan verification checks pass:
1. `CRASH_MATRIX` contains control_manager entries
2. `make -n test-integration-m2` outputs correct pytest command
3. Both test modules import cleanly with no regressions

## Self-Check: PASSED

- `tests/integration/test_startup.py` — modified, control_manager and alarm_manager present
- `tests/integration/test_crash_recovery.py` — modified, 4 new CRASH_MATRIX entries
- `Makefile` — modified, test-integration-m2 target present
- Commit 26c1b95: feat(17-01): extend M1 integration tests with control_manager and alarm_manager
- Commit d2a07d6: chore(17-01): add test-integration-m2 Makefile target
