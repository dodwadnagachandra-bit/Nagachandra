---
phase: 15-alarm-engine
plan: "01"
subsystem: alarm_manager
tags: [alarm-manager, config, signal-resolver, rtdb, tdd, iec-62682]
dependency_graph:
  requires: []
  provides: [load_alarm_config, SignalResolver, AlarmInstance, build_alarm_instances]
  affects: [15-02, 15-03]
tech_stack:
  added: [jsonschema, yaml]
  patterns: [TDD, ctypes-in-process-mock, dict-callable-resolver]
key_files:
  created:
    - src/alarm_manager/src/ems_alarm_manager/config.py
    - src/alarm_manager/src/ems_alarm_manager/resolver.py
    - src/alarm_manager/src/ems_alarm_manager/evaluator.py
    - src/alarm_manager/tests/__init__.py
    - src/alarm_manager/tests/test_config.py
    - src/alarm_manager/tests/test_resolver.py
  modified: []
key_decisions:
  - load_alarm_config derives schema path from config parent dir (config/parent/../schemas/) matching control_manager pattern
  - SignalResolver uses module-level pure functions plus a dict for O(1) dispatch, avoiding class inheritance
  - AlarmInstance uses plain string constants (not Enum) for IEC 62682 states to match control_manager convention
  - hysteresis field in per-rule config is named "hysteresis" (not "hysteresis_pct") per schema — build_alarm_instances maps it correctly
metrics:
  duration_seconds: 156
  completed_date: "2026-03-15"
  tasks_completed: 2
  files_created: 6
  files_modified: 0
  tests_added: 16
  tests_passing: 16
---

# Phase 15 Plan 01: Alarm Engine Foundation Summary

**One-liner:** JSON Schema-validated alarm config loader, 7-path RTDB signal resolver with offline-rack exclusion, and IEC 62682 AlarmInstance dataclass with pre-computed hysteresis clear thresholds.

## Tasks Completed

| Task | Description | Commit | Tests |
|------|-------------|--------|-------|
| 1 (TDD) | Config loader + SignalResolver + tests | 7f61eed | 16/16 |
| 2 | AlarmInstance dataclass + lifecycle constants | c60846b | — (import verified) |

## What Was Built

### config.py — load_alarm_config()

Follows the exact pattern from `ems_control_manager/config.py`:
- Loads YAML with `yaml.safe_load`, raising `ValueError` on parse failure
- Validates with `jsonschema.Draft202012Validator` (Draft 2020-12)
- Raises `ValueError` (not `jsonschema.ValidationError`) for consistent caller interface
- Schema path derived from `path.parent.parent / "schemas" / "alarms_config.schema.json"` when not provided
- Confirms 9 alarm rules present in valid config

### resolver.py — SignalResolver

Maps 7 dotted signal paths to RTDB callables:
- `bms.cell_voltage_max/min` — max/min of `rack.max_cell_v`/`rack.min_cell_v` across online racks
- `bms.cell_temp_max/min` — max/min of `rack.max_cell_t`/`rack.min_cell_t` across online racks
- `bms.soc_pct` — mean of `rack.pack_soc` across online racks
- `bms.bus_voltage_v` — `rtdb.pcs.dc_voltage`
- `pcs.internal_temp_c` — `rtdb.pcs.temperature`

BMS aggregates iterate all `MAX_CLUSTERS * MAX_RACKS_PER_CLUSTER` (8×16=128) rack slots, filtering `rack.online != 0`. Returns `None` when no online racks found.

### evaluator.py — AlarmInstance + constants

Five IEC 62682 lifecycle state constants as plain strings (matching control_manager Enum-avoidance convention):
`STATE_NORMAL`, `STATE_ACTIVE_UNACKED`, `STATE_ACTIVE_ACKED`, `STATE_CLEARED_UNACKED`, `STATE_RTN`

`AlarmInstance` dataclass pre-computes clear thresholds in `__post_init__`:
- `high_clear = high_threshold - abs(high_threshold) * hysteresis_pct / 100`
- `low_clear = low_threshold + abs(low_threshold) * hysteresis_pct / 100`

`build_alarm_instances()` creates 9 instances from config with correct threshold/hysteresis/delay values and defaults fallback.

## Tests

16 tests in 2 test files, all passing. No warnings.

Test coverage:
- Valid config load (9 rules, "rules" and "defaults" keys)
- Missing file (FileNotFoundError)
- Invalid YAML (ValueError "invalid YAML")
- Schema violation (ValueError "validation failed")
- SignalResolver: all 7 paths exercised
- Offline rack exclusion (3.8V offline rack excluded, 3.4V online returned)
- All-offline scenario returns None
- SOC mean calculation (50% + 70% = 60.0%)
- resolve_all dict result
- validate_paths unknown path detection

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

Files created:
- src/alarm_manager/src/ems_alarm_manager/config.py — EXISTS
- src/alarm_manager/src/ems_alarm_manager/resolver.py — EXISTS
- src/alarm_manager/src/ems_alarm_manager/evaluator.py — EXISTS
- src/alarm_manager/tests/__init__.py — EXISTS
- src/alarm_manager/tests/test_config.py — EXISTS
- src/alarm_manager/tests/test_resolver.py — EXISTS

Commits:
- 7c5149f — test(15-01): RED tests
- 7f61eed — feat(15-01): GREEN implementation (config.py, resolver.py)
- c60846b — feat(15-01): evaluator.py (AlarmInstance + constants)
