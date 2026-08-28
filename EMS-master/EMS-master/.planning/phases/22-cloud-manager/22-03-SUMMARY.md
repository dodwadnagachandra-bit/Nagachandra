---
phase: 22-cloud-manager
plan: "03"
subsystem: cloud_manager
tags: [mqtt, zmq, command-dispatch, rate-limiting, heartbeat, asyncio, tdd]
dependency_graph:
  requires:
    - ems_cloud_manager.publisher.MqttPublisher
    - ems_cloud_manager.loop.CloudLoop
    - ems_cloud_manager.config.load_cloud_config
    - ems_common.ipc.encode_command_request
    - ems_common.ipc.decode_command_response
    - ems_common.ipc.SOCK_CONTROL_CMD
    - ems_common.ipc.SOCK_ALARM_CMD
  provides:
    - ems_cloud_manager.dispatcher.CommandDispatcher
    - ems_cloud_manager.dispatcher.RateLimiter
    - ems_cloud_manager.__main__.main
    - ems_cloud_manager.loop.CloudLoop._heartbeat_publisher
    - ems_cloud_manager.loop.CloudLoop._command_dispatcher_loop
  affects:
    - src/cloud_manager/src/ems_cloud_manager/loop.py
    - tests/test_cloud_manager.py
tech_stack:
  added: []
  patterns:
    - ZMQ REQ persistent sockets with LINGER=0 RCVTIMEO=5000ms (same as HMI pattern)
    - run_in_executor for blocking ZMQ send/recv from asyncio context
    - Sliding-window rate limiter (monotonic clock, no external dependency)
    - TYPE_CHECKING guard for CommandDispatcher circular import avoidance
    - argparse + env var overrides for all ZMQ endpoints (test isolation)
    - asyncio.gather(5 tasks) in CloudLoop.run()
key_files:
  created:
    - src/cloud_manager/src/ems_cloud_manager/dispatcher.py
    - src/cloud_manager/src/ems_cloud_manager/__main__.py
  modified:
    - src/cloud_manager/src/ems_cloud_manager/loop.py
    - tests/test_cloud_manager.py
decisions:
  - "heartbeat_interval is a constructor param (not config) to enable test isolation with interval=0"
  - "CommandDispatcher stores one persistent REQ socket per target endpoint (not per-call) to match HMI pattern"
  - "TYPE_CHECKING guard for CommandDispatcher import in loop.py avoids circular import at runtime"
  - "maintenance command action is dynamic: maintenance_enter / maintenance_exit built from params.action"
metrics:
  duration_s: 522
  tasks_completed: 2
  files_created: 2
  files_modified: 2
  completed_date: "2026-03-15"
---

# Phase 22 Plan 03: Command Dispatcher and Entry Point Summary

**One-liner:** MQTT-to-ZMQ command proxy (CommandDispatcher) with sliding-window rate limiter, 60s heartbeat task, and argparse __main__.py wiring all cloud_manager components into a systemd-ready process

## What Was Built

This plan completed Phase 22 by adding the bidirectional control path — cloud operators can now send remote commands that are validated, rate-limited, and forwarded to control_manager or alarm_manager via ZMQ REQ.

### CommandDispatcher (`dispatcher.py`)

Full MQTT-to-ZMQ command proxy:
- `RateLimiter`: sliding-window max 10 commands / 60s window using monotonic clock
- `CommandDispatcher.dispatch()`: 10-step pipeline — JSON parse -> field check -> rate limit -> route lookup -> param validate -> ZMQ action build -> encode -> send/recv -> decode -> MQTT response
- Command routing table (6 command types from CONTEXT.md locked decisions):
  - `mode_change` -> control_cmd / mode_change (validates target_state in [idle, standby])
  - `setpoint` -> control_cmd / manual_setpoint (validates power_kw is numeric)
  - `priority` -> control_cmd / source_priority (validates mode in [day, night, manual])
  - `fault_reset` -> control_cmd / fault_reset (no params needed)
  - `maintenance` -> control_cmd / maintenance_enter|exit (dynamic, validates action in [enter, exit])
  - `alarm_ack` -> alarm_cmd / acknowledge (validates alarm_id is non-empty string)
- Persistent ZMQ REQ sockets with LINGER=0, RCVTIMEO=5000ms
- `run_in_executor` for blocking ZMQ send/recv to keep asyncio event loop free
- All rejection paths publish error response to MQTT `{prefix}/responses/{request_id}`

### CloudLoop additions (`loop.py`)

Two new async tasks integrated into `CloudLoop.run()` (now gathers 5 tasks):

**`_heartbeat_publisher`:**
- Publishes `{device_id, uptime_s, version, connected, ts}` to `{prefix}/status` every `heartbeat_interval` seconds
- Skips publish when `publisher.connected is False`
- `heartbeat_interval` is a constructor param (default 60s) — pass 0 in tests for immediate firing
- `device_id` extracted from topic prefix: `"ems/RES-001"` -> `"RES-001"`

**`_command_dispatcher_loop`:**
- Polls `publisher.command_queue` (threading.Queue) at 100ms intervals
- Calls `dispatcher.dispatch(msg)` for each message when a dispatcher is configured
- Silently discards messages when no dispatcher is set (optional integration)

### Entry Point (`__main__.py`)

Full argparse entry point following alarm_manager pattern:
- `--config` (default: `config/cloud_config.yaml`), `--log-level`
- Env var overrides for all 6 ZMQ endpoints: EMS_TELEMETRY_SUB_ENDPOINT, EMS_ALARM_SUB_ENDPOINT, EMS_LOGGER_PUSH_ENDPOINT, EMS_CONTROL_CMD_ENDPOINT, EMS_ALARM_CMD_ENDPOINT, EMS_CLOUD_PUB_ENDPOINT
- Wires: MqttPublisher -> CommandDispatcher -> CloudLoop
- SIGTERM/SIGINT signal handlers -> `loop_obj.stop_event.set()`
- `finally:` block calls `dispatcher.cleanup()` then `loop_obj.cleanup()`

### Tests (`tests/test_cloud_manager.py`)

Converted all xfail stubs to real tests. Added 5 new tests (29 total, zero xfail):

- `TestCommandDispatcher.test_command_dispatch`: real ZMQ REP server, verifies action+params forwarded and MQTT response published
- `TestCommandDispatcher.test_command_invalid_rejected`: malformed JSON + missing request_id + unknown command all rejected with error, no ZMQ forward
- `TestCommandDispatcher.test_command_rate_limit`: 11 commands sent, exactly 10 reach ZMQ, 11th rejected with "rate limit exceeded"
- `test_heartbeat_payload`: heartbeat_interval=0, verifies all 5 required fields published when connected
- `test_heartbeat_not_published_when_disconnected`: publish_heartbeat never called when `connected=False`

## Test Results

```
29 passed in 1.77s
```

All CLOUD-01 through CLOUD-08 tests pass. Zero xfail remaining. Phase 22 complete.

## Verification

- `uv run pytest tests/test_cloud_manager.py -x -v` -- 29 passed, 0 failed
- `uv run python -m ems_cloud_manager --help` -- shows argparse usage
- `uv run python -c "from ems_cloud_manager.dispatcher import CommandDispatcher; print('dispatcher OK')"` -- passes

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed linter-injected xfail decorators**
- **Found during:** GREEN phase (Task 1)
- **Issue:** After writing the RED-phase test file, the linter auto-added `@pytest.mark.xfail` decorators to the new `TestCommandDispatcher` class and heartbeat test functions, treating them as stubs. This would have made the tests xpass rather than pass.
- **Fix:** Removed the three spurious `@pytest.mark.xfail` decorators added by the linter.
- **Files modified:** `tests/test_cloud_manager.py`
- **Commit:** fc86e3c

### Context from Parallel Execution

Plan 22-02 ran in parallel. Per the plan's instructions:
- Task 1 (dispatcher.py) was completed first.
- `loop.py` was then read from disk before modification — Plan 22-02 had written it by then.
- The `heartbeat_interval` parameter skeleton was already in the `__init__` signature from Plan 22-02, but the body and tasks were not implemented. This plan added the implementation.

## Commits

| Hash | Message |
|------|---------|
| 0f78a97 | test(22-03): add failing tests for CommandDispatcher and heartbeat (CLOUD-06/07) |
| fc86e3c | feat(22-03): CommandDispatcher with validation, rate limiting, and ZMQ REQ forwarding |
| f14153a | feat(22-03): heartbeat publisher, command dispatcher integration, and __main__ entry point |

## Self-Check: PASSED

- [x] src/cloud_manager/src/ems_cloud_manager/dispatcher.py -- FOUND
- [x] src/cloud_manager/src/ems_cloud_manager/__main__.py -- FOUND
- [x] src/cloud_manager/src/ems_cloud_manager/loop.py -- FOUND (modified)
- [x] tests/test_cloud_manager.py -- FOUND (modified)
- [x] Commit 0f78a97 -- FOUND
- [x] Commit fc86e3c -- FOUND
- [x] Commit f14153a -- FOUND
