---
phase: 12-logging
plan: "06"
subsystem: logger
tags: [entry-point, systemd, integration-tests, asyncio]
dependency_graph:
  requires: ["12-02", "12-03", "12-04", "12-05"]
  provides: ["logger-service", "logger-integration-tests"]
  affects: ["deploy/systemd"]
tech_stack:
  added: []
  patterns: ["asyncio-gather-with-signal-shutdown", "graceful-config-fallback"]
key_files:
  created:
    - src/logger/python/src/ems_logger/__main__.py
    - src/logger/python/tests/test_logger_integration.py
  modified:
    - deploy/systemd/logger.service
key_decisions:
  - "Logger gracefully falls back to default LoggerConfig when config file missing"
  - "Topology loaded from system_config.yaml with default fallback (residential profile)"
  - "4 asyncio tasks managed via create_task with SIGTERM/SIGINT cancellation"
  - "Integration tests use tcp://127.0.0.1 random ports for ZMQ isolation"
metrics:
  duration_s: 182
  completed: "2026-03-14T10:32:21Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 4
  tests_total: 64
  files_created: 2
  files_modified: 1
requirements:
  - LOG-01
  - LOG-02
  - LOG-03
  - LOG-04
  - LOG-05
  - LOG-06
  - LOG-07
  - LOG-08
  - LOG-09
---

# Phase 12 Plan 06: Logger Entry Point, Service, and Integration Tests Summary

Logger entry point wiring TelemetryWriter + EventConsumer + QueryServer + RetentionManager as 4 concurrent asyncio tasks with SIGTERM/SIGINT graceful shutdown and end-to-end integration tests proving the full pipeline.

## What Was Built

### Task 1: Logger __main__.py entry point (212 lines)

Created `src/logger/python/src/ems_logger/__main__.py` following the data_manager pattern:

- CLI arg parsing (`--config` path to logger_config.yaml)
- LoggerConfig loading with graceful fallback to defaults
- Topology loading from system_config.yaml (residential defaults if missing)
- Shared zmq.asyncio.Context
- RetentionManager.startup_recovery() synchronous call (clean stale .tmp)
- 4 asyncio tasks: TelemetryWriter.run(), EventConsumer.run(), QueryServer.run(), RetentionManager.run_periodic()
- SIGTERM/SIGINT signal handlers cancel all tasks
- Graceful shutdown: close all components, flush files, term ZMQ context

Updated `deploy/systemd/logger.service`:
- Removed C++ subprocess comment
- Added `After=ems-data-manager.service comm_manager.service`
- Added `Wants=ems-data-manager.service`
- Added `Environment=EMS_CONFIG_DIR=/opt/ems/config`
- Kept `Restart=on-failure`, `RestartSec=5`

### Task 2: Integration tests (439 lines)

Created `src/logger/python/tests/test_logger_integration.py` with 4 end-to-end tests:

1. **test_telemetry_to_parquet_to_query**: ZMQ PUB -> TelemetryWriter -> Parquet files with Snappy compression in correct YYYY/MM/DD directory -> DuckDB query_time_series returns matching data
2. **test_events_to_jsonl_to_query**: ZMQ PUSH -> EventConsumer -> JSONL files -> query_event_log with severity and source filters returns correct subsets
3. **test_crash_recovery_on_startup**: Creates .tmp files, runs startup_recovery(), verifies .tmp deleted and .parquet untouched
4. **test_query_server_round_trip**: Writes Parquet directly, starts QueryServer on tcp:// REP, sends ZMQ REQ with time_series query, verifies response values match

## Verification

- Logger __main__.py syntax validated (ast.parse)
- systemd service has correct After= dependencies
- All 64 logger tests pass (4 integration + 60 unit)
- Full test suite: `uv run pytest src/logger/python/tests/ -x -v` -- 64 passed

## Deviations from Plan

None -- plan executed exactly as written.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | f59b1d4 | feat(12-06): logger entry point with asyncio lifecycle and systemd service |
| 2 | 893042d | test(12-06): integration tests for end-to-end logger pipeline |

## Self-Check: PASSED
