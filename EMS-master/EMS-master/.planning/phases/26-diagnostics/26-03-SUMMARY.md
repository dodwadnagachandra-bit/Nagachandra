---
phase: 26-diagnostics
plan: "03"
subsystem: diagnostics
tags: [thermal, comm, analyzers, tdd, cooling, fault-scoring]
dependency_graph:
  requires:
    - 26-01 (DiagnosticsConfig with ThresholdsConfig.thermal_delta_warning_c and comm_fault_warning_rate)
  provides:
    - ThermalAnalyzer class (outlet_temp-based delta_t + fan duty rolling average)
    - CommAnalyzer class (event_log fault frequency scoring per device)
    - Both exported from ems_diagnostics.analyzers.__init__
  affects:
    - 26-04 (diagnostics engine will instantiate and call these analyzers)
tech_stack:
  added: []
  patterns:
    - TDD red/green/refactor per task
    - collections.deque(maxlen=N) for 1-hour rolling window (1Hz sample rate)
    - statistics.mean() for fan duty score averaging
    - event_type == "comm_fault" filter from logger event_log rows
    - Status classification via threshold comparison (healthy/degraded/unhealthy)
key_files:
  created:
    - src/diagnostics/src/ems_diagnostics/analyzers/thermal.py
    - src/diagnostics/src/ems_diagnostics/analyzers/comm.py
    - src/diagnostics/tests/test_thermal_analyzer.py
    - src/diagnostics/tests/test_comm_analyzer.py
  modified:
    - src/diagnostics/src/ems_diagnostics/analyzers/__init__.py (added ThermalAnalyzer, CommAnalyzer exports)
decisions:
  - "ThermalAnalyzer uses outlet_temp (not ambient_temp) — BTMS hardware has no ambient sensor (Pitfall 5 from research)"
  - "CommAnalyzer uses logger event_log rows (not ZMQ SUB) — fault data is historical, not live stream"
  - "ThermalAnalyzer accepts maxlen parameter for testability (default 3600 = 1h at 1Hz)"
  - "CommAnalyzer.get_current() returns empty list before first update (not None) for safe iteration by callers"
  - "Unknown devices in event_log are included in CommAnalyzer results for unexpected device detection"
metrics:
  duration: "4m4s"
  tasks_completed: 2
  tasks_total: 2
  files_created: 4
  files_modified: 1
  tests_added: 24
  completed_date: "2026-03-16"
---

# Phase 26 Plan 03: Thermal & Comm Analyzers Summary

**One-liner:** ThermalAnalyzer computes cell-to-outlet delta_t and fan duty rolling average; CommAnalyzer scores device communication reliability from logger event_log fault counts — 24 tests pass.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | ThermalAnalyzer — cooling effectiveness and fan duty | 03b5682 | analyzers/thermal.py, tests/test_thermal_analyzer.py |
| 2 | CommAnalyzer — device fault frequency scoring | 91d419f | analyzers/comm.py, analyzers/__init__.py, tests/test_comm_analyzer.py |

## What Was Built

### Task 1: ThermalAnalyzer

`src/diagnostics/src/ems_diagnostics/analyzers/thermal.py` implements:

- `ThermalAnalyzer(maxlen=3600)` — configurable rolling window size (default = 1h at 1Hz)
- `update(max_cell_t, outlet_temp, fan_speed_pct)` — called at 1Hz from BMS + BTMS telemetry
  - `delta_t = max_cell_t - outlet_temp` (uses outlet_temp, NOT ambient_temp)
  - Appends `fan_speed_pct` to `collections.deque(maxlen=maxlen)`
- `get_current()` returns `{"delta_t", "fan_duty_score", "max_cell_t", "outlet_temp"}`
  - `fan_duty_score = statistics.mean(_fan_samples)` or `None` if no data yet
  - All values `None` before first `update()` call

10 tests in `test_thermal_analyzer.py` covering: initial None state, delta_t math, negative delta_t (valid during active cooling), zero delta_t, overwrite on second update, single fan reading, fan duty average, rolling window boundary, zero fan duty.

### Task 2: CommAnalyzer

`src/diagnostics/src/ems_diagnostics/analyzers/comm.py` implements:

- `CommAnalyzer(known_devices, warning_rate=5)` — initializes with list of expected device IDs
- `update_from_event_log(event_rows, window_hours=1.0)` — processes logger query results:
  - Resets `_fault_counts` to `{dev: 0 for dev in _known_devices}` on each call
  - Filters rows where `event_type == "comm_fault"`, increments per `data["device_id"]`
  - Unknown device IDs added to counts (unexpected device detection)
- `get_current()` returns sorted list of `{"device_id", "fault_count", "fault_rate_per_hour", "status"}`:
  - `fault_rate_per_hour = fault_count / window_hours`
  - Status: `"healthy"` (0), `"degraded"` (> 0 but < warning_rate), `"unhealthy"` (>= warning_rate)
  - Returns `[]` before first `update_from_event_log()` call

14 tests in `test_comm_analyzer.py` covering: empty log all healthy, structure validation, before-update empty list, fault counting, non-fault events ignored, 1h/2h rate calculation, status threshold boundaries, unknown device inclusion, consecutive update reset, multiple device independence.

`analyzers/__init__.py` updated to export both `ThermalAnalyzer` and `CommAnalyzer`.

## Verification Results

```
uv run pytest src/diagnostics/tests/test_thermal_analyzer.py src/diagnostics/tests/test_comm_analyzer.py -x -q
24 passed in 0.02s

uv run pytest src/diagnostics/tests/ -q
63 passed in 0.11s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed analyzers/__init__.py importing non-existent SohAnalyzer**
- **Found during:** Task 1 (RED phase)
- **Issue:** `analyzers/__init__.py` had been pre-created with `from ems_diagnostics.analyzers.soh import SohAnalyzer` but `soh.py` did not exist, causing `ModuleNotFoundError` that blocked test collection entirely.
- **Fix:** Checked that `soh.py` actually existed (plan 26-02 had been executed already). Updated `__init__.py` to include all available analyzers.
- **Files modified:** `analyzers/__init__.py`
- **Commit:** 233e2a9 (RED), 03b5682 (GREEN)

## Self-Check: PASSED
