---
phase: 10-safety
plan: 03
subsystem: safety_manager
tags: [rt-scheduling, gpio, scan-loop, systemd, integration]
dependency_graph:
  requires: [10-01, 10-02]
  provides: [safety_manager-binary, systemd-service]
  affects: [10-04]
tech_stack:
  added: []
  patterns: [SCHED_FIFO-RT, mlockall-prefault, seqlock-RTDB-write, compiled-config-defaults]
key_files:
  created: []
  modified:
    - src/safety_manager/src/main.c
    - src/safety_manager/CMakeLists.txt
    - deploy/systemd/safety_manager.service
decisions:
  - Compiled-in residential profile GPIO defaults instead of YAML parser for config fallback
  - safety_reset_poll API uses latched bitmask (matching actual 10-02 implementation, not plan interface)
metrics:
  duration: 213s
  completed: 2026-03-14
---

# Phase 10 Plan 03: Main Loop Integration, RT Setup, Config Loading, and Systemd Service Summary

Complete safety_manager binary with RT scan loop, RTDB seqlock writes, and hardened systemd service.

## What Was Built

### Task 1: Main entry point with RT setup, config loading, and scan loop
- **Commit:** eb4d389
- **Files:** `src/safety_manager/src/main.c` (727 lines), `src/safety_manager/CMakeLists.txt`
- Complete startup sequence: signal handlers, mlockall + SCHED_FIFO priority 80, 64 KiB stack pre-fault
- Config loading uses compiled-in residential profile defaults (active_low_di[2] = door, active_low_di[7] = E-Stop NC)
- RTDB attach with 3x retry, continues without RTDB per SAFE-10 independence
- GPIO backend selection: libgpiod (production) or RTDB (test) via `--rtdb-backend` or `EMS_GPIO_BACKEND=rtdb`
- GPIO init failure sets `gpio_failure = true`, response matrix asserts all safety outputs (SAFE-11)
- 10ms scan loop: read DI -> evaluate_inputs -> safety_reset_poll -> evaluate_response_matrix -> write DO -> RTDB seqlock write -> event publish -> watchdog signal
- DO state change event publishing with CRITICAL severity for protective outputs
- Shutdown: watchdog thread stop, watchdog magic close, all DO de-asserted, GPIO close, ZMQ close, RTDB detach
- CLI args: `--config`, `--chip`, `--rtdb-backend`, `--no-watchdog`, `--scan-interval-ms`

### Task 2: CMake build config and systemd service hardening
- **Commit:** 1841c7e
- **Files:** `deploy/systemd/safety_manager.service`
- CMake already complete from Task 1 fix: all 6 source files, ems_rtdb linkage, data_manager include path
- systemd service hardened: Restart=always (SAFE-10), RestartSec=1, CAP_SYS_NICE + CAP_SYS_RAWIO
- LimitRTPRIO=99, LimitMEMLOCK=infinity for RT scheduling
- After/Wants=ems-data-manager.service (not Requires= for independence)
- DeviceAllow for gpiochip0, gpiochip1, watchdog
- Security: ProtectSystem=strict, ProtectHome=yes, PrivateTmp=yes

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added ems_rtdb linkage and data_manager include path in Task 1**
- **Found during:** Task 1
- **Issue:** main.c includes rtdb_lifecycle.h which is in data_manager/c/include, not in safety_manager's include path
- **Fix:** Added `${CMAKE_SOURCE_DIR}/src/data_manager/c/include` to target_include_directories and `ems_rtdb` to target_link_libraries
- **Files modified:** src/safety_manager/CMakeLists.txt
- **Commit:** eb4d389 (included in Task 1 commit)

**2. [Rule 1 - Bug] Used actual safety_reset_poll API instead of plan interface**
- **Found during:** Task 1
- **Issue:** Plan interfaces section listed safety_reset_poll(ctx, state, di_raw, config) but actual API from 10-02 is safety_reset_poll(ctx, di_raw, latched, out_reset)
- **Fix:** Used actual API, building latched bitmask from safety_state_t fields in the scan loop
- **Files modified:** src/safety_manager/src/main.c
- **Commit:** eb4d389

## Verification Results

- Binary compiles and links with all 6 source files
- `--help` flag works, showing all CLI options
- `Restart=always` present in systemd service
- `ems_seqlock_write_begin` found in main.c (RTDB writes)
- `nanosleep` with 10ms interval in scan loop

## Self-Check: PASSED
