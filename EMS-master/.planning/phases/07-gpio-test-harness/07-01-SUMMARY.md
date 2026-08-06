---
phase: 07-gpio-test-harness
plan: 01
subsystem: tools/simulators/gpio_harness
tags: [gpio, simulator, safety, rtdb, shm, testing]
dependency_graph:
  requires: [src/common/python/src/ems_common/rtdb.py, config/gpio_config.yaml]
  provides: [tools/simulators/gpio_harness]
  affects: [tests/test_gpio_harness.py, Makefile]
tech_stack:
  added: []
  patterns: [seqlock-rw, resource-tracker-unregister, configfs-lifecycle]
key_files:
  created:
    - tools/simulators/gpio_harness/backend.py
    - tools/simulators/gpio_harness/rtdb_backend.py
    - tools/simulators/gpio_harness/config.py
    - tools/simulators/gpio_harness/gpio_sim_backend.py
    - tools/simulators/gpio_harness/__main__.py
    - tools/simulators/gpio_harness/__init__.py
    - tests/test_gpio_harness.py
  modified:
    - Makefile
    - pyproject.toml
decisions:
  - "Simulators are NOT uv workspace members (follows can_sim/modbus_sim pattern); plan specified pyproject.toml as workspace member but that breaks existing convention"
  - "resource_tracker.unregister() used instead of track=False (Python 3.12 lacks track kwarg, added in 3.13)"
metrics:
  duration: "~6 min"
  completed: "2026-03-13"
  tests_added: 21
  tests_total: 112
requirements: [SIM-05]
---

# Phase 07 Plan 01: GPIO Test Harness Summary

GPIO test harness injecting 8 DI + 8 DO safety signals into RTDB shared memory with seqlock atomicity, pin name resolution from gpio_config.yaml, and CLI set/get/daemon subcommands.

## What Was Built

### Core Package (Task 1 -- TDD)
- **GpioBackend ABC** (`backend.py`): Abstract base with `set_di`, `get_di`, `get_do`, `set_di_multi`, `close` + `detect_backend()` factory
- **RtdbBackend** (`rtdb_backend.py`): POSIX shared memory backend mapping `EmsGpio` ctypes struct via seqlock concurrency. Creates shm if not found (standalone mode). Uses `resource_tracker.unregister()` on attach to prevent subprocess cleanup of shared segments.
- **GpioConfig** (`config.py`): Loads gpio_config.yaml, resolves pin names (ESTOP_NO -> 6, DI-6 -> 6) case-insensitively, applies active_low polarity inversion.
- **12 unit tests** covering all DI/DO pins, multi-pin atomic writes, seqlock sequence verification, shm lifecycle, config resolution, backend autodetect.

### CLI + API + gpio-sim (Task 2)
- **GpioSimBackend** (`gpio_sim_backend.py`): Linux gpio-sim configfs lifecycle (create/teardown virtual GPIO chip with 16 lines). Structurally complete, testable when kernel supports gpio-sim module.
- **CLI** (`__main__.py`): `set`, `get`, `daemon` subcommands with `--backend`, `--logical`, `--raw`, `--config`, `--shm-name` flags. Multi-pin atomic `PIN=VALUE` syntax.
- **Public API** (`__init__.py`): `set_di()`, `get_di()`, `get_do()`, `set_di_multi()` convenience functions with auto open/close lifecycle.
- **Makefile**: `sim-gpio` target following sim-can/sim-modbus pattern.
- **5 CLI integration tests** via subprocess round-trip.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 (RED) | 29a5b48 | Failing tests for GPIO harness |
| 1 (GREEN) | 7e02c88 | Core: RTDB backend, config, ABC |
| 2 | 81eeb7a | gpio-sim backend, CLI, public API, Makefile |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Resource tracker unlinking shm in subprocess CLI**
- **Found during:** Task 2
- **Issue:** Python 3.12 `SharedMemory(create=False)` registers with resource_tracker, causing subprocess CLI to unlink shm on exit. Broke CLI set/get round-trip tests.
- **Fix:** Added `resource_tracker.unregister(f"/{shm_name}", "shared_memory")` after attach. Plan specified `track=False` kwarg but that's Python 3.13+.
- **Files modified:** `tools/simulators/gpio_harness/rtdb_backend.py`
- **Commit:** 81eeb7a

**2. [Rule 3 - Blocking] Skipped pyproject.toml workspace member**
- **Found during:** Task 1
- **Issue:** Plan specified creating gpio_harness as a uv workspace member with pyproject.toml, but existing simulators (can_sim, modbus_sim) are NOT workspace members -- they're plain packages under `tools/simulators/` run as `python -m tools.simulators.*`. Adding a workspace member would break the established pattern.
- **Fix:** Followed existing convention. No pyproject.toml for the harness; it runs as a regular module like the other simulators.
- **Files affected:** None (skipped creating `tools/simulators/gpio_harness/pyproject.toml`)

## Test Results

```
112 passed, 1 skipped in 9.53s
```

- 21 new tests for GPIO harness (12 unit + 4 config + 5 CLI integration)
- 1 skipped: Modbus RTU test requiring socat
- 0 regressions

## Verification Checklist

- [x] All RTDB-mode tests pass
- [x] Full test suite has no regressions (112 passed)
- [x] CLI help works
- [x] CLI set/get round-trip works
- [x] Package importable: `from tools.simulators.gpio_harness import set_di, get_do`
- [x] Makefile target exists: `make -n sim-gpio`
