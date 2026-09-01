---
phase: 20-scheduler
plan: 02
subsystem: scheduler
tags: [python, async, zmq, req-rep, pub-sub, hot-reload, telemetry, signal-handling]
dependency_graph:
  requires: [ems_scheduler.config.load_schedule_config, ems_scheduler.evaluator, ems_common.ipc, SOCK_CONTROL_CMD, SOCK_CONFIG_PUB, SOCK_SCHEDULER_PUB]
  provides: [ems_scheduler.loop.SchedulerLoop, ems_scheduler.__main__.main]
  affects: [20-03-PLAN]
tech_stack:
  added: []
  patterns: [async 1Hz loop with timing correction, ZMQ REQ with timeout recovery, ZMQ SUB config hot-reload, ZMQ PUB telemetry multipart, background MockRepServer for integration tests, now_func injection for deterministic time]
key_files:
  created:
    - src/scheduler/src/ems_scheduler/loop.py
    - src/scheduler/src/ems_scheduler/__main__.py
    - src/scheduler/tests/test_loop.py
  modified: []
decisions:
  - "Schedule mode evaluated before day/night to set _schedule_owns_priority before day/night check"
  - "MockRepServer background thread replaces inline mock_rep for reliable ZMQ REQ/REP test isolation"
  - "now_func callable injection for deterministic time control in tests (avoids patching datetime globally)"
  - "_send_command accepts timeout_ms parameter for test-friendly short timeouts"
  - "_on_config_reloaded public method enables direct testing of config reload without ZMQ SUB"
metrics:
  duration: 458s
  completed: 2026-03-15
  tasks: 2
  tests: 15
  files_created: 3
  files_modified: 0
---

# Phase 20 Plan 02: SchedulerLoop Async Class and Entry Point Summary

SchedulerLoop 1Hz async class with ZMQ REQ command dispatch on state change only, three scheduling modes (manual/time_of_day/curve), day/night source_priority switching, config hot-reload via SUB, telemetry PUB, REQ socket timeout recovery, and CLI entry point with SIGTERM/SIGINT signal handling -- 15 integration tests with MockRepServer background thread.

## What Was Built

### SchedulerLoop Class (`loop.py`)
- 1Hz async loop with timing-corrected sleep (mirrors alarm_manager/control_manager pattern)
- Three scheduling modes: manual (no dispatch), time_of_day (window matching), curve (96-point index)
- Commands sent to control_manager via ZMQ REQ on state change only
- Two-step pattern: `source_priority {mode: manual}` before `manual_setpoint` in time_of_day/curve modes
- Day/night switching sends `source_priority day/night` independently of schedule mode
- When switching from time_of_day/curve to manual, restores day/night source_priority
- REQ socket closed and recreated after poll timeout (ZMQ REQ/REP lockstep recovery)
- Config hot-reload via SUB on SOCK_CONFIG_PUB, filters for name="schedule_config", resets tracking state
- Telemetry PUB publishes multipart [TOPIC_SCHEDULE, encoded envelope] with mode, active_window, curve_index, day_night
- `now_func` injection for deterministic time in tests

### Entry Point (`__main__.py`)
- argparse with `--config` (default: config/schedule_config.yaml) and `--log-level`
- SIGTERM/SIGINT signal handlers for graceful shutdown via stop_event
- Env var overrides: EMS_CONTROL_CMD_ENDPOINT, EMS_CONFIG_SUB_ENDPOINT, EMS_SCHEDULER_PUB_ENDPOINT
- Follows alarm_manager entry point pattern exactly

### Test Suite (`test_loop.py`)
- MockRepServer: background thread auto-replying OK to ZMQ REQ messages, collects (action, params) for assertions
- 15 integration tests covering all scenarios:
  - Manual mode: no commands sent
  - Time-of-day: setpoint on window change, no resend same window
  - Curve: setpoint on index change, no resend same index
  - Startup: immediate evaluation on first tick
  - Day/night: transition sends source_priority, works in manual mode, schedule mode overrides, restore on mode change
  - Hot-reload: resets tracking state, applies new mode
  - Telemetry: publishes state with correct topic and envelope
  - Error handling: REQ timeout recreates socket, command rejection logged

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Evaluation order: schedule mode before day/night**
- **Found during:** Task 1 (test_time_of_day_sends_setpoint_on_change, test_schedule_mode_overrides_day_night)
- **Issue:** Day/night evaluation running before time_of_day sent `source_priority day` on first tick, contradicting the "schedule mode overrides day/night" requirement
- **Fix:** Reordered _evaluate_tick to run schedule mode evaluation first (setting _schedule_owns_priority) before day/night evaluation
- **Files modified:** src/scheduler/src/ems_scheduler/loop.py
- **Commit:** ea53720

**2. [Rule 1 - Bug] Test architecture: synchronous _send_command needs background REP**
- **Found during:** Task 1 (tests blocking on ZMQ poll)
- **Issue:** Tests calling _evaluate_tick (which calls _send_command with blocking poll) without a background responder caused 5s timeouts
- **Fix:** Created MockRepServer class with background thread that auto-replies to all ZMQ REQ messages, replacing inline mock_rep fixtures
- **Files modified:** src/scheduler/tests/test_loop.py
- **Commit:** ea53720

## Commits

| Hash | Message |
|------|---------|
| 5c9d4a1 | test(20-02): add failing tests for SchedulerLoop |
| ea53720 | feat(20-02): implement SchedulerLoop with ZMQ command dispatch |
| dd45f9b | feat(20-02): add scheduler entry point with signal handling |

## Self-Check: PASSED
