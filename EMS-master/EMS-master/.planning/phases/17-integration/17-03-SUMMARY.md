---
phase: 17-integration
plan: "03"
subsystem: integration-tests
tags: [integration, hot-reload, control_manager, alarm_manager, inotify, config_manager, ctrl-11, alm-09]
dependency_graph:
  requires: [17-02, 16-02]
  provides: [CTRL-11, ALM-09]
  affects: [tests/integration/test_m2_integration.py]
tech_stack:
  added: []
  patterns:
    - "Atomic config modify: yaml.safe_load + dot-path traversal + yaml.dump to .tmp + os.rename"
    - "Per-test restore_configs autouse fixture: write original text back, sleep 0.3s for inotify settle"
    - "Class-scoped fixture saves original config text before any test runs"
    - "get_active_alarms polling: send_alarm_command REQ loop within wait_for_criteria"
key_files:
  created: []
  modified:
    - tests/integration/test_m2_integration.py
decisions:
  - "modify_config_atomic uses os.rename (not shutil.move) — os.rename is atomic on same-filesystem, triggers single IN_MOVED_TO inotify event"
  - "Alarm threshold reload test waits up to 10s to account for 5s default delay_ms in alarms_config"
  - "Module-level helpers (_read_total_soc, _read_pcs_reg) preferred over instance methods — TestHotReload shares same RTDB/Modbus patterns as TestDispatchFlow"
  - "restore_configs autouse fixture writes original file text directly (not modify_config_atomic) — avoids triggering a second inotify chain during teardown"
metrics:
  duration: "4 minutes"
  completed_date: "2026-03-15"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 1
---

# Phase 17 Plan 03: Hot-Reload Integration Tests Summary

**One-liner:** Config hot-reload graduation tests — atomic YAML write + inotify chain validates CTRL-11 (control config live reload) and ALM-09 (alarm config live reload) without module restart.

## What Was Built

Appended `TestHotReload` class (4 test methods, ~330 lines) and supporting module-level helpers to `tests/integration/test_m2_integration.py`. The class proves the full hot-reload chain: `os.rename` atomic write -> inotify `IN_MOVED_TO` -> `config_manager` validation -> ZMQ `config_reload` PUB event -> module re-reads disk -> applies on next tick.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add TestHotReload class to test_m2_integration.py | 3c01c20 | tests/integration/test_m2_integration.py |

## Key Changes

### Module-level helpers added

- `modify_config_atomic(path, updates)`: reads YAML, traverses dot-separated key paths, writes to `.tmp`, calls `os.rename` for atomic single-event inotify trigger
- `send_alarm_command(endpoint, cmd, params)`: ZMQ REQ to alarm_manager REP socket with 3s timeout
- `_read_total_soc()`: attaches RTDB, reads `system.total_soc`, detaches
- `_read_pcs_reg(address)`: connects ModbusTcpClient to port 502, reads holding register

### TestHotReload class

**Fixture `reload_system` (class-scoped):**
- Allocates 8 random TCP ports (separate port set from TestProtectionFlow/TestDispatchFlow)
- Copies residential profile to tmpdir — same pattern as other test classes
- Saves `control_config.yaml` and `alarms_config.yaml` original text as `original_configs` dict
- Launches full 11-module stack with env var endpoint overrides
- Teardown restores original configs in `finally` block before process cleanup

**Fixture `restore_configs` (autouse, function-scoped):**
- Yields, then writes original text back after each test
- Sleeps 0.3s for inotify to settle before next test

**`test_control_config_discharge_cutoff_reload` (CTRL-11):**
- Transitions to DISCHARGING (SOC ~50%, > 15% guard)
- `modify_config_atomic`: `soc_limits.discharge_cutoff_pct` 10 -> 90
- `time.sleep(2.0)` + `wait_for_criteria(STATE_IDLE, 5s)` — SOC 50% < new 90% cutoff -> IDLE
- Verifies PCS 0x500E == 0 after transition

**`test_alarm_config_threshold_reload` (ALM-09):**
- `modify_config_atomic`: `rules.cell_voltage_high.high_threshold` 3.65 -> 3.00
- Polls `get_active_alarms` via ZMQ REQ up to 10s (covers 5s alarm delay + reload time)
- Asserts `cell_voltage_high` present in active list

**`test_alarm_config_disable_reload` (ALM-09):**
- Lowers threshold to 3.00 (step 1), waits for alarm to become active (skip if not)
- `modify_config_atomic`: sets `enabled=False` atomically with threshold still at 3.00
- `time.sleep(2.0)` + `wait_for_criteria` up to 5s — alarm no longer in active list

**`test_control_config_power_limit_reload` (CTRL-11):**
- Transitions to DISCHARGING, waits 6s for ramp to approach max (250 = 25 kW * 10)
- `modify_config_atomic`: `power_limits.max_discharge_kw` 25 -> 15
- `time.sleep(2.0)` + `wait_for_criteria(pcs <= 170, 5s)` — verifies register drops to ~150

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- FOUND: tests/integration/test_m2_integration.py (TestHotReload class with 4 test methods)
- FOUND: `modify_config_atomic` function using `os.rename` atomic pattern
- FOUND: `restore_configs` autouse fixture for inter-test config restoration
- FOUND: TestProtectionFlow and TestDispatchFlow still present (not accidentally overwritten)
- FOUND commit 3c01c20 in git log
