---
phase: 11-communications
plan: 04
subsystem: comm_manager
tags: [modbus, polling, orchestrator, pcs, rtdb, pymodbus]
dependency_graph:
  requires: [DeviceHealth, RegisterDef, load_register_map, scale_value, publish_comm_fault, publish_comm_recovery, publish_comm_exception]
  provides: [ModbusDevice, PcsDevice, CommOrchestrator, comm_manager.__main__]
  affects: [device discovery (Plan 05), control_manager PCS commands]
tech_stack:
  added: [pymodbus AsyncModbusSerialClient]
  patterns: [TDD red-green, per-port async polling, seqlock RTDB writes, priority-ordered device polling]
key_files:
  created:
    - src/comm_manager/python/src/ems_comm_manager/modbus_device.py
    - src/comm_manager/python/src/ems_comm_manager/pcs_device.py
    - src/comm_manager/python/src/ems_comm_manager/orchestrator.py
    - src/comm_manager/python/src/ems_comm_manager/__main__.py
    - src/comm_manager/python/tests/test_modbus_device.py
    - src/comm_manager/python/tests/test_orchestrator.py
  modified:
    - src/comm_manager/python/pyproject.toml
decisions:
  - ModbusDevice base class uses abstract _write_to_rtdb pattern (subclass override, not ABC)
  - Exception codes 01-03 return None from poll() (no offline transition); code 04 raises ModbusDeviceError
  - PcsDevice seqlock write uses lock.sequence increment (odd=writing, even=done)
  - CommOrchestrator uses asyncio.wait_for per device for timeout isolation
  - __main__.py gracefully degrades without RTDB (logs warning, continues without writes)
metrics:
  duration: "4m 26s"
  completed: "2026-03-14T04:15:33Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 20
  files_created: 6
  lines_of_code: 630
---

# Phase 11 Plan 04: Modbus Orchestrator Summary

Python Modbus polling engine with ModbusDevice base class, PcsDevice RTDB writer, per-port CommOrchestrator with priority ordering and 50ms loop floor, and __main__.py entry point -- 20 new tests covering poll/exception/RTDB/orchestration.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | ModbusDevice base class and PcsDevice (TDD) | ab7493d, f3b2313 | modbus_device.py, pcs_device.py, test_modbus_device.py |
| 2 | CommOrchestrator and __main__ entry point | f593c47 | orchestrator.py, __main__.py, test_orchestrator.py |

## Implementation Details

### ModbusDevice (modbus_device.py, 194 lines)

Base class for Modbus RTU device polling. Key behavior:
- `poll()` reads holding registers via pymodbus AsyncModbusSerialClient, assembles raw values from read ranges.
- Exception codes 01-03 (Illegal Function/Address/Value) publish comm_exception event and return None -- no offline transition.
- Exception code 04 (Device Failure) publishes comm_exception AND raises ModbusDeviceError to trigger on_failure.
- `on_success()` records health success, publishes recovery on offline->online transition, writes RTDB.
- `on_failure()` records health failure, publishes comm_fault on threshold crossing.

### PcsDevice (pcs_device.py, 91 lines)

PCS inverter subclass of ModbusDevice. Key behavior:
- Maps PCS register values to RTDB EmsPcs fields via `rtdb_field` attribute in register definitions.
- Writes via seqlock: increments lock.sequence to odd, sets fields, sets last_update_ms, increments to even.
- Priority 0 (highest) ensures PCS is always polled first on shared RS485 ports.
- Device type constant `DEVICE_PCS = "pcs"`.

### CommOrchestrator (orchestrator.py, 172 lines)

Per-port async polling loops with priority ordering. Key behavior:
- Groups devices by serial port, sorts by priority (lower = higher).
- Creates one pymodbus AsyncModbusSerialClient per unique port (RTU framer, reconnect_delay=0).
- Spawns one asyncio task per port via `_port_polling_loop()`.
- Each loop: check health.should_poll, wait_for(poll, timeout), on_success/on_failure.
- 50ms minimum loop period via asyncio.sleep prevents busy-spin.
- Timeout on one device does not block others (asyncio.wait_for per device).
- Graceful shutdown: cancel all tasks, close all clients.

### Entry Point (__main__.py, 173 lines)

- Loads pcs_config.yaml from config directory.
- Creates ZMQ PUSH socket to SOCK_LOGGER.
- Attaches to RTDB (graceful degradation if unavailable).
- Creates PcsDevice from config, wraps in CommOrchestrator.
- SIGTERM/SIGINT trigger orchestrator.shutdown().
- CLI: `--config CONFIG_DIR`, `--log-level DEBUG|INFO|WARNING|ERROR`.

## Deviations from Plan

None -- plan executed exactly as written.

## Verification Results

- 20/20 new tests pass (13 modbus_device + 7 orchestrator)
- 58/58 total comm_manager tests pass
- Module import verified: `from ems_comm_manager.orchestrator import CommOrchestrator`
- Entry point runs: `python -m ems_comm_manager --help` shows usage

## Self-Check: PASSED

All 6 files found. All 3 commits verified.
