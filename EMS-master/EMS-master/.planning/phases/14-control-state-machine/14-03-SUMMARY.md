---
phase: 14-control-state-machine
plan: "03"
subsystem: control
tags: [control-manager, python, asyncio, zmq, rtdb, seqlock, 1hz, state-machine, tdd]

# Dependency graph
requires:
  - phase: 14-control-state-machine
    plan: "01"
    provides: "EmsSystem ctypes struct with pcs_command/pcs_command_seq fields, attach_rtdb() returning (shm, rtdb) tuple"
  - phase: 14-control-state-machine
    plan: "02"
    provides: "ControlStateMachine.tick(), TickResult dataclass, all state/PCS/command constants"

provides:
  - "ControlLoop class: 1Hz asyncio loop reading RTDB (pcs, gpio, system) and writing RTDB system section via seqlock"
  - "ZMQ REP on SOCK_CONTROL_CMD handles mode_change, manual_setpoint, fault_reset, maintenance_enter/exit, source_priority (6 commands)"
  - "ZMQ PUB on SOCK_CONTROL_PUB publishes control.state telemetry at 1Hz"
  - "ZMQ PUSH to SOCK_LOGGER sends state_change events on SM transitions via encode_event"
  - "Entry point: python -m ems_control_manager --config PATH --log-level LEVEL"
  - "SAFETY_EMERGENCY_DO_INDEX=5 constant (DO_PCS_STOP from safety_manager response_matrix)"
  - "21 loop integration tests covering all RTDB reads/writes and ZMQ command/telemetry/event paths"

affects:
  - "15-alarm-manager (alarm_manager sends ZMQ REQ to SOCK_CONTROL_CMD for protection dispatch)"
  - "16-derating-interlocks (control_manager will receive derating commands via source_priority)"
  - "17-integration (smoke test drives control_manager via ZMQ REQ)"
  - "hmi_server (subscribes to SOCK_CONTROL_PUB for control.state telemetry)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "_seqlock_read_section() in loop.py mirrors publisher.py pattern — 100-retry loop, memmove, even sequence guard"
    - "ControlLoop.__init__ patches: test uses patch('ems_control_manager.loop.attach_rtdb') returning (MagicMock(), MockRtdb())"
    - "ZMQ TCP test endpoints: each test class uses unique tcp://127.0.0.1:155XX ports (avoids /run/ems dependency)"
    - "50ms connect sleep + 20ms deliver sleep: required for ZMQ TCP tests — not needed for ipc:// but good practice"
    - "source_priority deferred stub: accepts any mode, logs, returns ok — full dispatch wired in Phase 16"

key-files:
  created:
    - "src/control_manager/python/src/ems_control_manager/loop.py"
    - "src/control_manager/python/src/ems_control_manager/__main__.py"
    - "src/control_manager/python/tests/test_loop.py"
  modified: []

key-decisions:
  - "ControlLoop exposes stop_event property so __main__.py can wire SIGTERM/SIGINT without coupling to asyncio internals"
  - "SOCK_CONTROL_PUB defined locally in loop.py (not added to ipc.py) — plan gave option and local avoids ipc.py churn"
  - "source_priority returns ok immediately with a log line — prevents Phase 15/16 callers from getting errors before full implementation"
  - "SAFETY_EMERGENCY_DO_INDEX=5 matches DO_PCS_STOP in safety_manager/src/response_matrix.h — any PCS stop means emergency from control perspective"
  - "MockRtdb uses real ctypes structs in process memory — seqlock operations are exact matches to live shm behavior"
  - "_build_loop helper patches attach_rtdb at import location (ems_control_manager.loop.attach_rtdb), not at definition (ems_common.rtdb.attach_rtdb)"

patterns-established:
  - "ControlLoop pattern: __init__ binds sockets, run() is the asyncio loop, cleanup() closes all resources"
  - "REP socket safety: _poll_commands() wraps entire dispatch in try/except, ALWAYS sends a reply even on internal error"
  - "1Hz loop timing: tick_start = time.monotonic(); ... ; await asyncio.sleep(max(0, interval - elapsed))"

requirements-completed: [CTRL-01, CTRL-10, CTRL-12]

# Metrics
duration: 6min
completed: "2026-03-14"
---

# Phase 14 Plan 03: Control Loop RTDB Integration Summary

**ControlLoop class wiring the 1Hz state machine to RTDB shared memory, ZMQ REP command API, PUB telemetry, and PUSH events — control_manager is now a runnable service**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-14T19:30:22Z
- **Completed:** 2026-03-14T19:36:28Z
- **Tasks:** 2 (Task 1 TDD: RED/GREEN/REFACTOR, Task 2: entry point)
- **Files modified:** 3 (2 created source, 1 created test)

## Accomplishments

- `ControlLoop` class connecting the pure state machine (Plan 02) to real I/O: seqlock reads from pcs/gpio/system RTDB sections each tick, seqlock write to system section after SM tick, monotonic `pcs_command_seq` increment when SM signals command change
- ZMQ REP on `SOCK_CONTROL_CMD` handles all 6 commands with correct dispatch; REP safety protocol ensures a reply is always sent even if decode or dispatch raises an exception
- ZMQ PUB on `SOCK_CONTROL_PUB` publishes `control.state` telemetry (state, setpoint_kw, pcs_command, safety_emergency) at every tick; ZMQ PUSH sends `state_change` events to logger on every SM state transition
- `__main__.py` entry point: `python -m ems_control_manager --config PATH` with SIGTERM/SIGINT signal handlers, graceful cleanup via `loop.cleanup()`
- 21 new integration tests covering all behavioral requirements; all 76 control_manager tests pass; no regressions in comm_manager (88) or RTDB (8) tests

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests for ControlLoop** - `107ae7c` (test)
2. **Task 1 GREEN: ControlLoop implementation** - `0ba48b1` (feat)
3. **Task 1 REFACTOR: Move TickResult import to module level** - `2ae3fbe` (refactor)
4. **Task 2: Entry point and service wiring** - `2be0755` (feat)

_Note: TDD plan — 3 commits for Task 1 following RED-GREEN-REFACTOR cycle_

## Files Created/Modified

- `/home/overlord/EMS/src/control_manager/python/src/ems_control_manager/loop.py` - ControlLoop class: seqlock reads/writes, ZMQ REP/PUB/PUSH, 1Hz asyncio run loop, cleanup
- `/home/overlord/EMS/src/control_manager/python/src/ems_control_manager/__main__.py` - Entry point: argparse, signal handling, asyncio.run()
- `/home/overlord/EMS/src/control_manager/python/tests/test_loop.py` - 21 integration tests covering RTDB reads/writes and all ZMQ paths

## Decisions Made

- **SOCK_CONTROL_PUB defined locally** in loop.py as `ipc:///run/ems/control_pub.sock` — plan gave option between local and ipc.py; local chosen to avoid ipc.py churn since data_manager binds SOCK_TELEMETRY and control_manager needs its own PUB endpoint
- **SAFETY_EMERGENCY_DO_INDEX=5** matches `DO_PCS_STOP` (bit 5) from `safety_manager/src/response_matrix.h` — PCS emergency stop signal is the correct indicator for control state machine emergency entry
- **source_priority stub** accepts any mode and returns ok with a log line — prevents downstream callers from receiving errors before Phase 16 wires full dispatch logic
- **MockRtdb uses real ctypes structs** (EmsPcs, EmsGpio, EmsSystem in process memory) so seqlock arithmetic is exactly the same as live shared memory behavior

## Deviations from Plan

None — plan executed exactly as written.

The only implementation discovery was the ZMQ TCP test timing requirement (50ms connect sleep + 20ms deliver sleep) which is standard for ZMQ TCP test suites. This is a test infrastructure concern, not a plan deviation.

## Issues Encountered

None significant. ZMQ TCP test timing is a known pattern — after `socket.connect()` there is kernel TCP handshake latency before messages can flow. Added `_CONNECT_SLEEP_S = 0.05` and `_MSG_DELIVER_S = 0.02` constants to tests. All 21 tests pass reliably.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `control_manager` is a fully runnable service: start with `python -m ems_control_manager --config config/control_config.yaml`
- ZMQ REP on `ipc:///run/ems/control_cmd.sock` ready to receive commands from alarm_manager (Phase 15)
- `pcs_command` + `pcs_command_seq` RTDB fields are written correctly — comm_manager (Plan 14-01) already polls these
- All 8 control states and all PCS sub-states tested; the state machine is production-ready

---
*Phase: 14-control-state-machine*
*Completed: 2026-03-14*

## Self-Check: PASSED

- loop.py: FOUND
- __main__.py: FOUND
- test_loop.py: FOUND
- 14-03-SUMMARY.md: FOUND
- commit 107ae7c (RED): FOUND
- commit 0ba48b1 (GREEN): FOUND
- commit 2ae3fbe (REFACTOR): FOUND
- commit 2be0755 (entry point): FOUND
