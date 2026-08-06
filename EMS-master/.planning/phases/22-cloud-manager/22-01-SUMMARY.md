---
phase: 22-cloud-manager
plan: "01"
subsystem: cloud_manager
tags: [mqtt, paho, mtls, zmq, config, tdd]
dependency_graph:
  requires: []
  provides:
    - ems_cloud_manager.config.load_cloud_config
    - ems_cloud_manager.publisher.MqttPublisher
    - ems_common.ipc.SOCK_CLOUD_PUB
    - ems_common.ipc.TOPIC_CLOUD
  affects:
    - src/common/python/src/ems_common/ipc.py
    - tests/test_cloud_manager.py
tech_stack:
  added:
    - paho-mqtt==2.1.0
  patterns:
    - paho CallbackAPIVersion.VERSION2 with VERSION2 callback signatures
    - threading.Queue as paho-to-asyncio bridge (not asyncio.Queue)
    - ZMQ PUB binds, HMI SUB connects pattern (mirrors alarm_pub.sock)
    - JSON Schema validation via Draft202012Validator (alarm_manager pattern)
key_files:
  created:
    - src/cloud_manager/src/ems_cloud_manager/config.py
    - src/cloud_manager/src/ems_cloud_manager/publisher.py
    - tests/test_cloud_manager.py
  modified:
    - src/cloud_manager/pyproject.toml
    - src/common/python/src/ems_common/ipc.py
    - uv.lock
decisions:
  - "ZMQ cloud status uses dedicated SOCK_CLOUD_PUB (ipc:///run/ems/cloud_pub.sock), not SOCK_TELEMETRY — avoids multi-binder conflict; HMI subscribes to both endpoints"
  - "MqttPublisher.start() is separate from __init__ — testability; callers control when network loop begins"
  - "threading.Queue used for command_queue and status_queue — paho callbacks run in paho network thread, not asyncio thread"
metrics:
  duration_s: 305
  tasks_completed: 2
  files_created: 3
  files_modified: 3
  completed_date: "2026-03-15"
---

# Phase 22 Plan 01: Cloud Manager Foundation Summary

**One-liner:** paho-mqtt 2.x MqttPublisher with mTLS/VERSION2 callbacks + JSON Schema config loader + SOCK_CLOUD_PUB ZMQ status socket

## What Was Built

This plan established the cloud_manager foundation that Plans 02 and 03 build upon.

### Config Loader (`config.py`)

`load_cloud_config(path, schema_path)` following the alarm_manager pattern exactly:
- Loads YAML, validates against `cloud_config.schema.json` via `Draft202012Validator`
- When `auth.method=mtls`, verifies all three cert paths exist on disk; raises `FileNotFoundError` with descriptive message
- Skips cert path checks for `auth.method=token` (dev/test mode)
- Schema path derived from `path.parent.parent / "schemas" / "cloud_config.schema.json"` (auto-discovery)

### IPC Constants (`ems_common/ipc.py`)

Added:
- `SOCK_CLOUD_PUB = "ipc:///run/ems/cloud_pub.sock"` — dedicated ZMQ PUB for cloud status
- `TOPIC_CLOUD = "cloud"` — topic string for HMI subscription filtering

### MqttPublisher (`publisher.py`)

Complete paho-mqtt 2.x wrapper with:
- `CallbackAPIVersion.VERSION2` (required in paho 2.x — raises TypeError otherwise)
- mTLS via `tls_set(ca_certs, certfile, keyfile, ssl.PROTOCOL_TLS_CLIENT)` — only when `broker.protocol=mqtts` or `auth.method=mtls`
- `reconnect_delay_set(min_delay=1, max_delay=60)` — exponential backoff
- VERSION2 callback signatures for `_on_connect`, `_on_disconnect`, `_on_message`
- `_on_connect`: re-subscribes to `{prefix}/commands` QoS 1 on every connect (required with `clean_session=True`)
- `_on_message`: puts MQTTMessage onto `threading.Queue` (safe from paho network thread)
- `_publish_cloud_status(state)`: publishes msgpack-encoded status on SOCK_CLOUD_PUB using `encode_telemetry`
- `start()` separated from `__init__` for testability
- `cleanup()`: calls `loop_stop()`, `disconnect()`, closes ZMQ socket

### Test Scaffold (`tests/test_cloud_manager.py`)

663 lines covering all CLOUD-01 through CLOUD-08 requirements:
- `TestLoadCloudConfig`: 7 passing tests — happy path, missing file, invalid YAML, schema violation, mTLS cert validation, token auth bypass
- `TestIpcConstants`: 2 passing tests — SOCK_CLOUD_PUB and TOPIC_CLOUD importable
- `TestMqttPublisherInit`: 5 passing tests — VERSION2, tls_set, reconnect_delay_set, start(), callbacks
- `TestMqttPublisherCallbacks`: 4 passing tests — on_connect sets flag + re-subscribes, on_disconnect clears flag, on_message queues message
- `TestMqttPublisherZmqStatus`: 2 passing tests — ZMQ PUB sends "cloud" topic with correct state after connect/disconnect
- 8 `xfail` stubs for CLOUD-02/03/06/07 (Plans 22-02 and 22-03)

## Test Results

```
20 passed, 8 xfailed in 0.25s
```

All CLOUD-01 and CLOUD-08 tests pass. Remaining stubs are xfail until Plans 02/03.

## Verification

- `uv run python -c "from ems_cloud_manager.config import load_cloud_config; print('config OK')"` — passes
- `uv run python -c "from ems_cloud_manager.publisher import MqttPublisher; print('publisher OK')"` — passes
- `uv run python -c "from ems_common.ipc import SOCK_CLOUD_PUB, TOPIC_CLOUD; print(SOCK_CLOUD_PUB)"` — prints `ipc:///run/ems/cloud_pub.sock`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed ZMQ socket role in test_connection_status_zmq**
- **Found during:** Task 2 GREEN phase
- **Issue:** Test initially bound SUB socket to a port, then tried to bind PUB socket to the same address — ZMQ error "Address already in use". PUB should bind, SUB should connect.
- **Fix:** Changed test to use `_find_free_port()` to pick a free port, bind PUB (publisher) to it, then SUB connects to that address. Matches the production pattern (HMI connects to cloud_manager's bound PUB).
- **Files modified:** `tests/test_cloud_manager.py`
- **Commit:** 9eacd98

## Commits

| Hash | Message |
|------|---------|
| f92e70f | feat(22-01): config loader, paho-mqtt dep, ipc SOCK_CLOUD_PUB |
| 1fcb163 | test(22-01): add cloud_manager test scaffold with config tests and xfail stubs |
| 9eacd98 | feat(22-01): MqttPublisher with mTLS, VERSION2 callbacks, ZMQ cloud status |

## Self-Check: PASSED

- [x] src/cloud_manager/src/ems_cloud_manager/config.py — FOUND
- [x] src/cloud_manager/src/ems_cloud_manager/publisher.py — FOUND
- [x] tests/test_cloud_manager.py — FOUND
- [x] Commit f92e70f — FOUND
- [x] Commit 1fcb163 — FOUND
- [x] Commit 9eacd98 — FOUND
