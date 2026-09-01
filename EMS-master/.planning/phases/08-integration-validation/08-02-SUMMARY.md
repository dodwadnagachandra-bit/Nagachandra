---
phase: 08-integration-validation
plan: 02
subsystem: integration
tags: [sim-all, integration-tests, ci, launcher, fault-injection]
dependency_graph:
  requires: [08-01]
  provides: [sim-all-launcher, integration-test-suite, ci-integration-job]
  affects: [tools/sim-all.sh, tests/test_integration.py, .github/workflows/pr-check.yml, Makefile]
tech_stack:
  added: []
  patterns: [subprocess-sim-testing, nullmodem-modbus-testing, rtdb-gpio-testing]
key_files:
  created:
    - tools/sim-all.sh
    - tests/test_integration.py
  modified:
    - Makefile
    - .gitignore
    - pyproject.toml
    - .github/workflows/pr-check.yml
decisions:
  - "Modbus integration tests use in-process NULLMODEM_HOST (not subprocess) because ModbusSimulator binds to pymodbus internal transport"
  - "GPIO harness is RTDB-backed stateless -- no daemon launched in sim-all.sh (RTDB created on demand)"
metrics:
  duration: "~4 min"
  completed: "2026-03-13"
---

# Phase 8 Plan 2: Sim-All Launcher, Integration Tests, and CI Summary

Unified sim-all.sh launcher with PID tracking and health checks, 6 integration smoke tests (3 basic ops + 3 fault injection), and CI integration-test job gating PRs.

## Changes Made

### Task 1: sim-all.sh Launcher and Makefile Target (commit 34c0afa)

Created `tools/sim-all.sh` -- a bash script that launches all simulators with:

- **Argument parsing**: `--profile` (residential/commercial/container), `--tcp-port` (default 5020), `--verbose`
- **Config validation**: verifies profile directory exists, lists available profiles on error
- **vcan0 setup**: loads vcan/can/can_raw kernel modules, creates and brings up vcan0
- **PID tracking**: stores background process PIDs in array, cleanup trap on EXIT/INT/TERM
- **Health checks**: `wait_for_health` polls each sim with 5s timeout (CAN via ip link, Modbus via ss port check)
- **GPIO note**: RTDB-backed, stateless -- no daemon needed (backend creates shm on demand)
- **Status summary**: prints running PIDs, profile, log paths, blocks with `wait`

Added `sim-all` Makefile target with `PROFILE` variable support. Added `logs/` to .gitignore.

### Task 2: Integration Tests and CI Job (commit 3b04804)

Created `tests/test_integration.py` with 6 tests under `@pytest.mark.integration`:

**Basic operations (3 tests):**
- `test_can_sends_frames`: CAN sim subprocess on vcan0, reads one extended frame via python-can
- `test_modbus_read_write`: In-process Modbus sim via NULLMODEM_HOST, reads 0x500E and writes 0x0291
- `test_gpio_set_and_read`: RtdbBackend with unique shm name, sets/reads DI-0

**Fault injection (3 tests):**
- `test_can_fault_injection`: Temp config with frame_drop_rate=1.0, verifies no frames received
- `test_modbus_fault_injection`: Temp config with exception_registers=[1], verifies error response on 0x0001, success on 0x0007
- `test_gpio_fault_injection`: RtdbBackend with stuck_pins=[3], verifies pin 3 ignores writes

**CI and pytest config:**
- Added `integration` marker to pyproject.toml
- Updated build-and-test job: `-m "not integration"` excludes integration tests from fast unit run
- Added `integration-test` job: `needs: build-and-test`, sets up vcan0, runs `pytest -m integration`

## Verification

- `bash -n tools/sim-all.sh` -- syntax check passes
- `uv run pytest tests/test_integration.py --co -m integration` -- 6 tests collected
- `grep "integration-test" .github/workflows/pr-check.yml` -- CI job exists
- `grep "not integration" .github/workflows/pr-check.yml` -- unit tests exclude integration
- `uv run pytest tests/ -v -m "not integration"` -- 111 passed, 1 failed (pre-existing), 1 skipped

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Modbus sim uses NULLMODEM_HOST, not real TCP socket**
- **Found during:** Task 2
- **Issue:** ModbusSimulator binds to pymodbus internal `NULLMODEM_HOST` transport, not a real TCP socket. Subprocess-based Modbus tests cannot connect via localhost.
- **Fix:** Used in-process Modbus sim with NULLMODEM_HOST client (same pattern as existing test_modbus_simulator.py tests). All Modbus operations exercised identically.
- **Files modified:** tests/test_integration.py

**2. [Rule 3 - Blocking] GPIO daemon requires --backend gpio-sim, not usable for RTDB**
- **Found during:** Task 1
- **Issue:** GPIO harness daemon subcommand only works with gpio-sim backend. RTDB backend is stateless (creates shm on demand).
- **Fix:** sim-all.sh skips GPIO daemon launch, documents RTDB as stateless. Integration tests use RtdbBackend directly.
- **Files modified:** tools/sim-all.sh

### Out-of-Scope Issues

- `test_x_unit_on_numeric_fields` failing -- fault_injection schema fields from 08-01 missing x-unit annotations. Pre-existing, not caused by 08-02 changes.

## Decisions Made

1. **In-process Modbus testing**: ModbusSimulator uses pymodbus NULLMODEM_HOST transport (not real TCP socket). Integration tests use same in-process pattern as unit tests for reliable cross-platform operation.
2. **GPIO stateless in sim-all.sh**: RTDB backend creates shared memory on demand. No background daemon needed -- integration tests interact with RtdbBackend directly.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 34c0afa | Create sim-all.sh unified launcher with PID tracking and Makefile target |
| 2 | 3b04804 | Add integration smoke tests (6 tests) and CI integration-test job |
