---
phase: 11-communications
plan: 05
subsystem: comm_manager
tags: [modbus, generic-device, discovery, btms, meter, dg, pv, systemd, rtdb]
dependency_graph:
  requires: [ModbusDevice, PcsDevice, CommOrchestrator, DeviceHealth, RegisterDef, scale_value]
  provides: [GenericDevice, startup_discovery, comm_manager.service, comm_manager_c.service]
  affects: [control_manager device status, logger integration phase]
tech_stack:
  added: []
  patterns: [TDD red-green, config-driven device instantiation, per-port sequential discovery, cross-port parallel discovery, seqlock RTDB writes]
key_files:
  created:
    - src/comm_manager/python/src/ems_comm_manager/generic_device.py
    - src/comm_manager/python/src/ems_comm_manager/discovery.py
    - deploy/systemd/comm_manager_c.service
    - src/comm_manager/python/tests/test_generic_device.py
    - src/comm_manager/python/tests/test_discovery.py
  modified:
    - src/comm_manager/python/src/ems_comm_manager/orchestrator.py
    - src/comm_manager/python/src/ems_comm_manager/__main__.py
    - deploy/systemd/comm_manager.service
decisions:
  - GenericDevice uses register name as RTDB field name (register maps match ctypes struct fields)
  - ctypes integer fields handled via try/except TypeError fallback to int cast
  - DG and PV skip RTDB writes (log-only) until RTDB extended in future phase
  - Mandatory devices (PCS, BMS) log ERROR on unreachable; optional devices log WARNING
  - Discovery uses asyncio.wait_for with overall timeout for graceful timeout handling
  - comm_manager and comm_manager_c are fully independent systemd services
  - comm_manager_c RestartSec=1 (faster than comm_manager RestartSec=3) because CAN data goes stale quickly
  - COMM-08 (GPIO monitoring) verified as handled by safety_manager (SAFE-08)
metrics:
  duration: "6m 28s"
  completed: "2026-03-14T04:26:07Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 16
  files_created: 5
  lines_of_code: 520
---

# Phase 11 Plan 05: Generic Devices, Discovery & Systemd Summary

GenericDevice for BTMS/Meter/DG/PV with configurable RTDB section mapping, startup discovery with per-port sequential and cross-port parallel probing, and two independent systemd service files for comm_manager (Python/Modbus) and comm_manager_c (C/CAN).

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | GenericDevice and startup discovery (TDD) | 797c386, d8e90ce | generic_device.py, discovery.py, test_generic_device.py, test_discovery.py |
| 2 | Systemd service files | 9b4cd0e | comm_manager.service, comm_manager_c.service |

## Implementation Details

### GenericDevice (generic_device.py, 112 lines)

Modbus device subclass for BTMS, Meter, DG, and PV subsystems. Key behavior:
- Takes `rtdb` and `rtdb_section_name` parameters to target the correct RTDB struct section.
- `_write_to_rtdb()` gets section via `getattr(rtdb, rtdb_section_name)`, writes via seqlock.
- Uses register `name` as RTDB field name (register maps designed with matching names).
- Handles ctypes integer fields (e.g., `c_uint8 cooling_active`) with try/except fallback to int.
- Returns silently when rtdb is None or section doesn't exist (DG/PV log-only mode).
- Priority constants: PRIORITY_METER=1, PRIORITY_BTMS=2, PRIORITY_DG=3, PRIORITY_PV=4.

### Discovery (discovery.py, 103 lines)

Startup device probing before entering polling loops. Key behavior:
- Groups devices by port, probes sequentially within each port (RS485 bus constraint).
- Probes all ports concurrently via asyncio.gather (cross-port parallelism).
- Uses min(device.timeout_s, 3.0) as probe timeout for fast failure detection.
- MANDATORY_DEVICES = {"pcs", "bms"} -- log ERROR on unreachable.
- Optional devices (btms, meter, dg, pv) log WARNING on unreachable.
- Overall timeout prevents discovery from blocking startup indefinitely.
- Never raises -- always returns dict[device_id, bool].

### Updated Orchestrator (orchestrator.py, +50 lines)

- Extracted `_create_clients()` for reuse between run() and run_with_discovery().
- `run_with_discovery()` calls startup_discovery before entering polling loops.
- Logs discovery summary: "Discovery complete: N/M devices reachable".

### Updated Entry Point (__main__.py, 234 lines)

- Loads all device configs: PCS, BTMS, Meter, DG, PV from config directory.
- Creates GenericDevice instances with correct RTDB section mappings.
- Config-driven: device config files are optional (missing config = skip device).
- Register map auto-discovery: config/{device_type}_register_map.yaml.
- COMM-08 verification log at startup: GPIO monitoring handled by safety_manager.
- Calls run_with_discovery() instead of run().

### Systemd Service Files

**comm_manager.service**: Python Modbus RTU polling.
- Type=exec, Restart=always, RestartSec=3
- After=ems-safety-manager.service ems-data-manager.service
- SupplementaryGroups=dialout, SyslogIdentifier=ems-comm-manager

**comm_manager_c.service**: C CAN BMS decode.
- Type=exec, Restart=always, RestartSec=1 (faster for CAN)
- AmbientCapabilities=CAP_NET_RAW for CAN socket
- Both services fully independent (no cross-dependency).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ctypes integer field assignment TypeError**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** `setattr(section, "cooling_active", 1.0)` raises TypeError because EmsBtms.cooling_active is c_uint8.
- **Fix:** Added try/except TypeError fallback that casts to int for ctypes integer fields.
- **Files modified:** generic_device.py
- **Commit:** d8e90ce

## Verification Results

- 16/16 new tests pass (10 generic_device + 6 discovery)
- 74/74 total comm_manager unit tests pass (11 integration tests skipped/pre-existing)
- Module import verified: GenericDevice and startup_discovery load correctly
- Service files validated by systemd-analyze verify

## Self-Check: PASSED
