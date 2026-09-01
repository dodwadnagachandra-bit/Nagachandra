---
phase: 11-communications
plan: "01"
subsystem: comm_manager
tags: [can, dbc, decode, rtdb, health]
dependency_graph:
  requires: []
  provides: [can_decode, can_health_rtdb]
  affects: [comm_manager_c, data_manager]
tech_stack:
  added: [linux/can.h, linux/can/error.h]
  patterns: [manual-dbc-decode, le-byte-extraction, can-id-decomposition]
key_files:
  created:
    - src/comm_manager/c/src/can_decode.h
    - src/comm_manager/c/src/can_decode.c
    - src/comm_manager/c/tests/test_can_decode.c
    - src/comm_manager/c/tests/CMakeLists.txt
  modified:
    - src/common/c/include/rtdb.h
    - src/common/python/src/ems_common/rtdb.py
    - src/comm_manager/c/CMakeLists.txt
decisions:
  - "Cell voltage indexing uses flat array in module[0] (slot * 4 + offset)"
  - "RackStatus decodes online + min/max/avg cell_v per DBC (no temp fields in DBC RackStatus message)"
  - "Error frame handler uses linux/can/error.h constants directly"
metrics:
  duration: "5m 2s"
  completed: "2026-03-14"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 27
  tests_passed: 27
---

# Phase 11 Plan 01: CAN DBC Decode and RTDB Health Extension Summary

RTDB struct extended with per-CAN-interface health tracking (ems_can_health_t), manual DBC decode for all 10 BMS message types using little-endian byte extraction, CAN error frame parser for bus-off/error-passive/warning states with TEC/REC extraction.

## Task Completion

| Task | Name | Commit | Status |
|------|------|--------|--------|
| 1 | Add CAN health struct to RTDB + Python ctypes | 23e50f9 | Done |
| 2 | CAN DBC decode functions + unit tests (TDD) | dd2d88c, dd304b5 | Done |

## What Was Built

### Task 1: RTDB CAN Health Extension

- Added `ems_can_bus_state_t` enum (ACTIVE, ERROR_WARNING, ERROR_PASSIVE, BUS_OFF)
- Added `ems_can_health_t` struct (32 bytes: seqlock, last_update_ms, bus_state, tx/rx_error_count, last_error_frame_ms)
- Added `MAX_CAN_INTERFACES=2` and `can_health[MAX_CAN_INTERFACES]` to `ems_rtdb_t` (appended at end, preserving all existing offsets)
- Added matching `EmsCanHealth` ctypes class to `rtdb.py`
- C and Python sizeof verified matching: ems_rtdb_t = 1,800,808 bytes (was 1,800,744, +64 bytes for 2 health structs)

### Task 2: CAN DBC Decode Functions (TDD)

**RED phase:** 27 failing tests covering all decode functions, CAN ID decomposition, and error frame handling.

**GREEN phase:** Full implementation of all decoders:

- `can_decode_id()` -- strips EFF flag (0x80000000) using CAN_29BIT_MASK, decomposes to cluster/rack/msg_offset using stride formula
- `can_decode_pack_summary()` -- pack_v (u16*0.1), pack_i (s16*0.1), pack_soc (u8*0.5), pack_soh (u8*0.5), fault_code (u16)
- `can_decode_cell_voltage()` -- 4 cells per slot (u16*0.001V), slots 0-6 for CellVoltage_01-07
- `can_decode_cell_temperature()` -- 8 temps (u8 - 40 offset)
- `can_decode_rack_status()` -- online flag, min/max/avg cell_v (u16*0.001)
- `can_decode_frame()` -- dispatcher routing msg_offset 0-9 to appropriate decoder
- `can_handle_error_frame()` -- CAN_ERR_BUSOFF, CAN_ERR_CRTL with TX/RX_PASSIVE/WARNING, TEC/REC from data[6:7]

## Verification

- 27/27 unit tests pass
- 0 compiler warnings with -Wall -Wextra -Werror
- data_manager_c builds successfully with updated RTDB
- Python ctypes import of EmsCanHealth succeeds
- ems_can_health_t _Static_assert confirms 32-byte size

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] RackStatus temperature fields not in DBC**
- **Found during:** Task 2 implementation
- **Issue:** Plan specified decode_rack_status should map min/max/avg cell_t, but the actual DBC RackStatus message (0x09) only contains online, balancing_count, min/max/avg cell_v (6 bytes). No temperature fields exist in RackStatus.
- **Fix:** Implemented RackStatus decode per actual DBC spec (online + cell voltage stats). Temperature min/max/avg can be computed from CellTemperature message data by the CAN reader process.
- **Files modified:** src/comm_manager/c/src/can_decode.c, test_can_decode.c

**2. [Rule 1 - Bug] CAN ID test values in plan behavior spec inconsistent with base_id**
- **Found during:** Task 2 test writing
- **Issue:** Plan behavior spec says "CAN ID 0x98FF0006 -> msg_offset=6" but with base_id 0x18FF0003, the offset would be 3, not 6. For msg_offset=6, the correct CAN ID is 0x98FF0009.
- **Fix:** Tests use mathematically correct CAN IDs based on the actual base_id formula.
- **Files modified:** src/comm_manager/c/tests/test_can_decode.c

## Self-Check: PASSED

All 7 created/modified files verified on disk. All 3 commits (23e50f9, dd2d88c, dd304b5) verified in git log.
