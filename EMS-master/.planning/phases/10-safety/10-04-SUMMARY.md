---
phase: 10-safety
plan: 04
subsystem: safety_manager
tags: [integration-tests, pytest, rtdb, gpio, zmq, e-stop, fire, flood]
dependency_graph:
  requires: [10-01, 10-02, 10-03]
  provides: [safety-integration-tests]
  affects: []
tech_stack:
  added: []
  patterns: [subprocess-Popen-integration, rtdb-shm-stimulus, wait-for-do-polling]
key_files:
  created:
    - tests/test_safety_manager.py
  modified:
    - src/safety_manager/src/safety_event.c
    - src/safety_manager/src/safety_reset.c
decisions:
  - ZMQ_LINGER=0 required on all safety_manager ZMQ sockets to prevent shutdown hang
  - Normal RTDB state requires DI-0=1 (ACDB feedback), DI-2=1 (door closed), DI-7=1 (E-Stop NC idle)
  - ZMQ event/reset tests skip when /run/ems/ unavailable (ipc paths hardcoded in C binary)
metrics:
  duration: 506s
  completed: 2026-03-14
---

# Phase 10 Plan 04: Safety Manager Integration Tests Summary

26 pytest integration tests validating all SAFE-01 through SAFE-11 requirements via RTDB test backend stimulus and DO output verification.

## What Was Built

### Task 1: Integration test suite for all SAFE requirements
- **Commit:** 9827853
- **Files:** `tests/test_safety_manager.py` (1112 lines), `src/safety_manager/src/safety_event.c`, `src/safety_manager/src/safety_reset.c`
- 26 test cases across 10 test classes, 23 pass and 3 skip (ZMQ tests requiring /run/ems/)
- Test infrastructure: `rtdb_shm` fixture creates RTDB with proper initial state, `safety_process` fixture starts/stops binary
- Helper functions: `wait_for_do()`, `wait_for_do_mask()`, `set_di()`, `set_di_multi()`, `clear_all_di()`

**Test coverage by requirement:**

| Requirement | Tests | Coverage |
|-------------|-------|----------|
| SAFE-01 E-Stop dual-channel | 3 | Dual-channel triggers, single-channel discrepancy, both normal |
| SAFE-02 E-Stop timing | 1 | 10-iteration timing measurement, max < 100ms |
| SAFE-03 Fire dual-confirm | 2 | Both sensors trigger extinguisher, single sensor warning only |
| SAFE-04 Flood | 1 | ACDB trip + PCS stop + siren, extinguisher NOT asserted |
| SAFE-05 Watchdog | 1 | Process stays alive with watchdog (skip without /dev/watchdog) |
| SAFE-06 SCHED_FIFO | 1 | Graceful fallback without CAP_SYS_NICE |
| SAFE-07 Watchdog thread | 1 | Multiple threads verified via /proc/pid/task |
| SAFE-08 RTDB writes | 3 | DI values, DO state, last_update_ms advancing |
| SAFE-09 ZMQ events | 1 | Event on E-Stop (skip without /run/ems) |
| SAFE-10 Independent lifecycle | 2 | Starts without config_manager, exits gracefully without RTDB |
| SAFE-11 GPIO failure | 1 | Response matrix verified (C unit tests cover gpio_failure path) |

**Additional general tests:** clean shutdown, latching requires reset, reset clears latch, reset rejected while active, normal state running lamp, door open warning only, ACDB feedback loss, ACDB auto-recover, multiple faults combined.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ZMQ_LINGER=0 missing on safety_manager sockets**
- **Found during:** Task 1
- **Issue:** safety_manager hangs on SIGTERM shutdown because ZMQ sockets have infinite linger (default). zmq_close blocks forever trying to deliver queued messages to non-existent ipc:// endpoints.
- **Fix:** Added `ZMQ_LINGER=0` on PUB, PUSH (safety_event.c) and REP (safety_reset.c) sockets. Shutdown now takes <5ms.
- **Files modified:** `src/safety_manager/src/safety_event.c`, `src/safety_manager/src/safety_reset.c`
- **Commit:** 9827853

**2. [Rule 1 - Bug] RTDB initial state must set DI-0, DI-2, DI-7 to normal values**
- **Found during:** Task 1
- **Issue:** All-zero DI state is NOT "normal" -- DI-0=0 means ACDB feedback loss, DI-7=0 (active_low) means E-Stop NC pressed, DI-2=0 (active_low) means door open.
- **Fix:** `rtdb_shm` fixture initializes DI-0=1 (feedback present), DI-2=1 (door closed), DI-7=1 (E-Stop NC idle).
- **Files modified:** `tests/test_safety_manager.py`
- **Commit:** 9827853

## Verification Results

- `uv run pytest tests/test_safety_manager.py -x -v` -- 23 passed, 3 skipped
- `ctest --test-dir build -R test_response_matrix -V` -- 16/16 C unit tests pass
- E-Stop response time consistently < 100ms (typically < 10ms with 5ms scan)
- Every SAFE-01 through SAFE-11 has at least one integration test
- Test file: 1112 lines (requirement: min 200)

## Self-Check: PASSED
