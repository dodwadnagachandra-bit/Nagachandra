---
phase: 09-foundation
plan: 03
subsystem: config_manager
tags: [config, hot-reload, zmq, inotify, backup, diff, ipc]
dependency_graph:
  requires: [ConfigManager, ems-common-ipc]
  provides: [SOCK_CONFIG, ConfigWatcher, ZMQ-config-API, hot-reload, config-backup]
  affects: [all-modules-querying-config, logger-events]
tech_stack:
  added: [pyzmq, inotify-simple, pytest-asyncio]
  patterns: [inotify-debounce, zmq-req-rep, recursive-dict-diff, backup-rotation]
key_files:
  created:
    - src/config_manager/src/ems_config_manager/watcher.py
    - src/config_manager/src/ems_config_manager/__main__.py
    - tests/test_config_watcher.py
    - tests/test_config_hot_reload.py
  modified:
    - src/config_manager/src/ems_config_manager/manager.py
    - src/config_manager/pyproject.toml
    - src/common/python/src/ems_common/ipc.py
    - src/common/c/include/ipc_defs.h
    - tests/test_ipc_contracts.py
decisions:
  - "ConfigWatcher uses asyncio add_reader (non-blocking) instead of executor-based blocking inotify.read for clean cancellation"
  - "Hot-reload backup uses millisecond timestamps for uniqueness across rapid reloads"
  - "serve_queries accepts optional bind_addr for testability (defaults to SOCK_CONFIG)"
  - "_init_push_socket accepts optional push_addr for testability (defaults to SOCK_LOGGER)"
metrics:
  duration_seconds: 761
  completed: "2026-03-13"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 20
  files_created: 4
  files_modified: 5
---

# Phase 9 Plan 03: Config Manager Hot-Reload and ZMQ API Summary

Hot-reload via inotify with 500ms debounce for control/alarms/schedule configs, ZMQ REQ/REP config query API from in-memory cache, backup rotation (keep 5), recursive diff in reload events, asyncio service entry point.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | IPC contract update and inotify hot-reload watcher | 963db84 | ipc.py, ipc_defs.h, watcher.py, test_config_watcher.py |
| 2 | ZMQ config API, backup, diff, and service entry point | 0229050 | manager.py, __main__.py, test_config_hot_reload.py |

## What Was Built

### Task 1: SOCK_CONFIG IPC Contract and ConfigWatcher
- Added `SOCK_CONFIG = "ipc:///run/ems/config.sock"` to both Python `ipc.py` and C `ipc_defs.h`
- Added `pyzmq>=26.0`, `inotify-simple>=1.3`, `msgpack>=1.0` dependencies to config_manager
- Created `ConfigWatcher` class with:
  - `HOT_RELOAD_CONFIGS = {"control_config", "alarms_config", "schedule_config"}`
  - `DEBOUNCE_S = 0.5` (500ms debounce after last IN_CLOSE_WRITE)
  - Uses `asyncio.add_reader()` for non-blocking inotify fd watching
  - Filters: skips dot-prefixed files, non-yaml files, and configs not in HOT_RELOAD_CONFIGS
  - system_config changes logged as warning but not hot-reloaded (topology requires restart)
- 9 tests covering inotify detection, filtering, debounce, and config set validation
- Updated IPC contract tests to include SOCK_CONFIG in uniqueness checks

### Task 2: ZMQ Config API, Hot-Reload, and Service
- **serve_queries()**: ZMQ REP socket handles `get_config` (full dict) and `get_value` (dotted path). Unknown actions return error. Served from in-memory cache.
- **handle_reload()**: (a) Creates backup in config/backups/{name}.{timestamp}.yaml, (b) loads and validates new YAML against JSON Schema, (c) computes recursive diff (changed/added/removed with old/new values), (d) swaps cache atomically, (e) publishes config_reload event via ZMQ PUSH with full config + diff
- **Failed reload**: Validation errors reject the change, keep current running config in cache, and publish config_reload_failed error event with validation details
- **Backup rotation**: Keeps only last 5 backups per config name, deletes oldest on overflow
- **__main__.py**: Parses --config-dir, --schema-dir, --profile (or EMS_PROFILE env). Runs asyncio.gather with serve_queries + watch_loop. Handles SIGTERM/SIGINT for graceful shutdown.
- 11 tests covering ZMQ get_config, get_value, missing path error, unknown action error, backup creation, backup cleanup, reload event with diff, failed reload rejection, diff accuracy, and service entry point

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed inotify blocking in asyncio tests**
- **Found during:** Task 1
- **Issue:** Original implementation used `loop.run_in_executor(None, inotify.read)` which blocked executor thread indefinitely on cancellation, causing test hangs
- **Fix:** Switched to `asyncio.add_reader()` on inotify fd for non-blocking event-driven reading with clean cancellation support
- **Files modified:** src/config_manager/src/ems_config_manager/watcher.py
- **Commit:** 963db84

**2. [Rule 1 - Bug] Fixed test data using wrong config field names**
- **Found during:** Task 2
- **Issue:** Tests referenced `control.dispatch_interval_s` which does not exist in control_config.yaml (actual field is `soc_limits.charge_cutoff_pct`)
- **Fix:** Updated all test references to use correct field paths from the actual config schema
- **Files modified:** tests/test_config_hot_reload.py
- **Commit:** 0229050

**3. [Rule 2 - Missing] Added pytest-asyncio dev dependency**
- **Found during:** Task 1
- **Issue:** Async test markers required pytest-asyncio which was not installed
- **Fix:** Added pytest-asyncio as dev dependency via `uv add --dev pytest-asyncio`
- **Files modified:** pyproject.toml, uv.lock
- **Commit:** 963db84

### Pre-existing Issues (Out of Scope)

1. **test_x_unit_on_numeric_fields**: 6 numeric fields in bms_config and pcs_config missing x-unit annotation. Pre-existing from M0.

## Verification

- Config watcher tests: 9/9 pass (`tests/test_config_watcher.py`)
- Hot-reload + ZMQ API tests: 11/11 pass (`tests/test_config_hot_reload.py`)
- Existing ConfigManager tests: 15/15 pass (`tests/test_config_manager.py`)
- IPC contract tests: 10/10 pass (`tests/test_ipc_contracts.py`)
- Full suite: 80 passed, 1 pre-existing failure (x-unit)
