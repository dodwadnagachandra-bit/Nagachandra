---
phase: 10-safety
plan: 01
subsystem: safety_manager
tags: [gpio, response-matrix, safety, c, unit-tests]
dependency_graph:
  requires: [rtdb.h, seqlock.h, ems_types.h, gpio_config.yaml]
  provides: [gpio_ops_t, gpio_ops_rtdb, gpio_ops_libgpiod, safety_state_t, evaluate_inputs, evaluate_response_matrix, safety_reset]
  affects: [safety_manager main loop, safety_event publishing]
tech_stack:
  added: [libgpiod v2 (conditional)]
  patterns: [vtable abstraction, conditional compilation, CTest unit tests, latch-and-reset safety logic]
key_files:
  created:
    - src/safety_manager/src/gpio.h
    - src/safety_manager/src/gpio.c
    - src/safety_manager/src/response_matrix.h
    - src/safety_manager/src/response_matrix.c
    - src/safety_manager/tests/CMakeLists.txt
    - src/safety_manager/tests/test_response_matrix.c
  modified:
    - src/safety_manager/CMakeLists.txt
decisions:
  - "libgpiod compiled conditionally via HAVE_LIBGPIOD -- RTDB backend always available for CI/dev"
  - "Active-low inversion in response_matrix (evaluate_inputs) not GPIO layer -- RTDB backend stores raw values"
  - "E-Stop discrepancy triggers warning lamp, not E-Stop response -- per CONTEXT.md locked decision"
  - "fire_single_sensor bool added to safety_state_t for single-sensor warning lamp"
metrics:
  duration: 6m
  tasks_completed: 2
  tasks_total: 2
  tests_added: 16
  tests_passed: 16
  completed: 2026-03-14T02:46:40Z
---

# Phase 10 Plan 01: GPIO Abstraction & Response Matrix Summary

GPIO vtable with libgpiod v2 and RTDB backends; safety response matrix implementing full DI-to-DO mapping with E-Stop dual-channel, fire dual-confirm, flood, latching, and safety_reset validation.

## What Was Built

### GPIO Abstraction Layer (gpio.h/c)

- `gpio_ops_t` vtable with `init`, `read_di`, `write_do`, `close` function pointers
- `gpio_ops_libgpiod` backend (behind `#ifdef HAVE_LIBGPIOD`): uses libgpiod v2 API exclusively -- separate DI/DO line requests, consumer "ems-safety-manager", proper resource cleanup chain
- `gpio_ops_rtdb` backend: reads DI from `rtdb->gpio.di[]`, writes DO to `rtdb->gpio.do_state[]` -- enables hardware-free testing via GPIO harness
- `gpio_config_t` struct with per-pin `active_low_di[8]` and `active_low_do[8]` flags
- `rtdb_backend_init()` to set RTDB pointer before RTDB backend use
- `read_di` returns raw values; active-low inversion deferred to response_matrix layer
- `write_do` on libgpiod: logs errors but continues writing remaining pins (DO failures never block)

### Response Matrix (response_matrix.h/c)

- `safety_state_t` with current conditions + latch tracking (estop/fire/flood latched)
- DO bitmask constants (DO_ACDB_TRIP through DO_SIREN) and PROTECTIVE_OUTPUTS mask
- `evaluate_inputs()`: applies active_low inversion, detects E-Stop dual-channel confirm/discrepancy, fire dual-confirm/single-sensor, flood, ACDB loss, door open
- `evaluate_response_matrix()`: computes DO bitmask per CONTEXT.md table, Running lamp OFF when any protective output (IEC 60073)
- `safety_reset()`: validates inputs cleared before accepting latch reset, returns -1 if inputs still active
- No dynamic allocation in any evaluation path

### Unit Tests (16 test cases)

All 16 tests pass via CTest:

| # | Test | Verifies |
|---|------|----------|
| 1 | test_normal_state | Only running lamp ON in normal operation |
| 2 | test_estop_dual_channel_confirm | Both DI-6+DI-7 -> ACDB trip, fault, PCS stop, siren |
| 3 | test_estop_discrepancy | Single channel -> warning only, no E-Stop response |
| 4 | test_fire_dual_confirm | DI-3+DI-4 -> ACDB trip, extinguisher, fault, PCS stop, siren |
| 5 | test_fire_single_sensor | DI-3 only -> warning lamp, no extinguisher |
| 6 | test_flood | DI-1 -> ACDB trip, fault, PCS stop, siren |
| 7 | test_acdb_loss | DI-0 low -> fault, PCS stop, siren (no ACDB re-trip) |
| 8 | test_door_open | DI-2 active_low -> warning lamp only |
| 9 | test_gpio_failure | All safety outputs asserted (worst case) |
| 10 | test_estop_latched | Latch persists after inputs clear |
| 11 | test_reset_rejected_inputs_active | Reset rejected when E-Stop still pressed |
| 12 | test_reset_accepted_inputs_cleared | Reset accepted, latch clears, running lamp returns |
| 13 | test_multiple_conditions | E-Stop + door combine outputs correctly |
| 14 | test_running_lamp_off_during_fault | IEC 60073 compliance (green+red prohibited) |
| 15 | test_fire_latch_and_reset | Fire latch/reset cycle |
| 16 | test_flood_latch_and_reset | Flood latch/reset cycle |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] libgpiod not installed on dev system**
- **Found during:** Task 1
- **Issue:** libgpiod-dev not available and cannot install without sudo
- **Fix:** Made libgpiod conditional via `#ifdef HAVE_LIBGPIOD` and CMake `pkg_check_modules(GPIOD libgpiod>=2.0)`. RTDB backend always compiles.
- **Files modified:** CMakeLists.txt, gpio.h, gpio.c
- **Commit:** 21c8fc7

**2. [Rule 3 - Blocking] Existing untracked files from parallel session**
- **Found during:** Task 1
- **Issue:** safety_event.c/h, safety_reset.c/h, watchdog.c/h already existed from another plan's execution
- **Fix:** CMakeLists.txt already referenced them; added gpio.c and response_matrix.c to existing file list
- **Files modified:** CMakeLists.txt
- **Commit:** 21c8fc7

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | 21c8fc7 | feat(10-01): GPIO abstraction layer and safety response matrix |
| 2 | 8e90530 | test(10-01): add response matrix unit tests with 16 test cases |

## Verification Results

- `cmake --build build --target safety_manager` -- PASS (compiles without errors)
- `ctest --test-dir build -R test_response_matrix -V` -- PASS (16/16 tests, 0 failures)
- Response matrix covers every row in CONTEXT.md safety response matrix table
- GPIO vtable exposes both libgpiod (conditional) and RTDB backend implementations
- No dynamic allocation in response matrix evaluation path
