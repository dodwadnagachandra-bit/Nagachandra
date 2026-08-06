---
phase: 14-control-state-machine
plan: "02"
subsystem: control
tags: [state-machine, python, control-manager, pcs, fault-handling, tdd]

# Dependency graph
requires:
  - phase: 14-control-state-machine
    provides: "ems_types.h C enum values and control_config.yaml state_machine section"
provides:
  - "ControlStateMachine class with all 8 states matching ems_control_state_t"
  - "TickResult dataclass with state, setpoint_kw, pcs_command outputs"
  - "PCS ON/OFF non-blocking sequencing via STARTING/STOPPING sub-states"
  - "Fault auto-retry (configurable) and manual reset"
  - "MAINTENANCE persistence on restart via RTDB state check"
  - "47 TDD tests covering all transitions and edge cases"
affects:
  - "14-control-state-machine/plan-03 (ControlLoop uses ControlStateMachine)"
  - "alarm_manager (protection actions dispatch to ControlStateMachine)"
  - "hmi_server (state values must match ems_control_state_t int values)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-logic state machine class — no I/O, no RTDB access, testable without hardware"
    - "TickResult dataclass as tick output contract between state machine and caller"
    - "Sub-state enum for non-blocking PCS ON/OFF sequencing (no sleep, no threads)"
    - "Priority-ordered tick: EMERGENCY > PCS_FAULT > sub-state_timers > pending_commands > steady_state"

key-files:
  created:
    - "src/control_manager/python/src/ems_control_manager/state_machine.py"
    - "src/control_manager/python/tests/test_state_machine.py"
    - "src/control_manager/python/tests/__init__.py"
  modified: []

key-decisions:
  - "State constants are plain int (not Python Enum) to match ems_control_state_t C values without serialization overhead"
  - "_SubState uses Python Enum since it is internal only and never crosses language boundary"
  - "STARTING sub-state: _state stays STATE_IDLE until PCS confirms PCS_RUNNING, then transitions to STATE_STANDBY"
  - "CHARGING->STANDBY: zero setpoint only, no PCS OFF (PCS remains running for fast re-dispatch)"
  - "STANDBY->IDLE: 2s ramp hold then send PCS_CMD_OFF, wait for PCS_STATE_OFF confirmation"
  - "Fault auto-retry fires every pcs_startup_timeout_s seconds, up to fault_retry_count times"
  - "request_fault_reset() accepted even during auto-retry countdown (operator override)"

patterns-established:
  - "State machine tick() returns TickResult — caller owns RTDB writes and pcs_command_seq increment"
  - "request_*() methods called before tick() to inject commands; tick() processes them in priority order"
  - "All command rejections return (False, error_msg) tuple — never raise exceptions"

requirements-completed:
  - CTRL-02
  - CTRL-07

# Metrics
duration: 5min
completed: 2026-03-14
---

# Phase 14 Plan 02: Control State Machine Summary

**8-state ControlStateMachine with non-blocking PCS ON/OFF sequencing, configurable fault auto-retry, and full TDD coverage (47 tests)**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-14T19:19:17Z
- **Completed:** 2026-03-14T19:24:17Z
- **Tasks:** 3 (RED/GREEN/REFACTOR)
- **Files modified:** 3

## Accomplishments

- ControlStateMachine implementing all 8 states with int values matching ems_control_state_t C enum exactly
- Non-blocking STARTING/STOPPING sub-states for PCS ON/OFF sequencing using monotonic timestamps — no sleep, no threads
- Fault handling with configurable auto-retry (default 3) and operator-cancellable countdown via request_fault_reset()
- MAINTENANCE persistence: first tick reads current_rtdb_state to survive process restart without losing maintenance lock
- CHARGING<->DISCHARGING direct transition rejected — must route via STANDBY (prevents unsafe setpoint direction flip)
- 47 tests across 14 test classes, all passing, covering every transition and edge case from the plan spec

## Task Commits

Each task was committed atomically:

1. **RED: Failing tests for ControlStateMachine** - `7c00847` (test)
2. **GREEN: ControlStateMachine implementation** - `0043e0e` (feat)
3. **REFACTOR: Clean up stopping substate and variable naming** - `e784039` (refactor)

_Note: TDD plan — 3 commits following RED-GREEN-REFACTOR cycle_

## Files Created/Modified

- `/home/overlord/EMS/src/control_manager/python/src/ems_control_manager/state_machine.py` - ControlStateMachine class, TickResult dataclass, all state/PCS/command constants
- `/home/overlord/EMS/src/control_manager/python/tests/test_state_machine.py` - 47 tests across 14 test classes covering all 21 transition scenarios
- `/home/overlord/EMS/src/control_manager/python/tests/__init__.py` - Test package init

## Decisions Made

- **STARTING sub-state holds _state at STATE_IDLE** until PCS confirms STATE_RUNNING, then transitions to STATE_STANDBY. This makes `state_changed=True` visible to callers on the confirmation tick, giving a clean event signal.
- **CHARGING->STANDBY requires no PCS OFF** because PCS stays running and just receives a zero setpoint — preserves fast redispatch capability.
- **Fault recovery detection** uses `pcs_fault_code == 0` as the clearing signal rather than requiring an explicit PCS state — more robust to different PCS firmware versions.

## Deviations from Plan

None — plan executed exactly as written.

The one GREEN-phase iteration (47 pass on first full run except 1 failing `state_changed` assertion) was a TDD implementation artifact: the `STARTING->STANDBY` sub-state transition needed to hold `_state` at `STATE_IDLE` while sequencing rather than pre-setting it to `STATE_STANDBY`. Fixed within the GREEN phase, no extra commits.

## Issues Encountered

None significant. One test assertion caught an implementation subtlety in STARTING sub-state: the state must stay `STATE_IDLE` during PCS startup so that `state_changed=True` is correctly reported when PCS confirms RUNNING. Resolved by moving the `STATE_STANDBY` assignment from the mode-change handler into `_handle_starting_substate`.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- ControlStateMachine ready for integration into ControlLoop (plan 14-03)
- TickResult contract defined: callers must write state, setpoint_kw to RTDB and increment pcs_command_seq when pcs_command_changed is True
- All state values are plain int constants matching ems_control_state_t — safe to use in C/Python cross-language RTDB reads
- Alarm manager can dispatch protection actions via request_fault_reset() or request_maintenance_enter() interfaces

---
*Phase: 14-control-state-machine*
*Completed: 2026-03-14*

## Self-Check: PASSED

- state_machine.py: FOUND
- test_state_machine.py: FOUND
- 14-02-SUMMARY.md: FOUND
- commit 7c00847 (RED): FOUND
- commit 0043e0e (GREEN): FOUND
- commit e784039 (REFACTOR): FOUND
