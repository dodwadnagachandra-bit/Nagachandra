---
phase: 09-foundation
plan: 04
subsystem: data_manager
tags: [zmq, telemetry, health-monitor, snapshot, systemd, python, ipc]
dependency_graph:
  requires: [09-02]
  provides: [zmq-telemetry-publisher, health-monitor, rtdb-snapshot, systemd-units]
  affects: [logger, hmi_server, cloud_manager, control_manager]
tech_stack:
  added: [pyzmq, msgpack]
  patterns: [seqlock-read-memmove, zmq-pub-multipart, atomic-write-rename, asyncio-gather]
key_files:
  created:
    - src/data_manager/python/src/ems_data_manager/publisher.py
    - src/data_manager/python/src/ems_data_manager/health.py
    - src/data_manager/python/src/ems_data_manager/snapshot.py
    - src/data_manager/python/src/ems_data_manager/__main__.py
    - deploy/systemd/ems-data-manager.service
    - deploy/systemd/ems-data-manager-python.service
  modified:
    - src/data_manager/python/pyproject.toml
    - tests/test_data_manager.py
decisions:
  - "TelemetryPublisher uses sync ZMQ PUB (not async) with publish_once() for testability; async publish_loop wraps it"
  - "Health monitor binds its own PUB socket (separate from publisher) to allow independent endpoint configuration"
  - "Snapshot uses ctypes.memmove to copy full RTDB buffer to bytes, then atomic write (tmp+rename+fsync)"
  - "Tests use tcp://127.0.0.1 endpoints instead of ipc:// to avoid requiring /run/ems directory"
metrics:
  duration: "11m 33s"
  completed: "2026-03-13"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 12
  tests_passed: 12
requirements: [DATA-05, DATA-06, DATA-07, DATA-08]
---

# Phase 9 Plan 4: Data Manager Python Service Summary

1Hz ZMQ telemetry publisher with seqlock-based RTDB reads, stale section health monitor, periodic disk snapshots with atomic write, and split systemd units for C/Python processes.

## What Was Built

### TelemetryPublisher (publisher.py)

- Reads all RTDB sections (BMS racks, PCS, GPIO, meter, BTMS, system) via seqlock pattern
- Uses `ctypes.memmove` to minimize time in critical section (copy entire section, then extract fields)
- Publishes multipart ZMQ messages: `[topic_string, msgpack_envelope]`
- BMS rack topics: `bms.rack.{cluster}.{rack}` (e.g., `bms.rack.0.3`)
- Non-BMS topics use `TOPIC_PCS`, `TOPIC_GPIO`, etc. from `ems_common.ipc`
- Incrementing sequence number per message, monotonic timestamp

### HealthMonitor (health.py)

- Checks each RTDB section's `last_update_ms` against `CLOCK_MONOTONIC` at 1Hz
- Publishes stale warnings on topic `system.health` when `(now_ms - last_update_ms) > threshold`
- Zero `last_update_ms` treated as "no data received yet" -- no false warnings (per locked decision)
- Default threshold: 5000ms (configurable via CLI)
- Monitors both BMS racks and non-BMS sections

### SnapshotManager (snapshot.py)

- Dumps full RTDB buffer (1,800,744 bytes) to disk at configurable interval (default 60s)
- Atomic write: writes to `.tmp` file, `fsync`, then `os.rename` to final path
- Retains last 10 snapshots, prunes oldest automatically
- `dump_on_safety_event()` for immediate snapshots triggered by safety events

### Service Entry Point (__main__.py)

- Parses CLI args: `--snapshot-dir`, `--snapshot-interval`, `--stale-threshold`
- Attaches to C-owned RTDB via `attach_rtdb()`
- Runs publisher, health monitor, and snapshot loops via `asyncio.gather`
- Handles SIGTERM/SIGINT for graceful shutdown (detach RTDB, close ZMQ)

### systemd Service Files

- `ems-data-manager.service`: C RTDB owner, Type=simple, ExecStop removes /dev/shm/ems_rtdb
- `ems-data-manager-python.service`: After=ems-data-manager.service + Requires= for startup ordering

## Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| C lifecycle (TestCLibrary) | 4 | All pass |
| Python helpers (TestPythonHelpers) | 6 | All pass |
| Telemetry publisher (TestTelemetryPublisher) | 4 | All pass |
| Health monitor (TestHealthMonitor) | 2 | All pass |
| Snapshot (TestSnapshot) | 3 | All pass |
| systemd units (TestSystemdUnits) | 3 | All pass |
| **Total** | **22** | **All pass** |

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 5ee92b0 | test | TDD RED: failing tests for publisher, health, snapshot, systemd |
| d559227 | feat | TDD GREEN: ZMQ telemetry publisher and health monitor |
| 97d58d3 | feat | TDD GREEN: snapshot manager, systemd units, service entry point |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] BufferError from ctypes/shm reference ordering in tests**
- **Found during:** Task 1 GREEN phase
- **Issue:** Tests failed with `BufferError: cannot close exported pointers exist` because publisher/monitor/snapshot objects hold rtdb references that prevent shm.close()
- **Fix:** Added `del publisher`/`del monitor`/`del snap` before `del rtdb` and `detach_rtdb(shm)` in all test cleanup sections
- **Files modified:** tests/test_data_manager.py
- **Commit:** d559227, 97d58d3

**2. [Rule 3 - Blocking] IPC socket path requires /run/ems directory**
- **Found during:** Task 1 GREEN phase
- **Issue:** Tests using default `ipc:///run/ems/telemetry.sock` endpoint hung because /run/ems doesn't exist in test environment
- **Fix:** Added endpoint parameter to TelemetryPublisher and HealthMonitor; tests use `tcp://127.0.0.1:{port}` instead
- **Files modified:** tests/test_data_manager.py
- **Commit:** d559227

### Out-of-Scope Issues

- `test_config_hot_reload.py::TestHotReloadBackup::test_backup_created_before_apply` fails (KeyError: 'control') -- pre-existing config_manager issue
- `test_config_validation.py::test_x_unit_on_numeric_fields` fails -- pre-existing schema annotation issue

## Self-Check: PASSED
