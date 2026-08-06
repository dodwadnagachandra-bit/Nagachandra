---
phase: 09-foundation
plan: 01
subsystem: config_manager
tags: [config, validation, schema, overlay, cli]
dependency_graph:
  requires: []
  provides: [ConfigManager, ems-config-cli, schema-version-validation, profile-overlay]
  affects: [all-14-yaml-configs, all-14-json-schemas, config-profiles]
tech_stack:
  added: []
  patterns: [fail-fast-validation, full-file-replacement-overlay, dotted-path-access, schema-version-const]
key_files:
  created:
    - src/config_manager/src/ems_config_manager/manager.py
    - src/config_manager/src/ems_config_manager/overlay.py
    - src/config_manager/src/ems_config_manager/cli.py
    - tests/test_config_manager.py
    - tests/test_schema_version.py
  modified:
    - config/schemas/system_config.schema.json
    - config/schemas/*.schema.json (all 14)
    - config/*.yaml (all 14 active)
    - config/profiles/*/*.yaml (all 42 profile configs)
    - src/config_manager/pyproject.toml
    - src/config_manager/src/ems_config_manager/__init__.py
    - tests/test_config_validation.py
decisions:
  - "Profile overlay uses full file replacement (not deep merge) -- deterministic and matches file structure"
  - "_schema_version field is a required const string at root of every config"
  - "subsystems object in system_config is optional (schema defaults handle missing fields)"
  - "ConfigManager.get_value uses KeyError with descriptive message including available keys"
  - "CLI uses argparse subcommands (validate) for extensibility"
metrics:
  duration_seconds: 588
  completed: "2026-03-13"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 92
  files_created: 5
  files_modified: 72
---

# Phase 9 Plan 01: Config Manager Core Summary

ConfigManager with fail-fast startup validation, schema version checking (const "1.0"), profile overlay (full file replacement), dotted-path cache access, and ems-config validate CLI.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Schema version fields and subsystem presence flags | 9cf62bf | 14 schemas, 56 YAMLs, tests/test_schema_version.py |
| 2 | ConfigManager core class with profile overlay and CLI | dd677de | manager.py, overlay.py, cli.py, tests/test_config_manager.py |

## What Was Built

### Task 1: Schema Version and Subsystem Flags
- Added `_schema_version: "1.0"` as a required const field to all 14 JSON Schemas
- Added `subsystems` object to system_config.schema.json with `has_dg`, `has_pv`, `has_btms`, `has_meter` boolean fields
- Updated all 56 YAML files (14 active + 14x3 profiles) with `_schema_version: "1.0"`
- Added subsystems section to all 4 system_config.yaml files with profile-appropriate defaults (residential: dg=false, pv=false; commercial/container: all true)
- 77 tests covering schema version validation and subsystem presence

### Task 2: ConfigManager Core
- **ConfigManager.load_all()**: Loads system_config first for subsystem flags, then core configs, then optional device configs. Validates every file against JSON Schema with Draft202012Validator. Checks _schema_version matches schema const before validation. Fail-fast on any error (sys.exit(1)).
- **ConfigManager.get_config(name)**: Returns full parsed dict from in-memory cache. Raises KeyError with loaded-config list if not found.
- **ConfigManager.get_value(name, path)**: Navigates dotted path (e.g., "connection.protocol"). Raises KeyError with available keys at the failed level.
- **Profile overlay (overlay.py)**: Full file replacement -- if profile file exists at config_dir/profiles/{profile}/{name}.yaml, loads that instead. Falls back to default otherwise.
- **CLI (cli.py)**: `ems-config validate <file|dir>` subcommand. Single file returns OK/FAIL with errors. Directory validates all 14 files with summary.
- 15 tests covering all behaviors: load_all valid/invalid/missing, get_config, get_value, schema mismatch, overlay, CLI exit codes.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed inline test data in test_config_validation.py**
- **Found during:** Task 1
- **Issue:** 7 existing tests constructed inline YAML data without the new required `_schema_version` field, causing validation failures
- **Fix:** Added `"_schema_version": "1.0"` to all inline test data dicts
- **Files modified:** tests/test_config_validation.py
- **Commit:** 9cf62bf (bundled with Task 1 in prior execution)

### Pre-existing Issues (Out of Scope)

1. **test_x_unit_on_numeric_fields**: 6 numeric fields in bms_config and pcs_config missing x-unit annotation (fault_injection.frame_drop_rate, etc.). Pre-existing from M0 simulator work.
2. **test_modbus_simulator**: Requires pyserial which is not installed. Pre-existing.

## Verification

- All 14 configs validate: `uv run python tools/validate_config.py` -- PASS
- All 3 profiles validate -- PASS
- ConfigManager tests: 15/15 pass
- Schema version tests: 77/77 pass
- CLI works: `uv run ems-config validate config/system_config.yaml` returns "OK"
- Full test suite: 128 passed (excluding 1 pre-existing pyserial failure, 1 pre-existing x-unit failure)
