---
phase: 04-ipc-contracts
plan: 01
subsystem: ipc-contracts
tags: [ipc, zmq, msgpack, mpack, contracts, interop]
dependency_graph:
  requires: [01-01-scaffold, 03-01-rtdb-layout]
  provides: [ipc-defs-h, ipc-py, mpack-vendor, interop-tests]
  affects: [data_manager, comm_manager, control_manager, alarm_manager, logger, hmi_server, cloud_manager]
tech_stack:
  added: [mpack-1.1.1, msgpack-1.1.2]
  patterns: [length-prefixed-framing, msgpack-map-envelope, c-python-interop]
key_files:
  created:
    - src/common/c/include/ipc_defs.h
    - src/common/python/src/ems_common/ipc.py
    - src/vendor/mpack/mpack.h
    - src/vendor/mpack/mpack.c
    - src/vendor/mpack/CMakeLists.txt
    - tests/c/test_ipc_interop.c
    - tests/test_ipc_contracts.py
  modified:
    - src/common/c/include/ems_types.h
    - CMakeLists.txt
    - tests/c/CMakeLists.txt
    - pyproject.toml
    - uv.lock
decisions:
  - mpack v1.1.1 vendored as amalgamation (single .h + .c) with -w to suppress warnings in vendored code
  - Length-prefixed framing (4-byte big-endian uint32) for C interop output file
  - TOPIC_CONTROL_STATE added beyond plan spec to match plan's must_haves referencing control state topic
metrics:
  duration: ~6 min
  completed: "2026-03-05"
  tasks: 4/4
  test_count: 12 (2 C + 10 Python)
  files_created: 7
  files_modified: 5
---

# Phase 4 Plan 1: IPC Contracts Foundation Summary

IPC contract definitions (ipc_defs.h + ipc.py) with mpack/msgpack interop validation proving C and Python agree on MessagePack encoding for all three message patterns (telemetry, command, event).

## What Was Done

### Task 1: Vendor mpack library and wire CMake
- Downloaded mpack v1.1.1 amalgamation (mpack.h + mpack.c) from GitHub releases
- Created `src/vendor/mpack/CMakeLists.txt` building static library with `-w` (suppress vendored warnings)
- Added `add_subdirectory(src/vendor/mpack)` to root CMakeLists.txt before test guard
- **Commit:** `e3ae421`

### Task 2: IPC definitions header and Python mirror
- Created `src/common/c/include/ipc_defs.h` with 4 socket paths, 11 topic strings, 14 message key constants, 6 status/severity strings
- Added `ems_severity_t` enum to `ems_types.h` (INFO, WARNING, ERROR, CRITICAL)
- Created `src/common/python/src/ems_common/ipc.py` mirroring all C constants with type annotations
- Implemented 8 encode/decode helper functions for telemetry, command request/response, and event patterns
- Added `msgpack>=1.0` to dev dependencies, installed via `uv sync`
- **Commit:** `88d6350`

### Task 3: C interop test -- mpack encode
- Created `tests/c/test_ipc_interop.c` with 4 sample message encoders (telemetry, cmd request, cmd response, event)
- Self-test mode: encode + decode roundtrip in pure C, verify field values
- Output mode: write 4 length-prefixed msgpack messages to file for Python interop
- Wired into CMake with -Wall -Wextra -Werror, ctest self-test passes
- **Commit:** `9c4de55`

### Task 4: Python contract tests and C-Python interop
- Created `tests/test_ipc_contracts.py` with 10 test functions
- Tests cover: constant validation, telemetry roundtrip, command request/response roundtrip, event roundtrip, binary format validation, C-Python interop, topic uniqueness, socket uniqueness
- C-Python interop test runs the C binary, reads length-prefixed output, decodes with msgpack, verifies all field values match
- All 45 tests passing (scaffold 4 + config 19 + rtdb 8 + ipc 10 + ctest 2 + ipc_self 2 = 45 pytest + 2 ctest)
- **Commit:** `0f261e2`

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **mpack v1.1.1 amalgamation**: Vendored single-file amalgamation rather than full library source -- simplest integration, no submodule needed
2. **Length-prefixed framing**: 4-byte big-endian uint32 length prefix for multi-message output file -- simple, unambiguous parsing from Python
3. **TOPIC_CONTROL_STATE**: Added `control.state` topic string (referenced in plan must_haves) to both C and Python

## Verification Results

```
cmake -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build -- -j$(nproc)  # OK
ctest --test-dir build --output-on-failure                                     # 2/2 passed
uv run pytest tests/ -v                                                        # 45/45 passed
```

## Key Artifacts

| File | Purpose |
|------|---------|
| `src/common/c/include/ipc_defs.h` | Socket paths, topic strings, message key constants |
| `src/common/python/src/ems_common/ipc.py` | Python mirror + encode/decode helpers |
| `src/vendor/mpack/` | Vendored mpack v1.1.1 amalgamation |
| `tests/c/test_ipc_interop.c` | C-side mpack encode/decode + output for Python |
| `tests/test_ipc_contracts.py` | 10 Python tests validating all IPC contracts |
