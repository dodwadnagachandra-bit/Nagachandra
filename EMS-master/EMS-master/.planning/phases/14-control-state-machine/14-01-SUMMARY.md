---
phase: 14-control-state-machine
plan: "01"
subsystem: rtdb, control, comms
tags: [rtdb, pcs, modbus, ctypes, seqlock, jsonschema, yaml, control-manager, comm-manager]

requires:
  - phase: 11-can-health-bus-monitoring
    provides: "ems_can_health_t added to RTDB, C_SIZEOF_RTDB baseline of 1800808"
  - phase: 12-comm-manager-modbus
    provides: "PcsDevice, ModbusDevice, CommOrchestrator poll loop"
  - phase: 13-config-manager
    provides: "JSON Schema validation patterns, control_config.yaml and schema"

provides:
  - "ems_system_t extended with pcs_command (uint8), _pad_cmd[3], pcs_command_seq (uint32), active_derating_pct (float)"
  - "Python EmsSystem ctypes mirror updated to match (sizeof 48->56, ems_rtdb_t 1800808->1800816)"
  - "PcsDevice.write_setpoint(): reads RTDB active_setpoint_kw and writes register 0x500E"
  - "PcsDevice.process_command(): seq-deduped command dispatch to PCS ON/OFF/FAULT_RESET registers"
  - "CommOrchestrator poll loop extended to call write methods after each PCS poll cycle"
  - "load_control_config(): YAML loader with JSON Schema validation for control_manager"

affects:
  - 14-02-control-state-machine
  - 14-03-control-loop-rtdb-integration
  - 16-derating-interlocks
  - any consumer of ems_system_t / EmsSystem sizeof

tech-stack:
  added: []
  patterns:
    - "seqlock_read_section duplicated into pcs_device.py — future refactor: move to ems_common.rtdb"
    - "PCS command path: control_manager writes pcs_command + increments pcs_command_seq, comm_manager acts on seq change"
    - "Sequence deduplication: (new_seq - last_seq) & 0xFFFFFFFF == 0 guards against re-execution"
    - "load_control_config() uses Draft202012Validator.iter_errors() sorted by path for deterministic first-error reporting"

key-files:
  created:
    - src/control_manager/python/src/ems_control_manager/config.py
    - src/comm_manager/python/tests/test_pcs_device.py
    - src/control_manager/python/tests/test_config.py
  modified:
    - src/common/c/include/rtdb.h
    - src/common/python/src/ems_common/rtdb.py
    - tests/test_rtdb.py
    - src/data_manager/python/src/ems_data_manager/publisher.py
    - src/comm_manager/python/src/ems_comm_manager/pcs_device.py
    - src/comm_manager/python/src/ems_comm_manager/orchestrator.py

key-decisions:
  - "sizeof(ems_rtdb_t) is now 1800816 (was 1800808) — old EmsSystem had 4 bytes implicit tail-pad consumed by new fields, net addition is 8 not 12 bytes"
  - "PCS write_register() uses device_id= keyword (pymodbus v3 API), not slave="
  - "_seqlock_read_section duplicated in pcs_device.py rather than moved to ems_common to avoid premature abstraction; deferred refactor noted in patterns"
  - "CommOrchestrator calls write_setpoint + process_command after every poll cycle (not just after successful poll) — keeps command path live even if telemetry poll fails"
  - "load_control_config() raises ValueError (not jsonschema.ValidationError) for consistent error handling interface"

patterns-established:
  - "PCS command path: RTDB seqlock field (pcs_command_seq) as change notification — no ZMQ required for control->comms dispatch"
  - "Config loader pattern: load YAML -> validate with Draft202012Validator.iter_errors() -> raise ValueError with first error path"

requirements-completed: [CTRL-01, CTRL-03]

duration: 7min
completed: "2026-03-14"
---

# Phase 14 Plan 01: RTDB Command Path Foundation Summary

**PCS command path data layer: ems_system_t extended with seqlock-safe command fields, PcsDevice gains write_setpoint/process_command, and control_manager gets a validated config loader**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-14T19:19:25Z
- **Completed:** 2026-03-14T19:26:30Z
- **Tasks:** 2
- **Files modified:** 9 (3 created, 6 modified)

## Accomplishments

- Extended `ems_system_t` (C) and `EmsSystem` (Python ctypes) with three new fields for the M2 PCS command path: `pcs_command`, `pcs_command_seq`, and `active_derating_pct` — struct grows from 48 to 56 bytes, RTDB from 1800808 to 1800816 bytes, all existing tests pass
- Added `PcsDevice.write_setpoint()` and `PcsDevice.process_command()` to comm_manager, wired into the `CommOrchestrator` poll loop — control_manager can now route power setpoints and ON/OFF/FAULT_RESET commands to the PCS hardware via RTDB without direct Modbus access
- Created `load_control_config()` in control_manager with JSON Schema validation against the existing control_config.schema.json — the state machine (Plan 02) can load and validate its config at startup

## Task Commits

Each task was committed atomically:

1. **Task 1: RTDB struct update (C + Python + tests)** - `d4544d8` (feat)
2. **Task 2: comm_manager PcsDevice write methods + control config loader** - `ab9d3a4` (feat)

## Files Created/Modified

- `src/common/c/include/rtdb.h` - Added PCS_CMD_* constants and 3 new fields to ems_system_t
- `src/common/python/src/ems_common/rtdb.py` - Mirrored new fields in EmsSystem ctypes struct
- `tests/test_rtdb.py` - Updated C_SIZEOF_RTDB constant from 1800808 to 1800816
- `src/data_manager/python/src/ems_data_manager/publisher.py` - Added pcs_command, pcs_command_seq, active_derating_pct to _system_to_dict()
- `src/comm_manager/python/src/ems_comm_manager/pcs_device.py` - Added write_setpoint(), process_command(), _seqlock_read_section() helper, PCS_CMD_* constants
- `src/comm_manager/python/src/ems_comm_manager/orchestrator.py` - Extended _port_polling_loop to call write methods after poll cycle for PCS devices
- `src/control_manager/python/src/ems_control_manager/config.py` - Created: load_control_config() with JSON Schema validation
- `src/comm_manager/python/tests/test_pcs_device.py` - Created: 14 tests for write_setpoint and process_command
- `src/control_manager/python/tests/test_config.py` - Created: 8 tests for load_control_config

## Decisions Made

- `sizeof(ems_rtdb_t)` is now 1800816 (not 1800820 as the plan estimated): the old `EmsSystem` had 4 bytes of implicit tail-padding (ctypes aligns to largest member = uint64, so 44-byte logical size rounded to 48). New fields consumed that pad, net addition is 8 bytes.
- PCS `write_register()` uses `device_id=` keyword argument (pymodbus v3 API), not `slave=`.
- `_seqlock_read_section` duplicated in `pcs_device.py` rather than moved to `ems_common` — avoids premature abstraction; noted as future refactor.
- `CommOrchestrator` calls write methods after every poll loop iteration (not conditional on successful poll) — keeps command dispatch live even when telemetry reads are failing.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] pymodbus write_register uses device_id= not slave=**
- **Found during:** Task 2 (PcsDevice write methods)
- **Issue:** Plan specified `await client.write_register(0x500E, raw, slave=self._slave_id)` but pymodbus v3 API uses `device_id=` keyword; `slave=` raises TypeError
- **Fix:** Changed all `slave=` to `device_id=` in pcs_device.py and test_pcs_device.py
- **Files modified:** pcs_device.py, test_pcs_device.py
- **Verification:** All 14 PcsDevice tests pass
- **Committed in:** ab9d3a4 (Task 2 commit)

**2. [Rule 1 - Bug] Plan used self._slave_id but attribute is self.slave_id**
- **Found during:** Task 2 (PcsDevice write methods)
- **Issue:** Plan snippet used `self._slave_id` but ModbusDevice stores it as public `self.slave_id`; write calls silently fell through to the warning path
- **Fix:** Changed all `self._slave_id` to `self.slave_id` in write methods
- **Files modified:** pcs_device.py
- **Verification:** Tests caught the bug; all pass after fix
- **Committed in:** ab9d3a4 (Task 2 commit)

**3. [Rule 1 - Bug] sizeof(ems_rtdb_t) plan estimate was 1800820, actual is 1800816**
- **Found during:** Task 1 (RTDB struct update)
- **Issue:** Plan estimated +12 bytes but old EmsSystem had 4 bytes implicit tail-pad; actual net increase is 8 bytes
- **Fix:** Set C_SIZEOF_RTDB to 1800816 after verifying with C test binary output
- **Files modified:** tests/test_rtdb.py
- **Verification:** C binary reports 1800816; Python ctypes reports 1800816; test passes
- **Committed in:** d4544d8 (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (3 Rule 1 bugs — all plan-spec vs. actual-API mismatches)
**Impact on plan:** No scope change. All fixes correct the plan's incorrect API assumptions.

## Issues Encountered

- Rebuilding `data_manager_c` was required after the struct size change to fix integration tests that create live shared memory — the stale C binary was creating a 1800808-byte shm segment but Python now expects 1800816 bytes. Rebuild resolved immediately.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- RTDB data layer is complete for the M2 command path
- `PcsDevice.write_setpoint()` and `process_command()` are wired and tested — ready for control_manager to write setpoints and commands
- `load_control_config()` is ready for use in the state machine (Plan 02)
- Plan 02 (control state machine logic) can proceed immediately

---
*Phase: 14-control-state-machine*
*Completed: 2026-03-14*

## Self-Check: PASSED

All 9 output files exist. Both task commits (d4544d8, ab9d3a4) confirmed in git log. Tests: 8 RTDB + 14 PcsDevice + 8 config = 30 new/updated tests all pass.
