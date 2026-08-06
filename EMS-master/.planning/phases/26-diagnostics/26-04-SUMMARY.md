---
phase: 26-diagnostics
plan: "04"
subsystem: diagnostics
tags: [loop, reporter, predictions, zmq, asyncio, linear-regression, tdd, entry-point]
dependency_graph:
  requires:
    - 26-01 (DiagnosticsConfig, IPC socket definitions)
    - 26-02 (SohAnalyzer, PcsAnalyzer)
    - 26-03 (ThermalAnalyzer, CommAnalyzer)
  provides:
    - ReportBuilder class (build_current, build_report, build_predictions)
    - DiagnosticsLoop class (5 async tasks, ZMQ wiring)
    - ems_diagnostics.__main__ entry point
  affects:
    - systemd deployment (diagnostics.service)
    - HMI/cloud consumers of SOCK_DIAGNOSTICS_PUB
tech_stack:
  added: []
  patterns:
    - TDD red/green/refactor for ReportBuilder
    - statistics.linear_regression for SOH trend projection
    - asyncio.gather with 5 concurrent tasks
    - recv_multipart for ZMQ PUB/SUB topic-frame protocol
    - asyncio.to_thread for DuckDB queries (non-blocking event loop)
    - LINGER=0 on all ZMQ sockets for clean shutdown
    - inproc:// ZMQ sockets for isolated test contexts
    - Non-blocking NOBLOCK recv polling in async tests (avoid event loop deadlock)
key_files:
  created:
    - src/diagnostics/src/ems_diagnostics/reporter.py
    - src/diagnostics/src/ems_diagnostics/loop.py
    - src/diagnostics/src/ems_diagnostics/__main__.py
    - src/diagnostics/tests/test_reporter.py
    - src/diagnostics/tests/test_loop.py
  modified: []
decisions:
  - "ZMQ PUB/SUB uses multipart frames (topic frame + msgpack frame) matching data_manager publisher pattern"
  - "Trend updater sleeps before first update (not after) so startup does not block executor thread"
  - "asyncio.Event created lazily in run() to bind to the running event loop (pytest-asyncio per-test loop)"
  - "Test ZMQ contexts use per-test Context + LINGER=0 sockets + explicit cleanup() + ctx.term() ordering"
  - "build_predictions skips racks with < min_history_days (returns empty list, not placeholder entry)"
  - "_classify_severity(None) returns 'info' — no projection is not an alert condition"
metrics:
  duration: "27m33s"
  tasks_completed: 3
  tasks_total: 3
  files_created: 5
  files_modified: 0
  tests_added: 27
  completed_date: "2026-03-16"
---

# Phase 26 Plan 04: DiagnosticsLoop, ReportBuilder, Entry Point Summary

**One-liner:** DiagnosticsLoop runs 5 async ZMQ tasks (telemetry collector, trend updater, diagnostics publisher, report server, predictive alert checker) with ReportBuilder providing SOH linear-regression projections — 90 total diagnostics tests pass.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | ReportBuilder — query dispatch and prediction engine | 9be6106 | reporter.py, tests/test_reporter.py |
| 2 | DiagnosticsLoop — 5 async tasks wiring analyzers to ZMQ | 5c501a9 | loop.py, tests/test_loop.py |
| 3 | Entry point and full module verification | acdf7f9 | __main__.py |

## What Was Built

### Task 1: ReportBuilder

`src/diagnostics/src/ems_diagnostics/reporter.py` implements:

- `ReportBuilder(config: DiagnosticsConfig)` — holds config for threshold/prediction settings
- `build_current(soh_analyzers, pcs, thermal, comm)` — aggregates all 4 analyzer snapshots into `{"soh": [...], "pcs_efficiency": {...}, "thermal": {...}, "comm": [...]}`
- `build_report(period, query_fn)` — dispatches 3 logger queries (range_stats for SOH, range_stats for PCS, event_log for comm faults) with 24h/168h windows
- `build_predictions(soh_analyzers, config)` — uses `statistics.linear_regression` on SOH history; projects `(soh_critical_pct - current_soh) / slope = days_to_threshold`; skips racks with < min_history_days data
- `_classify_severity(days)` — `< 30` → critical, `< 90` → warning, `>= 90` or None → info

19 tests in `test_reporter.py` covering all 3 query types, predictions with known linear data, severity thresholds.

### Task 2: DiagnosticsLoop

`src/diagnostics/src/ems_diagnostics/loop.py` implements:

**Socket setup:**
- SUB → connects to SOCK_TELEMETRY (subscribed to bms.rack, pcs, btms)
- REQ → connects to SOCK_LOGGER_QUERY (5s RCVTIMEO)
- PUSH → connects to SOCK_LOGGER
- PUB → binds to SOCK_DIAGNOSTICS_PUB
- REP → binds to SOCK_DIAGNOSTICS_CMD

**5 async tasks:**
1. `_telemetry_collector` — 100Hz NOBLOCK `recv_multipart` drain; routes topic frame to correct analyzer
2. `_hourly_trend_updater` — sleeps `trend_update_s` first, then runs in `asyncio.to_thread` to avoid blocking event loop
3. `_diagnostics_publisher` — `build_current()` → PUB send at `publish_s` interval with topic prefix
4. `_report_server` — 100Hz NOBLOCK drain of REP socket; dispatches get_current/get_report/get_predictions; ALWAYS sends reply even on error
5. `_predictive_alert_checker` — calls `build_predictions()` at `publish_s` interval; fires PUSH events when `days_to_threshold < 90`

**Key design decisions discovered during implementation:**
- ZMQ PUB/SUB uses multipart frames (topic + msgpack body) matching the existing `data_manager` publisher pattern
- Test ZMQ contexts require explicit `loop.cleanup()` before `ctx.term()` to close bound sockets
- Test async helpers must use NOBLOCK polling (not blocking recv) to avoid deadlocking the event loop

8 tests in `test_loop.py` using inproc:// sockets.

### Task 3: Entry Point

`src/diagnostics/src/ems_diagnostics/__main__.py`:
- `argparse` CLI with `--config` and `--log-level`
- `load_diagnostics_config()` → `DiagnosticsLoop(config)` → `loop.run()`
- `asyncio.get_running_loop().add_signal_handler(SIGTERM/SIGINT, loop.stop)`
- `loop.cleanup()` in `finally` block

## Verification Results

```
uv run pytest src/diagnostics/tests/ -v --timeout=10
90 passed in 8.17s

uv run python -c "from ems_diagnostics.loop import DiagnosticsLoop; from ems_diagnostics.reporter import ReportBuilder; print('OK')"
OK

uv run python -m ems_diagnostics --help
usage: ems_diagnostics [-h] [--config CONFIG] [--log-level ...]
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ZMQ PUB/SUB multipart frame format not used in loop**
- **Found during:** Task 2 (telemetry routing tests failing with 100.0 SOH)
- **Issue:** Loop's `_telemetry_collector` called `recv()` (single frame) but data_manager uses `send_multipart([topic_bytes, envelope_bytes])` — the entire message was being decoded as a telemetry envelope without separating the topic frame
- **Fix:** Changed `recv()` → `recv_multipart()`, updated `_route_telemetry(topic_bytes, payload_bytes)` signature, updated tests to use `send_multipart`
- **Files modified:** `loop.py`, `tests/test_loop.py`
- **Commit:** 5c501a9

**2. [Rule 2 - Missing functionality] ZMQ LINGER=0 not set on test helper sockets**
- **Found during:** Task 2 (test hang at ctx.term() after loop stopped)
- **Issue:** Test helper sockets (logger_rep, push_sink, tel_pub) used default ZMQ LINGER setting, causing `ctx.term()` to block indefinitely waiting for unsent messages
- **Fix:** Added `_mk_sock()` helper that sets LINGER=0; added `_cleanup()` helper that calls `loop.cleanup()` before closing helper sockets then `ctx.term()`
- **Files modified:** `tests/test_loop.py`
- **Commit:** 5c501a9

**3. [Rule 1 - Bug] Non-blocking recv required in async test helpers**
- **Found during:** Task 2 (test report_server tests deadlocking)
- **Issue:** `client.recv()` (blocking) inside an async function blocked the entire asyncio event loop, preventing the `_report_server` task from processing the request — deadlock
- **Fix:** Replaced blocking `recv()` with a polling loop using `recv(zmq.NOBLOCK)` + `await asyncio.sleep(0.01)`
- **Files modified:** `tests/test_loop.py`
- **Commit:** 5c501a9

**4. [Rule 1 - Bug] asyncio.Event lazy creation needed for pytest-asyncio compatibility**
- **Found during:** Task 2 (investigating hang pattern)
- **Issue:** `asyncio.Event()` created in `__init__` before the test's event loop starts; in pytest-asyncio STRICT mode with per-test loops, this could bind to the wrong loop
- **Fix:** Changed `_stop_event` to `None` initially; created lazily in `run()` within the running event loop; `stop()` guards against None
- **Files modified:** `loop.py`
- **Commit:** 5c501a9

## Self-Check: PASSED
