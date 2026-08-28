---
phase: 16-control-intelligence
plan: "01"
subsystem: control_manager
tags: [intelligence, source-priority, soc-limits, thermal-derating, power-ramping, interlocks, tdd]
dependency_graph:
  requires:
    - 14-02: ControlStateMachine (state constants, PCS state constants)
    - 14-03: ControlLoop (config loader, existing tests)
  provides:
    - ControlIntelligence class with evaluate() returning IntelligenceResult
    - Extended control_config schema with derating and ramping sections
  affects:
    - 16-02: ControlLoop wiring (calls evaluate() from _tick())
tech_stack:
  added: []
  patterns:
    - Pure logic class (no I/O) following ControlStateMachine pattern from Phase 14
    - Piecewise linear derating via module-level _zone_factor/_zone_factor_low helpers
    - time.monotonic() delta for ramp rate (actual elapsed, not configured interval)
    - Atomic config reference replacement on hot-reload (replace not mutate)
key_files:
  created:
    - src/control_manager/python/src/ems_control_manager/intelligence.py
    - src/control_manager/python/tests/test_intelligence.py
  modified:
    - config/schemas/control_config.schema.json
    - config/control_config.yaml
decisions:
  - "DG always False in M2 — availability dict hard-codes dg=False (no DG_AVAILABLE RTDB field)"
  - "Ramp direction determines ramp rate selection: charge_ramp_kw_s for upward, discharge_ramp_kw_s for downward"
  - "Action severity 50% reduction applied to raw_setpoint BEFORE ramping — ramp then limits change rate to adjusted target"
  - "First tick snaps to desired setpoint (no previous state) — _last_tick_s=None sentinel"
  - "_zone_factor/_zone_factor_low extracted to module-level pure functions for testability and reuse by Plan 16-02"
metrics:
  duration_minutes: 5
  tasks_completed: 2
  files_created: 2
  files_modified: 2
  tests_added: 77
  tests_total: 153
  completed_date: "2026-03-15"
---

# Phase 16 Plan 01: ControlIntelligence Pure Logic Class Summary

**One-liner:** ControlIntelligence pure logic class with source priority waterfall (DAY/NIGHT/MANUAL), SOC limits, piecewise linear 3-zone thermal derating, time-monotonic power ramping, and interlock guards; extended control_config schema with derating + ramping sections.

## What Was Built

A `ControlIntelligence` class (222 lines) that encapsulates all decision intelligence for the EMS control loop. It follows the same pure-logic pattern as `ControlStateMachine` from Phase 14 — no RTDB access, no ZMQ, no asyncio. `ControlLoop._tick()` (Plan 16-02) will call `evaluate()` once per 1Hz cycle.

### Files Created

**`src/control_manager/python/src/ems_control_manager/intelligence.py`**
- `IntelligenceResult` dataclass: 7 fields (effective_max_power_kw, desired_setpoint_kw, active_derating_pct, active_source, soc_cutoff_hit, interlock_blocked, protection_active)
- `ControlIntelligence` class with `__init__`, `update_config`, `evaluate`, and 5 private methods
- Module-level `_zone_factor` and `_zone_factor_low` pure helper functions for derating curves

**`src/control_manager/python/tests/test_intelligence.py`**
- 77 tests across 10 test classes
- `TestSourcePriorityDayMode` (7 tests): DAY waterfall, BESS FAULT exclusion, DG M2 behaviour
- `TestSourcePriorityNightMode` (4 tests): NIGHT waterfall, solar not checked
- `TestSourcePriorityManualMode` (3 tests): bypass all checks
- `TestSocLimits` (11 tests): exact boundary conditions for charge and discharge cutoffs
- `TestDeratingBmsHigh` (6 parametrized): start/midpoint/cutoff/above/below
- `TestDeratingPcsHigh` (5 parametrized): same curve shape, different thresholds
- `TestDeratingBmsLow` (6 parametrized): inverted low-side zone
- `TestDeratingMostRestrictive` (7 tests): multi-zone minimum, effective_max_power computation
- `TestPowerRamping` (7 tests): ramp limiting, elapsed time, no overshoot, negative direction
- `TestInterlockGuards` (6 tests): PCS states, safety emergency, both conditions
- `TestAlarmProtection` (7 tests): severity response, 50% action reduction, derating independence
- `TestConfigUpdate` (3 tests): hot-reload SOC limits, derating thresholds, ramping rate

### Files Modified

**`config/schemas/control_config.schema.json`**
- Added `"derating"` and `"ramping"` to top-level `required[]`
- `derating` object: 6 fields (bms_high_start_c, bms_high_cutoff_c, pcs_high_start_c, pcs_high_cutoff_c, bms_low_start_c, bms_low_cutoff_c), all `x-mutable: true`, `additionalProperties: false`
- `ramping` object: 2 fields (charge_ramp_kw_s, discharge_ramp_kw_s), `minimum: 0.1`, `x-mutable: true`

**`config/control_config.yaml`**
- Added `derating:` section with default values (BMS: 40/50°C high, 5/0°C cold, PCS: 65/80°C)
- Added `ramping:` section with default values (charge_ramp_kw_s: 5.0, discharge_ramp_kw_s: 5.0)

## Key Design Decisions

1. **DG always False in M2:** `availability["dg"] = False` hard-coded — no `dg_available` field exists in RTDB yet. Source selection waterfall skips DG in all modes.

2. **Ramp direction selects ramp rate:** Upward setpoint change uses `charge_ramp_kw_s`, downward uses `discharge_ramp_kw_s`. This allows asymmetric ramp tuning (e.g., fast charge ramp, slow discharge ramp).

3. **Action severity applies before ramping:** The 50% setpoint reduction from `alarm_severity="action"` is applied to `raw_setpoint_kw` before the ramp function runs. The ramp then limits how fast the adjusted setpoint is reached.

4. **First tick snaps:** `_last_tick_s = None` sentinel on construction. First call to `_apply_ramp()` sets baseline state and returns `desired_kw` directly — no ramp delay on startup.

5. **Module-level zone factor helpers:** `_zone_factor` and `_zone_factor_low` are module-level functions (not methods) for clean extractability and potential reuse in Plan 16-02 RTDB writes.

## Deviations from Plan

None — plan executed exactly as written.

The one test fix (action severity assertion expected 10.0 kW but got 15.0 kW) was a test logic error in the RED phase. The test was pre-warming at 20 kW and expected a 50% cut to 10 kW in one ramp step — but at 5 kW/s over 1s, only 5 kW of reduction is possible from 20 → 15 kW, not all the way to 10 kW. Fixed to pre-warm at 10 kW, where the 50% cut to 5 kW is achievable in one step (delta=5 kW = max_step=5 kW). No implementation changes required.

## Verification Results

```
Task 1: uv run --all-packages python -c "...assert 'derating' in c..."
Schema + config OK
derating: {'bms_high_start_c': 40, ...}

Task 2: uv run --all-packages pytest src/control_manager/python/tests/test_intelligence.py -x -q
77 passed in 0.07s

Final: uv run --all-packages pytest src/control_manager/python/tests -x -q
153 passed in 1.08s
```

## Self-Check: PASSED

All created files verified on disk:
- FOUND: `src/control_manager/python/src/ems_control_manager/intelligence.py`
- FOUND: `src/control_manager/python/tests/test_intelligence.py`
- FOUND: `config/schemas/control_config.schema.json` (modified)
- FOUND: `config/control_config.yaml` (modified)

All commits verified in git log:
- FOUND: `0922c11` feat(16-01): extend control_config schema with derating and ramping sections
- FOUND: `9cae834` test(16-01): add failing tests for ControlIntelligence class
- FOUND: `bc727db` feat(16-01): implement ControlIntelligence pure logic class
