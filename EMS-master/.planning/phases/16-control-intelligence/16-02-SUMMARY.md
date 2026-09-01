---
phase: 16-control-intelligence
plan: "02"
subsystem: control
tags: [zmq, pub-sub, config-hot-reload, alarm-protection, intelligence, iec-62682, rtdb]

# Dependency graph
requires:
  - phase: 16-01
    provides: ControlIntelligence pure logic class with evaluate()/update_config() interface
  - phase: 15-alarm-manager
    provides: AlarmLoop with ZMQ PUB alarm events (raw msgpack, topic=alarm)
  - phase: 14-control-state-machine
    provides: ControlStateMachine, ControlLoop, RTDB seqlock patterns

provides:
  - SOCK_CONFIG_PUB constant in ipc.py and ipc_defs.h for config_reload PUB endpoint
  - ConfigManager with ZMQ PUB socket broadcasting config_reload events as multipart [topic, msgpack_body]
  - ControlLoop wired to ControlIntelligence every tick with alarm SUB + config reload SUB
  - AlarmLoop with config hot-reload via ZMQ SUB, preserving active alarm lifecycle state
  - ControlStateMachine fault injection via request_mode_change("fault") for alarm protection
  - active_derating_pct and source_priority written to RTDB system section every tick

affects: [17-integration, control-manager, alarm-manager, config-manager, scheduler]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Config hot-reload pattern: ZMQ SUB receives event trigger, re-reads from disk (disk = source of truth)"
    - "Alarm protection injection: loop calls request_mode_change('fault') before SM tick when protection severity active"
    - "ZMQ slow subscriber fix: bind PUB first, start SUB thread second, sleep 100ms before send"
    - "Sync ZMQ in background thread for tests (avoids asyncio event loop scope issues)"
    - "Lifecycle preservation across reload: copy state/timestamps from old instances to new instances by alarm_id"

key-files:
  created:
    - src/config_manager/tests/__init__.py
    - src/config_manager/tests/test_manager.py
  modified:
    - src/common/python/src/ems_common/ipc.py
    - src/common/c/include/ipc_defs.h
    - src/config_manager/src/ems_config_manager/manager.py
    - src/control_manager/python/src/ems_control_manager/loop.py
    - src/control_manager/python/src/ems_control_manager/state_machine.py
    - src/control_manager/python/tests/test_loop.py
    - src/alarm_manager/src/ems_alarm_manager/loop.py
    - src/alarm_manager/tests/test_loop.py

key-decisions:
  - "Config reload uses disk re-read (not event payload) — event is trigger, disk is source of truth for validation path"
  - "SOCK_ALARM_PUB defined locally in control_manager/loop.py to avoid circular import with alarm_manager"
  - "ControlStateMachine gains _pending_fault_inject flag and 'fault' target in request_mode_change — EMERGENCY is still highest priority, fault injection is Priority 1b"
  - "Alarm cooldown: 60 seconds after protection or action severity before _last_alarm_severity resets"
  - "AlarmLoop lifecycle preservation: only copies state/activated_at/acknowledged_at/exceeded_since_ms — clears_at and rtn_at reset on new instance"

patterns-established:
  - "ZMQ PUB/SUB config broadcast: ConfigManager binds PUB on SOCK_CONFIG_PUB; subscribers NOBLOCK recv every tick"
  - "Protection alarm flow: alarm_manager PUB -> control_manager SUB -> _last_alarm_severity = protection -> SM fault inject"

requirements-completed: [ALM-08, CTRL-11, ALM-09]

# Metrics
duration: 180min
completed: 2026-03-15
---

# Phase 16 Plan 02: Config Reload PUB + Alarm Protection + Intelligence Integration Summary

**Config-manager broadcasts config_reload via ZMQ PUB; ControlLoop and AlarmLoop subscribe via ZMQ SUB for runtime config hot-reload; protection alarms drive SM fault injection; ControlIntelligence evaluates derating and source priority every tick.**

## Accomplishments

- Added SOCK_CONFIG_PUB to ipc.py/ipc_defs.h; ConfigManager broadcasts config_reload events as multipart [b"config_reload", msgpack_body] on ZMQ PUB alongside existing PUSH to logger
- Wired ControlIntelligence into ControlLoop._tick() with BMS thermal reads, alarm severity tracking, SOC cutoff, derating+source_priority RTDB writes
- Protection-severity alarm events from alarm_manager now drive SM fault injection via new _pending_fault_inject flag in ControlStateMachine
- AlarmLoop subscribes to SOCK_CONFIG_PUB for alarms_config hot-reload, preserving active alarm lifecycle state across reloads
- 8 + 11 + 5 new tests (config_manager, control_manager, alarm_manager) — all 238 tests green across three modules

## Task Commits

1. **Task 1: Add SOCK_CONFIG_PUB + ConfigManager PUB socket** - `53e10d6` (feat)
2. **Task 2 RED: Failing tests for ControlLoop alarm + intelligence + config reload** - `f694643` (test)
3. **Task 2 GREEN: Wire ControlIntelligence + alarm SUB + config reload SUB into ControlLoop** - `10760f9` (feat)
4. **Task 3: AlarmLoop config hot-reload via ZMQ SUB** - `4446cd4` (feat)

## Files Created/Modified

- `src/common/python/src/ems_common/ipc.py` — Added SOCK_CONFIG_PUB, TOPIC_CONFIG_RELOAD (already present, just exported)
- `src/common/c/include/ipc_defs.h` — Added EMS_SOCK_CONFIG_PUB #define
- `src/config_manager/src/ems_config_manager/manager.py` — Added _pub_sock, _init_pub_socket(), _close_pub_socket(), PUB broadcast in handle_reload()
- `src/config_manager/tests/test_manager.py` — 8 tests for PUB socket init and handle_reload broadcast
- `src/control_manager/python/src/ems_control_manager/loop.py` — Complete rewrite: alarm SUB, config reload SUB, BMS thermal reads, intelligence integration, RTDB derating+source writes
- `src/control_manager/python/src/ems_control_manager/state_machine.py` — Added _pending_fault_inject, "fault" target in request_mode_change, Priority 1b fault inject in tick()
- `src/control_manager/python/tests/test_loop.py` — 11 new tests: alarm protection flow, config hot-reload, intelligence integration
- `src/alarm_manager/src/ems_alarm_manager/loop.py` — Added config_sub_endpoint/config_path params, _config_sub socket, _poll_config_reload() with lifecycle preservation
- `src/alarm_manager/tests/test_loop.py` — 5 new tests: threshold update, lifecycle preservation, enable/disable, wrong name ignored, invalid YAML rejected

## Decisions Made

- ControlStateMachine needed a new `_pending_fault_inject` flag because `request_mode_change` only handled "standby"/"idle"; "fault" could not use `_pending_target_state` because the SM tick routes FAULT state handling separately from normal pending transitions
- Exception handling in `_poll_config_reload()` uses bare `except Exception` (not just FileNotFoundError/ValueError) because yaml.ScannerError is not a subclass of ValueError and would propagate uncaught
- SOCK_ALARM_PUB defined locally in control_manager/loop.py (not imported from alarm_manager) to prevent circular imports — both modules share the same string constant value

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ControlStateMachine missing "fault" target in request_mode_change()**
- **Found during:** Task 2 GREEN (test run)
- **Issue:** `request_mode_change("fault")` returned `(False, "Unknown target mode: 'fault'")` — no fault injection path existed in SM
- **Fix:** Added `_pending_fault_inject: bool` instance var, "fault" target in `request_mode_change()`, and Priority 1b handler in `tick()` that processes fault inject before PCS fault check
- **Files modified:** `src/control_manager/python/src/ems_control_manager/state_machine.py`
- **Committed in:** `10760f9`

**2. [Rule 1 - Bug] yaml.ScannerError not caught by (FileNotFoundError, ValueError)**
- **Found during:** Task 2 GREEN (test: test_invalid_config_on_disk_rejected_keeps_old_config)
- **Issue:** `_poll_config_reload()` only caught FileNotFoundError and ValueError; invalid YAML raises yaml.scanner.ScannerError which is not a subclass of either
- **Fix:** Broadened exception handler to `except Exception` in `_poll_config_reload()` in both loop.py files
- **Files modified:** `src/control_manager/python/src/ems_control_manager/loop.py`
- **Committed in:** `10760f9`

---

**Total deviations:** 2 auto-fixed (2× Rule 1 — Bug)
**Impact on plan:** Both fixes necessary for correctness; no scope creep.

## Issues Encountered

- ZMQ "slow subscriber" pattern required careful test design: PUB must bind before SUB connects, then sleep 100ms inside the coroutine before sending. This pattern was established in Task 1 and reused in Tasks 2-3.
- Task 1 schema validation: initial test config included fields not in `state_machine` section schema (only `loop_interval_ms` and `fault_retry_count` allowed). Fixed by removing extra fields from test helper.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 17 (integration) can wire all ZMQ endpoints using real IPC socket paths
- ConfigManager `__main__.py` needs to call `_init_pub_socket(ctx)` during startup (not done here — deferred to Phase 17)
- ControlLoop and AlarmLoop default to SOCK_CONFIG_PUB on startup — no config changes needed
- All alarm protection → control fault flow is fully operational; tested end-to-end via ZMQ TCP

## Self-Check: PASSED

All files verified present. All task commits verified in git log.

---
*Phase: 16-control-intelligence*
*Completed: 2026-03-15*
