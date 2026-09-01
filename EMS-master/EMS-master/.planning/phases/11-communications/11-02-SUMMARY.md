---
phase: 11-communications
plan: 02
subsystem: comm_manager
tags: [modbus, health, register-map, events, zmq]
dependency_graph:
  requires: [ems_common.ipc, pcs_register_map.yaml]
  provides: [DeviceHealth, RegisterDef, load_register_map, scale_value, publish_comm_fault, publish_comm_recovery, publish_comm_exception]
  affects: [comm_manager orchestrator (Plan 04), device discovery (Plan 05)]
tech_stack:
  added: []
  patterns: [TDD red-green, dataclass register definitions, ZMQ PUSH non-blocking events]
key_files:
  created:
    - src/comm_manager/python/src/ems_comm_manager/health.py
    - src/comm_manager/python/src/ems_comm_manager/register_map.py
    - src/comm_manager/python/src/ems_comm_manager/events.py
    - src/comm_manager/python/tests/test_health.py
    - src/comm_manager/python/tests/test_register_map.py
    - src/comm_manager/python/tests/test_events.py
    - config/btms_register_map.yaml
    - config/meter_register_map.yaml
    - config/dg_register_map.yaml
    - config/pv_register_map.yaml
  modified: []
decisions:
  - DeviceHealth starts offline (unknown) -- first success triggers recovery event
  - Backoff doubles per offline failure (1s->2->4->8->16->30->30 cap)
  - scale_value uses 16-bit two's complement (>32767 means negative)
  - Event publisher uses zmq.NOBLOCK and silently drops on EAGAIN
  - Stub register maps use metadata.version = "synthetic-1.0"
metrics:
  duration: "3m 40s"
  completed: "2026-03-14T04:07:02Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 38
  files_created: 10
  lines_of_code: 385
---

# Phase 11 Plan 02: Python Foundation Modules Summary

DeviceHealth state machine with exponential backoff, register map YAML loader with signed/unsigned scaling, and ZMQ event publisher for comm fault/recovery/exception events -- 38 tests covering all state transitions and edge cases.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | DeviceHealth state machine (TDD) | 9e87c6e, 9cffbe6 | health.py, test_health.py |
| 2 | Register map, events, stub YAMLs (TDD) | 48f373a, be1217d | register_map.py, events.py, 4 YAML stubs |

## Implementation Details

### DeviceHealth (health.py, 104 lines)

State machine tracking online/offline per device. Key behavior:
- Devices start offline (unknown state). First `record_success()` returns recovery data.
- `record_failure()` increments consecutive failures. At threshold (default 3), sets offline and returns fault data.
- While offline, each subsequent failure doubles backoff: 1s -> 2s -> 4s -> 8s -> 16s -> 30s (capped).
- `should_poll()` uses poll_interval_ms when online, backoff_s * 1000 when offline.
- Recovery data includes `offline_duration_ms` for diagnostics.

### RegisterDef + Loader (register_map.py, 129 lines)

- `RegisterDef` dataclass: address, name, scale, unit, access, signed, default, rtdb_field, description.
- `load_register_map(path)`: Parses YAML format matching existing pcs_register_map.yaml.
- `scale_value(raw, reg)`: 16-bit two's complement for signed registers (raw > 32767 -> raw - 65536), then divides by scale.
- `build_read_ranges(registers)`: Groups contiguous addresses into (start, count) tuples for efficient Modbus reads.

### Event Publisher (events.py, 152 lines)

- `publish_comm_fault()`: severity=error, event_type=comm_fault, payload per CONTEXT.md spec.
- `publish_comm_recovery()`: severity=info, includes offline_duration_ms.
- `publish_comm_exception()`: severity=warning for codes 01-03, error for code 04 (Device Failure).
- All use `encode_event()` from `ems_common.ipc` and `zmq.NOBLOCK` sends.

### Stub Register Maps

| Device | File | Registers | RTDB Match |
|--------|------|-----------|------------|
| BTMS | btms_register_map.yaml | 4 | ems_btms_t |
| Meter | meter_register_map.yaml | 8 | ems_meter_t |
| DG | dg_register_map.yaml | 6 | No struct yet |
| PV | pv_register_map.yaml | 4 | No struct yet |

## Deviations from Plan

None -- plan executed exactly as written.

## Verification Results

- 38/38 tests pass (`uv run pytest src/comm_manager/python/tests/ -x -q`)
- All 4 stub YAML files load and validate successfully
- Event roundtrip encoding verified via in-process ZMQ PUSH/PULL

## Self-Check: PASSED

All 10 files found. All 4 commits verified.
