---
phase: 13-integration
plan: "01"
subsystem: testing
tags: [integration-tests, fixtures, infrastructure]
dependency_graph:
  requires: []
  provides: [ModuleProcess, MetricsCollector, integration-conftest, test-integration-target]
  affects: [13-02, 13-03, 13-04, 13-05]
tech_stack:
  added: [psutil, pytest-timeout, pyzmq-dev-dep]
  patterns: [subprocess-process-group, psutil-rss-monitoring, zmq-health-check]
key_files:
  created:
    - tests/integration/__init__.py
    - tests/integration/conftest.py
  modified:
    - Makefile
    - pyproject.toml
    - uv.lock
decisions:
  - "pyzmq added as root dev dependency (was only in sub-packages, needed for ZMQ health check helpers)"
  - "ModuleProcess uses os.setpgrp for process group isolation, enabling killpg cleanup"
  - "MetricsCollector skips threshold checks when no data collected (prevents empty-list errors)"
  - "RSS growth check requires 120+ samples (2 minutes) before asserting"
metrics:
  duration: "2m 22s"
  completed: "2026-03-14"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 13 Plan 01: Integration Test Infrastructure Summary

Integration test infrastructure with ModuleProcess subprocess wrapper, MetricsCollector for 5 performance thresholds, and dual-topology profile selector.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add dev dependencies and Makefile target | 2c864b5 | pyproject.toml, uv.lock, Makefile, tests/integration/__init__.py |
| 2 | Create integration test conftest.py | fb9f3cd | tests/integration/conftest.py, pyproject.toml, uv.lock |

## Key Components Created

### ModuleProcess (class)
Subprocess wrapper with: start, kill (any signal), restart, terminate (SIGTERM with SIGKILL fallback), cleanup (process group kill). Properties: pid, rss_bytes (via psutil), is_alive. Accepts custom env dict and ready_check callable with 15s timeout.

### MetricsCollector (dataclass)
Accumulates: gpio_latencies_ms, rtdb_write_latencies_ms, zmq_lag_messages, rss_samples (per module), logger row counts. `assert_thresholds()` checks all 5 CONTEXT.md metrics: GPIO p99 < 100ms, RTDB write p99 < 10ms, ZMQ lag < 5 messages, logger write rate >= 95%, RSS growth < 10%.

### Fixtures
- `cleanup_shm` (autouse, function): removes /dev/shm/ems_rtdb before and after each test
- `cleanup_ipc_sockets` (autouse, function): removes /run/ems/*.sock in teardown
- `profile_name` / `profile` (session): --profile CLI option with residential/container choices
- `ensure_build` (session): skips all tests if C binaries not built
- `ensure_vcan` (session): skips CAN tests if vcan0 not available
- `ensure_run_ems` (session): best-effort /run/ems/ directory creation

### Helper Functions
- `check_rtdb_exists()`: SHM file existence check
- `check_rtdb_fresh(max_age_ms)`: attach RTDB, verify system.last_update_ms is recent
- `check_zmq_receiving(endpoint, topic, timeout_ms)`: SUB socket receive check
- `wait_for_criteria(checks, timeout, poll_interval)`: multi-check polling with deadline

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added pyzmq to root dev dependencies**
- **Found during:** Task 2
- **Issue:** pyzmq was only a dependency of sub-packages (ems_common, etc.), not the root workspace. Importing zmq in conftest.py failed with ModuleNotFoundError.
- **Fix:** `uv add --dev pyzmq` to add it as a root dev dependency
- **Files modified:** pyproject.toml, uv.lock
- **Commit:** fb9f3cd

## Verification Results

All checks passed:
- `import psutil; import pytest_timeout` -- deps ok
- `grep -q "test-integration" Makefile` -- makefile ok
- `test -f tests/integration/__init__.py` -- init ok
- `ast.parse(conftest.py)` -- syntax ok
- `from tests.integration.conftest import ModuleProcess, MetricsCollector, PROFILES, check_rtdb_exists, wait_for_criteria` -- imports ok

## Self-Check: PASSED
