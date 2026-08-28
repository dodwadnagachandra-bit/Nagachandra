---
phase: 20-scheduler
plan: 01
subsystem: scheduler
tags: [python, tdd, config-loader, evaluator, pure-functions, jsonschema]
dependency_graph:
  requires: [config/schedule_config.yaml, config/schemas/schedule_config.schema.json, ems_common.ipc]
  provides: [ems_scheduler.config.load_schedule_config, ems_scheduler.evaluator.evaluate_time_of_day, ems_scheduler.evaluator.evaluate_curve, ems_scheduler.evaluator.evaluate_day_night, SOCK_SCHEDULER_PUB, TOPIC_SCHEDULE]
  affects: [20-02-PLAN, 20-03-PLAN]
tech_stack:
  added: [pyzmq, msgpack, pyyaml, jsonschema]
  patterns: [JSON Schema validation with Draft202012Validator, pure function evaluators, dataclass results, TDD red-green]
key_files:
  created:
    - src/scheduler/src/ems_scheduler/config.py
    - src/scheduler/src/ems_scheduler/evaluator.py
    - src/scheduler/tests/__init__.py
    - src/scheduler/tests/conftest.py
    - src/scheduler/tests/test_config.py
    - src/scheduler/tests/test_evaluator.py
  modified:
    - src/common/python/src/ems_common/ipc.py
    - src/scheduler/pyproject.toml
decisions:
  - "Config loader mirrors control_manager pattern exactly (load_schedule_config vs load_control_config)"
  - "Evaluator functions are pure -- no ZMQ, no async, take time+config args, return frozen dataclasses"
  - "Charge sign convention: charge action negates power_kw (negative=charge), discharge keeps positive"
  - "Time window matching uses half-open intervals [start, end) with midnight wrapping support"
metrics:
  duration: 145s
  completed: 2026-03-15
  tasks: 1
  tests: 20
  files_created: 6
  files_modified: 2
---

# Phase 20 Plan 01: Config Loader, IPC Constants, and Evaluator Functions Summary

Config loader with JSON Schema validation, IPC constants (SOCK_SCHEDULER_PUB, TOPIC_SCHEDULE), and pure evaluator functions for time_of_day windows, 96-point curve index, and day/night mode detection -- 20 TDD tests covering midnight wrapping, boundary conditions, and signed power convention.

## What Was Built

### IPC Constants
- `SOCK_SCHEDULER_PUB = "ipc:///run/ems/scheduler_pub.sock"` added to `ems_common/ipc.py`
- `TOPIC_SCHEDULE = "schedule"` added to `ems_common/ipc.py`

### Config Loader (`config.py`)
- `load_schedule_config(path, schema_path)` -- loads YAML, validates against JSON Schema with `Draft202012Validator`
- Mirrors `load_control_config` pattern from control_manager
- Raises `FileNotFoundError` for missing files, `ValueError` for schema violations

### Evaluator Pure Functions (`evaluator.py`)
- `parse_time("HH:MM")` -- returns `(hour, minute)` tuple
- `evaluate_time_of_day(hour, minute, windows)` -- matches current time against windows, returns `WindowResult` with signed power
- `evaluate_curve(hour, minute, power_curve)` -- calculates 96-point index (`hour*4 + minute//15`), returns `CurveResult`
- `evaluate_day_night(hour, minute, day_night)` -- returns `"day"` or `"night"` based on configured thresholds
- Two frozen dataclasses: `WindowResult` and `CurveResult`

### Sign Convention
- Discharge: positive power_kw
- Charge: negative power_kw (evaluator negates the unsigned `power_kw` from config when action is "charge")
- Idle: 0

### Test Coverage (20 tests)
- Config: valid load, missing file, invalid schema, missing schema (4 tests)
- parse_time: morning, late night (2 tests)
- time_of_day: in-window, outside, midnight wrap (both sides), start inclusive, end exclusive, first-match-wins (7 tests)
- curve: midnight index, midday index, end-of-day index, signed values (4 tests)
- day/night: daytime, nighttime, boundary conditions (3 tests)

## Deviations from Plan

None -- plan executed exactly as written.

## Commits

| Hash | Message |
|------|---------|
| 2bca706 | test(20-01): add failing tests for config loader and evaluator |
| 1e317cc | feat(20-01): implement config loader, evaluator functions, and IPC constants |
