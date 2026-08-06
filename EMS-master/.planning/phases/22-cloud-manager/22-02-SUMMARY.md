---
phase: 22-cloud-manager
plan: "02"
subsystem: cloud_manager
tags: [zmq, mqtt, telemetry, events, tdd, asyncio, cloud-loop]
dependency_graph:
  requires:
    - ems_cloud_manager.publisher.MqttPublisher
    - ems_common.ipc.SOCK_TELEMETRY
    - ems_common.ipc.SOCK_ALARM_PUB
  provides:
    - ems_cloud_manager.loop.CloudLoop
    - ems_common.ipc.SOCK_ALARM_PUB
  affects:
    - src/cloud_manager/src/ems_cloud_manager/loop.py
    - src/common/python/src/ems_common/ipc.py
    - tests/test_cloud_manager.py
tech_stack:
  added: []
  patterns:
    - asyncio gather with stop_event for multi-task coordination
    - zmq.NOBLOCK polling with asyncio.sleep(0.01) yield for non-blocking ZMQ in async context
    - Latest-value-wins accumulator pattern for telemetry downsampling
    - Phase 23 hook points via _do_publish_telemetry / _do_publish_event
key_files:
  created:
    - src/cloud_manager/src/ems_cloud_manager/loop.py
  modified:
    - src/common/python/src/ems_common/ipc.py
    - tests/test_cloud_manager.py
decisions:
  - "ZMQ telemetry polling uses NOBLOCK + asyncio.sleep(0.01) — avoids blocking the asyncio event loop while achieving ~100Hz poll rate"
  - "Phase 23 hook points as _do_publish_telemetry/_do_publish_event instance methods — Plan 23 can subclass or monkeypatch without touching the loop logic"
  - "Heartbeat stub added to CloudLoop.__init__ (heartbeat_interval kwarg) and _heartbeat_publisher() — allows Plan 22-03 tests to run against CloudLoop without breaking API"
metrics:
  duration_s: 420
  tasks_completed: 1
  files_created: 1
  files_modified: 2
  completed_date: "2026-03-15"
---

# Phase 22 Plan 02: CloudLoop Async Engine Summary

**One-liner:** CloudLoop with three async tasks: ZMQ telemetry accumulator (latest-value-wins), periodic consolidated JSON publish to MQTT, and alarm event forwarder with QoS 1

## What Was Built

### CloudLoop (`loop.py`)

Complete async engine bridging local ZMQ telemetry and events to the MQTT cloud broker:

**`_zmq_telemetry_collector()`**
- Polls `SOCK_TELEMETRY` ZMQ SUB socket (all topics) at ~100Hz using `zmq.NOBLOCK` + `asyncio.sleep(0.01)`
- Decodes msgpack telemetry envelopes via `decode_telemetry()`, extracts `msg["payload"]`
- Stores in `self._telemetry_snapshot[topic] = payload` — latest-value-wins, previous data replaced

**`_periodic_publish()`**
- Sleeps `interval_s` (from config) between publishes
- Skips if snapshot empty or `publisher.connected` is False
- Copies and clears snapshot atomically (single asyncio thread, no locks needed)
- Publishes `{"ts": epoch_ms, "data": {topic: payload, ...}}` via `publish_telemetry()` (QoS 0)
- Missing topics are omitted — never sent as null

**`_zmq_event_forwarder()`**
- Subscribes to `SOCK_ALARM_PUB` for `alarm`, `state_change`, `comm_fault` topics
- Decodes msgpack event bodies; builds `{"ts", "event_type", "data"}` cloud payload
- Calls `publish_event(topic, payload)` only when connected (QoS 1 via publisher)
- Also pushes raw event to logger PUSH socket for audit trail

**Phase 23 hook points:**
- `_do_publish_telemetry(payload)` — overrideable method wrapping `publisher.publish_telemetry()`
- `_do_publish_event(topic, payload)` — overrideable method wrapping `publisher.publish_event()`
- Plan 23 offline buffer can monkeypatch or subclass these to route through the buffer when disconnected

**Heartbeat stub (Plan 22-03 compatibility):**
- `heartbeat_interval` kwarg accepted in `__init__` (default 60s)
- `_heartbeat_publisher()` implemented with minimum viable fields: `device_id`, `uptime_s`, `version`, `connected`, `ts`
- Plan 22-03 will replace `version='0.0.0'` with real firmware version

### IPC Constants (`ems_common/ipc.py`)

Added:
- `SOCK_ALARM_PUB = "ipc:///run/ems/alarm_pub.sock"` — alarm_manager's PUB socket endpoint

### Tests (`tests/test_cloud_manager.py`)

Converted 4 xfail stubs to passing tests:
- `test_telemetry_accumulator` — ZMQ PUB sends msgpack frame; verifies snapshot updated
- `test_periodic_publish` — short interval_s; verifies `publish_telemetry` called with `{"ts", "data"}`
- `test_missing_topics_omitted` — only pcs injected; bms.rack absent from published payload
- `test_event_qos1` — alarm PUB sends msgpack event; verifies `publish_event("alarm", {...})` called

Also absorbed Plan 22-03 tests added by the linter:
- `TestCommandDispatcher` — CLOUD-06 tests for `dispatcher.py` (pre-created for Plan 22-03)
- `test_heartbeat_payload` / `test_heartbeat_not_published_when_disconnected` — CLOUD-07

## Test Results

```
29 passed in 1.76s
```

All CLOUD-01, CLOUD-02, CLOUD-03, CLOUD-07, CLOUD-08 tests pass. CLOUD-06 (CommandDispatcher) also passes because `dispatcher.py` was pre-created.

## Verification

- `uv run python -c "from ems_cloud_manager.loop import CloudLoop; print('loop OK')"` — passes
- `uv run pytest tests/test_cloud_manager.py::test_telemetry_accumulator tests/test_cloud_manager.py::test_periodic_publish tests/test_cloud_manager.py::test_missing_topics_omitted tests/test_cloud_manager.py::test_event_qos1 -x -v` — 4 passed
- `uv run pytest tests/test_cloud_manager.py -x -v` — 29 passed

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing functionality] Added heartbeat_interval kwarg and _heartbeat_publisher stub**
- **Found during:** GREEN phase — linter added Plan 22-03 test stubs calling `CloudLoop(heartbeat_interval=...)` and `_heartbeat_publisher()`
- **Issue:** `test_heartbeat_payload` and `test_heartbeat_not_published_when_disconnected` tested against `CloudLoop` but required `heartbeat_interval` kwarg and `_heartbeat_publisher()` method not yet in scope
- **Fix:** Added `heartbeat_interval: float | None = None` to `__init__`, stored as `self._heartbeat_interval`, implemented minimum `_heartbeat_publisher()` with CLOUD-07 required fields (`device_id`, `uptime_s`, `version`, `connected`, `ts`)
- **Files modified:** `src/cloud_manager/src/ems_cloud_manager/loop.py`
- **Commit:** c541467

**2. [Rule 2 - Missing constant] Added SOCK_ALARM_PUB to ems_common.ipc**
- **Found during:** RED phase — test required importing SOCK_ALARM_PUB; plan referenced it in interfaces but it was not present in ipc.py
- **Fix:** Added `SOCK_ALARM_PUB = "ipc:///run/ems/alarm_pub.sock"` to ipc.py
- **Files modified:** `src/common/python/src/ems_common/ipc.py`
- **Commit:** 96d8a8c

## Commits

| Hash | Message |
|------|---------|
| 96d8a8c | test(22-02): add failing tests for CloudLoop telemetry collector, periodic publish, and event forwarder |
| 8431e5b | feat(22-02): implement CloudLoop with ZMQ telemetry collector, periodic MQTT publish, and event forwarder |
| c541467 | feat(22-02): add heartbeat_interval kwarg and _heartbeat_publisher stub to CloudLoop |

## Self-Check: PASSED

- [x] src/cloud_manager/src/ems_cloud_manager/loop.py — FOUND
- [x] src/common/python/src/ems_common/ipc.py — FOUND (SOCK_ALARM_PUB added)
- [x] tests/test_cloud_manager.py — FOUND
- [x] Commit 96d8a8c — FOUND
- [x] Commit 8431e5b — FOUND
- [x] Commit c541467 — FOUND
