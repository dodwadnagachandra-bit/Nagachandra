---
phase: 29-hardware-validation
plan: "01"
subsystem: hardware-validation
tags: [pytest, ssh, can, rs485, modbus, gpio, hdmi, ethernet, bash]
dependency_graph:
  requires: []
  provides: [tests/hw, tools/hw-validation/stage1-boot.sh, tools/hw-validation/stage2-drivers.sh]
  affects: [pyproject.toml]
tech_stack:
  added: []
  patterns: [pytest-ssh-to-ECU, modbus-xfail-no-simulator, gpio-safe-do-only]
key_files:
  created:
    - tests/hw/__init__.py
    - tests/hw/conftest.py
    - tests/hw/test_boot.py
    - tests/hw/test_drivers.py
    - tools/hw-validation/stage1-boot.sh
    - tools/hw-validation/stage2-drivers.sh
  modified:
    - pyproject.toml
decisions:
  - "ecu_ssh() is a plain helper function (not a pytest fixture) so tests can call it multiple times without fixture overhead"
  - "Modbus poll tests use pytest.xfail (not skip) when no simulator — distinguishes UART-present-but-no-response from UART-absent"
  - "GPIO DO tests restricted to DO-3 RUNNING_LAMP and DO-7 SPARE_DO7; safety-critical outputs DO-0, DO-1, DO-5 never actuated"
  - "hw and requires_simulator markers registered in pyproject.toml to eliminate collection warnings"
metrics:
  duration: "5m26s"
  completed_date: "2026-03-16"
  tasks_completed: 2
  files_created: 6
  files_modified: 1
---

# Phase 29 Plan 01: Hardware Validation Test Infrastructure Summary

**One-liner:** pytest hw package with SSH-to-ECU fixtures, 22-test Stage 1-2 suite, and standalone bash scripts for boot and driver validation of ECU-1170-552A.

## What Was Built

### tests/hw/ — pytest Hardware Validation Package

**conftest.py** — shared fixtures and constants:
- `ECU_IP`, `ECU_USER`, `DATA_DIR`, `EMS_VENV` constants (all env-var overridable)
- `SERVICES` list of all 14 EMS runtime services (matches deploy/systemd/)
- `ecu_ssh(command)` — plain helper (not fixture) running SSH commands on the ECU
- `ecu_reachable` — session-scoped fixture that calls `pytest.skip()` when ECU is unreachable via ping, enabling `uv run pytest tests/hw/` to gracefully report skipped when no hardware is connected

**test_boot.py** — Stage 1: 4 tests
- `test_all_services_active` — all 14 services report 'active'; collects ALL failures before asserting
- `test_boot_time_within_60s` — parses systemd-analyze output; xfails if tool unavailable
- `test_ems_target_active` — ems.target aggregate health check
- `test_no_failed_services` — systemctl list-units --state=failed filtered to EMS services

**test_drivers.py** — Stage 2: 18 tests across 5 classes
- `TestCAN` (4): can0/can1 existence, can0 bring-up at 250 kbps, can0 loopback TX/RX
- `TestRS485` (5): 4 UART existence tests + 1 Modbus RTU poll test (xfail without simulator)
- `TestGPIO` (4): gpiodetect, gpioinfo line count, DI-0..7 read, safe DO write
- `TestNetwork` (3): ≥2 Ethernet interfaces, ETH0 link UP, WAN ping (xfail in isolated lab)
- `TestHDMI` (1): DRM subsystem presence

### tools/hw-validation/ — Standalone Bash Scripts

**stage1-boot.sh** — loops all 14 services with 60s total timeout; PASS/FAIL per service; checks ems.target; exits 1 on any failure. Based on RESEARCH.md Pattern 2.

**stage2-drivers.sh** — RS485 section has two distinct steps per port (UART-exists then Modbus-poll); summary line distinguishes hardware checks from communication checks; SKIP shown for Modbus when no simulator connected.

## Decisions Made

1. **ecu_ssh() is a plain function, not a fixture** — tests call it multiple times per function body without fixture protocol overhead. The `ecu_reachable` session fixture handles the ECU connectivity skip gate.

2. **Modbus poll tests use xfail, not skip** — `pytest.xfail("No simulator on /dev/ttySN")` leaves a distinct record in the test report vs skip. CI with no hardware shows xfail (expected failure) rather than skip, communicating "test ran but no device responded."

3. **GPIO DO test restricted to safe outputs only** — DO-3 (RUNNING_LAMP) and DO-7 (SPARE_DO7) are the only safe non-safety-critical DO lines. DO-0 (ACDB_TRIP), DO-1 (EXTINGUISHER), DO-5 (PCS_STOP) are never asserted. Offsets assume DI-0..7 → lines 0..7, DO-0..7 → lines 8..15; must be verified against ECU DTB on first boot.

4. **pytest markers registered in pyproject.toml** — `hw` and `requires_simulator` markers added to eliminate `PytestUnknownMarkWarning` during collection. This was an auto-fix (Rule 2 — missing for correctness).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Config] Registered hw and requires_simulator pytest markers**
- **Found during:** Task 2 verification (pytest collect-only)
- **Issue:** `pytest.mark.hw` and `pytest.mark.requires_simulator` used in test files but not registered in `pyproject.toml`, causing `PytestUnknownMarkWarning` on every collection and preventing `-m hw` marker filtering in CI
- **Fix:** Added two marker entries to `[tool.pytest.ini_options] markers` in pyproject.toml
- **Files modified:** `pyproject.toml`
- **Commit:** 948bafb

## Self-Check

### Files Exist

- [x] tests/hw/__init__.py — exists
- [x] tests/hw/conftest.py — exists (65+ lines)
- [x] tests/hw/test_boot.py — exists (4 test functions)
- [x] tests/hw/test_drivers.py — exists (18 test functions, 5 classes)
- [x] tools/hw-validation/stage1-boot.sh — exists, executable
- [x] tools/hw-validation/stage2-drivers.sh — exists, executable

### Commits Exist

- [x] b7ebdb8 — Task 1: hw infrastructure + Stage 1 boot validation
- [x] 948bafb — Task 2: Stage 2 driver tests + bash script + pyproject markers

### Verification Commands Passed

- [x] `uv run python -c "import tests.hw; import tests.hw.conftest; import tests.hw.test_boot; print('imports OK')"` — imports OK
- [x] `uv run python -c "import tests.hw.test_drivers; print('driver tests import OK')"` — driver tests import OK
- [x] `test -x tools/hw-validation/stage1-boot.sh && test -x tools/hw-validation/stage2-drivers.sh` — both executable
- [x] `uv run pytest tests/hw/ --collect-only` — 22 tests collected, no warnings

## Self-Check: PASSED
