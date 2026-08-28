---
phase: 26-diagnostics
plan: "01"
subsystem: diagnostics
tags: [ipc, config, schema, tdd, foundation]
dependency_graph:
  requires: []
  provides:
    - SOCK_DIAGNOSTICS_CMD constant in ems_common.ipc
    - SOCK_DIAGNOSTICS_PUB constant in ems_common.ipc
    - TOPIC_DIAGNOSTICS constant in ems_common.ipc
    - diagnostics_config.yaml default config
    - diagnostics_config.schema.json JSON Schema (Draft 2020-12)
    - load_diagnostics_config() typed config loader
    - DiagnosticsConfig dataclass with all sub-configs
  affects:
    - All subsequent diagnostics plans (26-02, 26-03, 26-04) depend on these constants and config
tech_stack:
  added:
    - duckdb>=1.5.0 (ems-diagnostics dependency)
    - jsonschema>=4.26.0 (ems-diagnostics dependency)
    - msgpack>=1.1.2 (ems-diagnostics dependency)
    - pyyaml>=6.0.3 (ems-diagnostics dependency)
    - pyzmq>=27.1.0 (ems-diagnostics dependency)
  patterns:
    - TDD red/green/refactor cycle per task
    - yaml.safe_load + Draft202012Validator (matches config_manager pattern)
    - Nested dataclasses for typed config tree
key_files:
  created:
    - src/common/python/src/ems_common/ipc.py (modified — 3 new constants)
    - config/diagnostics_config.yaml
    - config/schemas/diagnostics_config.schema.json
    - src/diagnostics/src/ems_diagnostics/config.py
    - src/diagnostics/tests/__init__.py
    - src/diagnostics/tests/test_ipc_and_schema.py
    - src/diagnostics/tests/test_config.py
  modified:
    - src/diagnostics/pyproject.toml (5 new dependencies)
    - uv.lock (updated)
decisions:
  - "Used Draft202012Validator directly for validation consistency with the project's existing schema draft"
  - "Path discovery in tests uses _find_repo_root() walking up to [tool.uv.workspace] pyproject.toml — avoids hardcoded relative paths"
  - "DiagnosticsConfig uses nested dataclasses (not a single flat class) to mirror the YAML structure and allow per-section access"
metrics:
  duration: "3m19s"
  tasks_completed: 2
  tasks_total: 2
  files_created: 7
  files_modified: 2
  tests_added: 22
  completed_date: "2026-03-15"
---

# Phase 26 Plan 01: Diagnostics Foundation Summary

**One-liner:** IPC socket constants and topic for diagnostics, YAML config with Draft 2020-12 JSON Schema, and typed DiagnosticsConfig dataclass loader with 22 tests.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | IPC constants and config schema | a0aac69 | ipc.py, diagnostics_config.yaml, diagnostics_config.schema.json, test_ipc_and_schema.py |
| 2 | Config loader and tests | b5532f0 | config.py, test_config.py, pyproject.toml, uv.lock |

## What Was Built

### Task 1: IPC Constants and Config Schema

Three new constants added to `src/common/python/src/ems_common/ipc.py`:
- `SOCK_DIAGNOSTICS_CMD = "ipc:///run/ems/diagnostics_cmd.sock"` — REQ/REP command socket
- `SOCK_DIAGNOSTICS_PUB = "ipc:///run/ems/diagnostics_pub.sock"` — PUB for diagnostic results
- `TOPIC_DIAGNOSTICS = "diagnostics"` — topic string for SUB filtering

`config/diagnostics_config.yaml` created with five sections: `intervals` (publish_s=60, trend_update_s=3600), `thresholds` (5 alert thresholds), `prediction` (7-day min history, 30-day window), `battery` (rated_capacity_ah=100.0), `metrics` (5 enable flags).

`config/schemas/diagnostics_config.schema.json` (Draft 2020-12) with `additionalProperties: false` on all objects, typed properties, and min/max constraints.

### Task 2: Config Loader and Tests

`src/diagnostics/src/ems_diagnostics/config.py` implements:
- Five nested dataclasses: `IntervalsConfig`, `ThresholdsConfig`, `PredictionConfig`, `BatteryConfig`, `MetricsConfig`
- Root `DiagnosticsConfig` dataclass
- `load_diagnostics_config(config_path, schema_dir)` — loads YAML, validates with `Draft202012Validator`, returns typed dataclass
- Explicit `FileNotFoundError` for missing config or schema, propagates `yaml.YAMLError` and `jsonschema.ValidationError`

`src/diagnostics/pyproject.toml` updated with 5 production dependencies.

## Verification Results

```
uv run pytest src/diagnostics/tests/ -q
22 passed in 0.09s

uv run python -c "from ems_common.ipc import SOCK_DIAGNOSTICS_CMD, SOCK_DIAGNOSTICS_PUB, TOPIC_DIAGNOSTICS; print('IPC OK')"
IPC OK
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed repo-root path discovery in test files**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** The test used a hardcoded relative `../../../../../` traversal which resolved to `/home` instead of the repo root because the tests directory is inside `src/diagnostics/tests/`
- **Fix:** Added `_find_repo_root()` helper that walks up searching for `[tool.uv.workspace]` in `pyproject.toml`
- **Files modified:** `test_ipc_and_schema.py`, `test_config.py`
- **Commit:** a0aac69

## Self-Check: PASSED

All 7 created/modified files exist on disk. Both task commits (a0aac69, b5532f0) verified in git log. 22 tests pass.
