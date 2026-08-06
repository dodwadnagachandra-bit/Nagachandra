---
phase: 26-diagnostics
plan: "02"
subsystem: diagnostics
tags: [soh, pcs, analyzers, tdd, cycle-detection, efficiency]
dependency_graph:
  requires:
    - DiagnosticsConfig dataclass from 26-01 (rated_capacity_ah)
    - ZMQ bms.rack.{N} telemetry payload fields (pack_i, pack_soc, pack_soh_bms)
    - ZMQ pcs telemetry payload fields (active_power, dc_voltage, dc_current)
  provides:
    - SohAnalyzer class (analyzers/soh.py) — per-rack BMS SOH trending + cycle detection
    - PcsAnalyzer class (analyzers/pcs.py) — AC/DC efficiency calculation
    - ems_diagnostics.analyzers package __init__ exporting both
  affects:
    - 26-03 and 26-04 depend on these analyzers for integration into the main diagnostics loop
tech_stack:
  added: []
  patterns:
    - TDD red/green/refactor cycle per task
    - Three-state machine (IDLE/CHARGED/DISCHARGING) for cycle detection (no double-counting)
    - Coulomb counting at 1Hz for cross-validation against rated capacity
    - collections.deque with maxlen for O(1) rolling average window (3600 samples = 1h)
    - Idle-threshold guard before efficiency calculation to skip noise
key_files:
  created:
    - src/diagnostics/src/ems_diagnostics/analyzers/__init__.py
    - src/diagnostics/src/ems_diagnostics/analyzers/soh.py
    - src/diagnostics/src/ems_diagnostics/analyzers/pcs.py
    - src/diagnostics/tests/test_soh_analyzer.py
    - src/diagnostics/tests/test_pcs_analyzer.py
  modified: []
decisions:
  - "BMS-reported pack_soh used as primary SOH value — coulomb counting is cross-validation only (Research Pitfall 4)"
  - "Cycle state machine requires explicit discharge current check (pack_i < -0.5A) before CHARGED->DISCHARGING transition to prevent false cycles on passive SOC drop"
  - "Coulomb accumulation starts at transition sample (not the next sample) to avoid under-counting"
  - "PCS idle threshold applied to BOTH ac_power and dc_power independently — avoids false efficiency values when one side is near zero"
  - "Efficiency clamped to [0, 100] — measurement noise can cause ratios slightly above 1.0"
metrics:
  duration: "215s"
  tasks_completed: 2
  tasks_total: 2
  files_created: 5
  files_modified: 0
  tests_added: 17
  completed_date: "2026-03-16"
---

# Phase 26 Plan 02: SOH and PCS Analyzers Summary

**One-liner:** SohAnalyzer with three-state cycle detection and coulomb cross-validation, PcsAnalyzer with idle-skip and rolling-average efficiency, backed by 17 unit tests.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | SohAnalyzer — BMS SOH trending and cycle detection | c5b27a8 | analyzers/soh.py, test_soh_analyzer.py |
| 2 | PcsAnalyzer — AC/DC efficiency calculation | 1f693b7 | analyzers/pcs.py, test_pcs_analyzer.py, analyzers/__init__.py |

## What Was Built

### Task 1: SohAnalyzer

`src/diagnostics/src/ems_diagnostics/analyzers/soh.py` implements:

- `SohAnalyzer(rack_id, rated_capacity_ah)` — one instance per rack
- `update(pack_i, pack_soc, pack_soh_bms)` — sets `_soh_pct = pack_soh_bms` (BMS is authoritative) and advances the state machine
- Three-state machine (`_CycleState.IDLE / CHARGED / DISCHARGING`):
  - IDLE → CHARGED when `pack_soc >= 90%`
  - CHARGED → DISCHARGING when `pack_i < -0.5A` (confirming actual discharge current)
  - DISCHARGING → IDLE when `pack_soc <= 10%` (cycle complete, count increments)
- Coulomb counting: `_cycle_ah += abs(pack_i) / 3600.0` per 1-Hz sample during DISCHARGING
- On cycle completion: `coulomb_soh_pct = (_cycle_ah / rated_capacity_ah) * 100`
- `get_current()` → `{rack_id, soh_pct, cycle_count, coulomb_soh_pct}`
- `add_history_point(timestamp_ms)` / `get_history()` for trend queries

8 unit tests covering: initial state, BMS SOH primacy, full cycle detection, no double-counting on SOC oscillation, coulomb accumulation accuracy, dict structure, history tracking, and no-discharge guard.

### Task 2: PcsAnalyzer

`src/diagnostics/src/ems_diagnostics/analyzers/pcs.py` implements:

- `PcsAnalyzer()` — single global PCS tracker
- `update(active_power, dc_voltage, dc_current)` computes `dc_power = dc_voltage * dc_current`
- Idle guard: skips if `abs(active_power) < 500W` or `abs(dc_power) < 500W`
- Discharge (`active_power > 0`): `efficiency = abs(ac_power) / abs(dc_power) * 100`
- Charge (`active_power < 0`): `efficiency = abs(dc_power) / abs(ac_power) * 100`
- Clamped to `[0.0, 100.0]`
- Rolling deque `maxlen=3600` for 1-hour average at 1 Hz
- `get_current()` → `{efficiency_pct, sample_count, avg_efficiency_pct}`

`analyzers/__init__.py` updated to export both `SohAnalyzer` and `PcsAnalyzer`.

9 unit tests covering: idle at zero, low power below threshold, discharge 95%, charge 95%, charge clamped to 100%, rolling average, dict structure, idle no sample count, AC-only above threshold.

## Verification Results

```
uv run pytest src/diagnostics/tests/test_soh_analyzer.py src/diagnostics/tests/test_pcs_analyzer.py -x -q
17 passed in 0.02s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Coulomb accumulation missing for transition sample**
- **Found during:** Task 1 (GREEN phase, test_coulomb_counting_during_discharge)
- **Issue:** The CHARGED→DISCHARGING transition reset `_cycle_ah` but did not accumulate the current for that sample, causing the accumulator to be one sample short of expected value
- **Fix:** Added `self._cycle_ah += abs(pack_i) / 3600.0` immediately after state transition in the CHARGED branch
- **Files modified:** `analyzers/soh.py`
- **Commit:** c5b27a8

## Self-Check: PASSED

All 5 created files exist on disk. Both task commits (c5b27a8, 1f693b7) verified in git log. 17 tests pass.
