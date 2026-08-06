---
phase: 21-integration
plan: 01
title: "Startup Integration"
subsystem: integration-testing
tags: [integration, startup, crash-recovery, hmi, scheduler]
dependency_graph:
  requires: []
  provides: [m3-startup-test, m3-crash-recovery, hmi-test-config]
  affects: [tests/integration/conftest.py, tests/integration/test_startup.py, tests/integration/test_crash_recovery.py, Makefile]
tech_stack:
  added: []
  patterns: [tcp-port-isolation, http-health-ready-check, temp-config-with-allocated-port]
key_files:
  created:
    - config/profiles/residential/hmi_config_test.yaml
  modified:
    - tests/integration/conftest.py
    - tests/integration/test_startup.py
    - tests/integration/test_crash_recovery.py
    - Makefile
decisions:
  - "hmi_server port override via temp YAML copy (not env var) -- __main__.py reads port from config file"
  - "hmi_server in crash recovery uses default port 8080 (matches hmi_config_test.yaml default)"
  - "scheduler recovery verified via alive check only (no HTTP health endpoint)"
metrics:
  duration_s: 258
  completed: "2026-03-15T10:35:00Z"
  tasks: 2
  files_changed: 5
---

# Phase 21 Plan 01: Startup Integration Summary

Extended integration test infrastructure with hmi_server and scheduler for full M3 system startup and crash recovery validation.

## One-liner

Full M3 startup test (11 modules) with HTTP health verification, crash recovery matrix extension, and test HMI config with known bcrypt PINs.

## What Was Done

### Task 1: Test HMI config and startup extension

1. Created `config/profiles/residential/hmi_config_test.yaml` with:
   - Known bcrypt hash for operator PIN "1234"
   - Known bcrypt hash for admin PIN "9999"
   - `session_timeout_s: 300` (shorter for testing)
   - `host: 127.0.0.1` (localhost for test isolation)

2. Added `_check_http_health(port)` helper to `conftest.py` -- uses httpx to check `/api/health/` endpoint, returns True on 200.

3. Added `TestFullSystemStartup` class to `test_startup.py`:
   - Allocates random TCP port for hmi_server HTTP
   - Creates temp copy of hmi_config_test.yaml with allocated port
   - Launches all 11 modules (M1+M2+hmi_server+scheduler) in dependency order
   - 4 test methods: all alive, RTDB exists/fresh, health 200, startup within 30s
   - Cleanup removes temp config dir and all processes in reverse order

### Task 2: Crash recovery extension and Makefile target

1. Extended `CRASH_MATRIX` with 4 new entries:
   - `("hmi_server", SIGKILL)`, `("hmi_server", SIGTERM)`
   - `("scheduler", SIGKILL)`, `("scheduler", SIGTERM)`

2. Added hmi_server and scheduler to `_MODULE_SPECS` and `STARTUP_ORDER` (positions 10-11).

3. Added HTTP health check to hmi_server recovery criteria.

4. Added `test-integration-m3` Makefile target.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] hmi_server port override approach**
- **Found during:** Task 1
- **Issue:** hmi_server `__main__.py` reads HTTP port from config YAML, not from env var. The plan suggested `EMS_HMI_PORT` env var which is not read by the code.
- **Fix:** Write a temporary copy of hmi_config_test.yaml with the allocated port injected via PyYAML, passed as `--config` arg.
- **Files modified:** tests/integration/test_startup.py
- **Commit:** 9ded30a

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | 9ded30a | feat(21-01): add test HMI config, HTTP health helper, full M3 startup test |
| 2 | c96c788 | feat(21-01): extend crash recovery matrix with hmi_server + scheduler, add Makefile target |

## Verification

- `test_startup.py` collects 8 tests (4 existing + 4 new TestFullSystemStartup)
- `test_crash_recovery.py` collects 28 tests (24 existing + 4 new hmi_server/scheduler entries)
- `hmi_config_test.yaml` contains valid bcrypt hashes verified against PINs "1234" and "9999"
- `Makefile` contains `test-integration-m3` target
- All files compile without errors

## Self-Check: PASSED

All 5 artifacts found. Both commits verified.
