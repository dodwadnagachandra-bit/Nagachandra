---
phase: 09-foundation
plan: 02
subsystem: data_manager
tags: [rtdb, shared-memory, lifecycle, c, python, ipc]
dependency_graph:
  requires: [09-CONTEXT]
  provides: [libems_rtdb.so, data_manager_c, python-rtdb-helpers]
  affects: [safety_manager, comm_manager, control_manager, logger]
tech_stack:
  added: [libems_rtdb.so]
  patterns: [shm_open/mmap lifecycle, stale detection, resource_tracker.unregister]
key_files:
  created:
    - src/data_manager/c/include/rtdb_lifecycle.h
    - src/data_manager/c/src/rtdb_lifecycle.c
    - tests/c/test_rtdb_lifecycle.c
    - tests/test_data_manager.py
  modified:
    - src/data_manager/c/src/main.c
    - src/data_manager/c/CMakeLists.txt
    - src/common/python/src/ems_common/rtdb.py
    - tests/c/CMakeLists.txt
decisions:
  - "rtdb_create always removes existing shm (stale or valid) before creating fresh — ensures topology consistency on restart"
  - "Python attach_rtdb uses resource_tracker.unregister to prevent premature shm unlink in Python 3.12"
  - "data_manager_c takes topology as CLI args (systemd ExecStart passthrough) rather than parsing YAML directly"
metrics:
  duration: "8m 26s"
  completed: "2026-03-13"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 20
  tests_passed: 20
requirements: [DATA-01, DATA-02, DATA-03, DATA-04]
---

# Phase 9 Plan 2: RTDB Shared Memory Lifecycle Summary

RTDB lifecycle library (libems_rtdb.so) with create/attach/detach/destroy API, stale shm detection, and Python attach helpers with resource_tracker fix for Python 3.12.

## What Was Built

### C Library: libems_rtdb.so

- **rtdb_create()**: shm_open + ftruncate + mmap, zero-fill, write magic/version/topology, initialize all seqlocks
- **rtdb_attach()**: Open existing shm, verify magic+version, return mapped pointer
- **rtdb_detach()**: munmap only (no unlink)
- **rtdb_destroy()**: shm_unlink to remove segment
- **Stale detection**: On create, checks for existing shm with wrong magic/version and removes it before creating fresh

### data_manager_c Executable

- Parses topology from CLI args: `data_manager_c <clusters> <racks> <modules> <cells> <temps>`
- Creates RTDB, blocks on pause() until SIGTERM/SIGINT
- On shutdown: detach + destroy (clean unlink)

### Python Helpers (ems_common.rtdb)

- **attach_rtdb()**: Opens SharedMemory, calls resource_tracker.unregister to prevent premature cleanup, returns (shm, EmsRtdb ctypes view)
- **detach_rtdb()**: Closes shm handle without unlinking
- **validate_topology()**: Verifies RTDB header counts match expected config dict

## Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| C lifecycle (test_rtdb_lifecycle) | 10 | All pass |
| Python integration (test_data_manager.py) | 10 | All pass |
| Existing RTDB layout (test_rtdb.py) | 8 | All pass (no regressions) |
| **Total** | **28** | **All pass** |

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 8773028 | test | TDD RED: C lifecycle tests (fail — no library yet) |
| 9cf62bf | feat | TDD GREEN: libems_rtdb.so + data_manager_c implementation |
| c0a1051 | test | TDD RED: Python integration tests (fail — no helpers yet) |
| 41b1e73 | feat | TDD GREEN: Python attach/detach/validate helpers |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Use-after-free in stale detection logging**
- **Found during:** Task 1 GREEN phase
- **Issue:** rtdb_lifecycle.c printed `existing->magic` after `munmap()` — segfault
- **Fix:** Save magic/version to local variables before munmap
- **Files modified:** src/data_manager/c/src/rtdb_lifecycle.c
- **Commit:** 9cf62bf

**2. [Rule 1 - Bug] BufferError in Python tests from ctypes/shm ordering**
- **Found during:** Task 2 GREEN phase
- **Issue:** `shm.close()` while `EmsRtdb.from_buffer(shm.buf)` still holds reference
- **Fix:** Add `del rtdb` before `detach_rtdb(shm)` in all tests
- **Files modified:** tests/test_data_manager.py
- **Commit:** 41b1e73

### Out-of-Scope Issues

- `test_config_manager.py::TestCLIValidate::test_cli_validate_valid_file_exit_0` fails (missing `ems_config_manager.cli` module) — pre-existing, not caused by this plan

## Self-Check: PASSED
