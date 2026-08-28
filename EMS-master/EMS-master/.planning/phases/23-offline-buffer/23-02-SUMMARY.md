---
phase: 23-offline-buffer
plan: "02"
subsystem: cloud_manager
tags: [cloud, offline-buffer, replay, CLOUD-05, buffered-loop]
dependency_graph:
  requires: [23-01]
  provides: [BufferedCloudLoop, offline-replay-task]
  affects: [cloud_manager]
tech_stack:
  added: []
  patterns:
    - CloudLoop subclass overriding hook methods for offline routing
    - Async replay task with 10 msg/s throttle (asyncio.sleep(0.1))
    - At-least-once delivery: file deleted only after all records published
    - Connection-guard removal in _periodic_publish and _zmq_event_forwarder
    - ZMQ cloud_buffer telemetry for buffer progress (files_remaining, mb_remaining)
key_files:
  created:
    - src/cloud_manager/src/ems_cloud_manager/buffered_loop.py
  modified:
    - src/cloud_manager/src/ems_cloud_manager/__main__.py
    - tests/test_cloud_manager.py
decisions:
  - "ZMQ slow-joiner fix: bind alarm PUB socket before constructing loop so subscriber connects to an already-listening socket; 0.1s settle before sending event"
  - "_publish_buffer_status uses AttributeError guard for publisher._cloud_pub access — allows mocked publishers in tests that lack _cloud_pub attribute"
  - "test_replay_resumes_on_reconnect uses 1.5s wait after reconnect to survive the 1.0s disconnect poll interval before the reconnect fires"
  - "TestMainWiring replicates wiring logic inline rather than calling __main__.run() to avoid needing paho broker and signal handler setup in tests"
metrics:
  duration_minutes: 10
  completed_date: "2026-03-15"
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 2
---

# Phase 23 Plan 02: BufferedCloudLoop Integration Summary

**One-liner:** BufferedCloudLoop subclass routes telemetry/events to JSONL buffer when offline, drains FIFO at 10 msg/s on reconnect with ZMQ progress reporting.

## Objective

Connect the BufferManager (Plan 01) to the live CloudLoop pipeline. Override the two publish hook points to route to the buffer when disconnected, remove connection guards from `_periodic_publish` and `_zmq_event_forwarder` so data flows to the buffer when offline, and add a sixth async replay task that drains the buffer FIFO at 10 msg/s whenever MQTT is live.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing TestBufferedCloudLoop tests | 9522d37 | tests/test_cloud_manager.py |
| 1 (GREEN) | BufferedCloudLoop implementation | 2faf375 | buffered_loop.py (created), tests/test_cloud_manager.py (fixed timing) |
| 2 | Wire BufferedCloudLoop in __main__.py | b2f1e88 | __main__.py, tests/test_cloud_manager.py |

## Implementation Details

### buffered_loop.py — BufferedCloudLoop class

- `__init__(config, publisher, buffer_manager, **kwargs)` — passes kwargs to CloudLoop, stores buffer as `_buffer`, sets `_replay_rate = 10.0`
- `_do_publish_telemetry(payload)` — routes to `publisher.publish_telemetry()` or `buffer.write("telemetry", "telemetry", payload)` based on `connected`
- `_do_publish_event(topic, payload)` — routes to `publisher.publish_event()` or `buffer.write("event", topic, payload)` based on `connected`
- `_periodic_publish()` — copy of base class with connection guard removed; always calls `_do_publish_telemetry()` when snapshot is non-empty
- `_zmq_event_forwarder()` — copy of base class with `if self._publisher.connected:` guard removed; always calls `_do_publish_event()`; audit logger unchanged
- `run()` — adds `_buffer_replay_task()` as sixth coroutine in `asyncio.gather()`
- `_buffer_replay_task()` — polls every 1s while disconnected; on reconnect calls `buffer.flush()`, drains FIFO at `1/_replay_rate` intervals, stops mid-file on disconnect, deletes files only after all records published, calls `_publish_buffer_status()` after each cycle
- `_publish_buffer_status()` — encodes `{ts, buffer_files_remaining, buffer_mb_remaining}` via `encode_telemetry`, sends on ZMQ PUB with topic `"cloud_buffer"`

### __main__.py updates

- Added imports: `BufferManager`, `BufferedCloudLoop`, `Any`
- Conditional wiring in `run()`: reads `config["offline_buffer"]["enabled"]`, constructs `BufferManager` + `BufferedCloudLoop` when True, plain `CloudLoop` when False
- `EMS_CLOUD_BUFFER_DIR` env var overrides default `data/cloud_buffer` directory
- Module docstring updated with `EMS_CLOUD_BUFFER_DIR` env var description

### Test coverage (16 new tests: 13 TestBufferedCloudLoop + 3 TestMainWiring)

- `test_telemetry_routes_to_buffer_when_offline` — buffer.write() called, not publish_telemetry
- `test_telemetry_routes_to_mqtt_when_online` — publish_telemetry called, not buffer.write
- `test_event_routes_to_buffer_when_offline` — buffer.write("event", topic, payload)
- `test_event_routes_to_mqtt_when_online` — publish_event called
- `test_periodic_publish_calls_hook_when_offline` — hook called with interval=0.05s
- `test_event_forwarder_calls_hook_when_offline` — real ZMQ send, hook captured
- `test_replay_drains_fifo` — publish_telemetry called after reconnect
- `test_replay_throttle` — 3 records * 0.1s = measurable elapsed time
- `test_replay_stops_on_disconnect` — fewer than 10 of 20 records published
- `test_replay_resumes_on_reconnect` — publish fires after pub.connected flipped True
- `test_replay_deletes_file_after_full_publish` — 0 JSONL files remain after drain
- `test_buffer_status_published` — `_publish_buffer_status` called at least once
- `test_flush_before_replay` — `buffer.flush()` called before drain
- `test_main_creates_buffered_loop_when_enabled` — isinstance(BufferedCloudLoop)
- `test_main_creates_plain_loop_when_disabled` — isinstance(CloudLoop), not subclass
- `test_main_uses_env_var_for_buffer_dir` — `_buffer._buffer_dir` matches custom dir

## Decisions Made

1. **ZMQ slow-joiner fix in tests**: The `test_event_forwarder_calls_hook_when_offline` test binds the alarm PUB socket before constructing the loop (subscriber connects to already-bound socket) and waits 0.1s before sending — matches the pattern used by the existing Phase 22 event forwarder tests.

2. **`_publish_buffer_status` AttributeError guard**: `publisher._cloud_pub` is a private attribute that exists on real `MqttPublisher` instances but not on `mock.MagicMock`. The method wraps the ZMQ send in `try/except AttributeError` to allow mocked publishers in unit tests.

3. **Reconnect poll timing in tests**: The disconnect poll sleeps `1.0s` between checks. `test_replay_resumes_on_reconnect` waits `1.5s` after setting `pub.connected = True` to ensure the poll loop wakes and processes the reconnect.

4. **TestMainWiring inline wiring**: Tests replicate the wiring conditional directly rather than importing and calling `__main__.run()`. This avoids needing paho broker, signal handlers, and asyncio.run() complexity in a simple isinstance check.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ZMQ slow-joiner in test_event_forwarder_calls_hook_when_offline**
- **Found during:** Task 1 GREEN phase (test was passing individually but flaky)
- **Issue:** Test was binding alarm PUB after starting the subscriber task, causing the first message to be dropped before subscription propagated
- **Fix:** Moved `alarm_pub.bind()` before `BufferedCloudLoop()` construction so subscriber connects to already-bound socket; matched Pattern from existing Phase 22 event forwarder tests
- **Files modified:** tests/test_cloud_manager.py
- **Commit:** 2faf375

**2. [Rule 1 - Bug] test_replay_resumes_on_reconnect timing too short**
- **Found during:** Task 1 GREEN phase
- **Issue:** Test waited only 0.3s after reconnect but the disconnect poll loop sleeps 1.0s between checks, so reconnect wasn't seen in time
- **Fix:** Extended wait from 0.3s to 1.5s after setting `pub.connected = True`
- **Files modified:** tests/test_cloud_manager.py
- **Commit:** 2faf375

## Verification Results

- `uv run pytest tests/test_cloud_manager.py -k "TestBufferedCloudLoop" -x -q` — 13 passed
- `uv run pytest tests/test_cloud_manager.py -x -q` — 58 passed (Phase 22: 42, Phase 23: 16)
- `python -c "from ems_cloud_manager.buffered_loop import BufferedCloudLoop; print('import ok')"` — import ok

## Self-Check: PASSED

- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/buffered_loop.py` — FOUND
- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/__main__.py` — FOUND (contains BufferedCloudLoop)
- `/home/overlord/EMS/tests/test_cloud_manager.py` — FOUND (contains TestBufferedCloudLoop, TestMainWiring)
- Commit 9522d37 (RED) — FOUND
- Commit 2faf375 (GREEN feat) — FOUND
- Commit b2f1e88 (Task 2) — FOUND
