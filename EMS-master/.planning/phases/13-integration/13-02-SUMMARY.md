---
phase: 13-integration
plan: "02"
subsystem: testing
tags: [integration-tests, startup-sequence, health-check]
dependency_graph:
  requires: [13-01]
  provides: [TestStartupSequence, build_start_order, startup-validation]
  affects: [13-03, 13-04, 13-05]
tech_stack:
  added: []
  patterns: [delay-ready-closure, skip-on-missing-binary, reverse-cleanup]
key_files:
  created:
    - tests/integration/test_startup.py
  modified: []
decisions:
  - "build_start_order constructs module specs dynamically from profile topology args"
  - "Modules with missing binaries or vcan0 are skipped with warnings, not failures"
  - "delay_ready closure avoids time.sleep blocking -- returns True after elapsed threshold"
  - "PID ordering used as sanity check for startup order (kernel assigns monotonically)"
  - "ZMQ telemetry test skips gracefully when /run/ems/ unavailable or no publisher active"
metrics:
  duration: "1m 18s"
  completed: "2026-03-14"
  tasks_completed: 1
  tasks_total: 1
---

# Phase 13 Plan 02: Startup Sequence Tests Summary

Startup sequence validation with 4 tests covering dependency-ordered launch of 7 modules, RTDB validity, ZMQ telemetry flow, and PID-based ordering verification.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create startup sequence tests | 321feaa | tests/integration/test_startup.py |

## Key Components Created

### build_start_order(profile) (function)
Constructs ordered list of 7 module specs (data_manager_c, data_manager_python, config_manager, safety_manager, comm_manager_c, comm_manager_python, logger) with cmd, ready_check, env, and skip_reason fields. Dynamically checks binary existence and vcan0 availability to populate skip_reason.

### TestStartupSequence (class)
Class-scoped `all_modules` fixture launches modules sequentially in dependency order. Cleanup runs in reverse order via try/finally. Four test methods:

1. **test_all_modules_start_within_30s** -- Verifies all processes alive, total check < 30s
2. **test_rtdb_valid_after_startup** -- Attaches to RTDB, asserts magic=0x454D5352 and version=1
3. **test_zmq_telemetry_flowing** -- Subscribes to SOCK_TELEMETRY, waits 5s for any message
4. **test_startup_order_data_manager_first** -- data_manager_c PID lowest (started first)

### _delay_ready(seconds) (helper)
Closure-based ready check that returns True after a fixed delay from first call. Avoids blocking -- compatible with ModuleProcess._wait_ready poll loop.

## Deviations from Plan

None -- plan executed exactly as written.

## Verification Results

- `ast.parse(test_startup.py)` -- syntax ok
- `pytest --collect-only` -- 4 tests collected (TestStartupSequence: test_all_modules_start_within_30s, test_rtdb_valid_after_startup, test_zmq_telemetry_flowing, test_startup_order_data_manager_first)

## Self-Check: PASSED
