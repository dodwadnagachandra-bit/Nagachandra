---
phase: 15-alarm-engine
plan: "02"
subsystem: alarm_manager
tags: [alarm-manager, evaluator, iec-62682, state-machine, hysteresis, delay-timer, tdd]
dependency_graph:
  requires: [15-01]
  provides: [AlarmEvaluator, evaluate_tick, acknowledge, get_active_alarms, get_alarm_config]
  affects: [15-03]
tech_stack:
  added: []
  patterns: [TDD, IEC-62682-lifecycle, hysteresis-band, delay-timer, pure-logic-evaluator]
key_files:
  created:
    - src/alarm_manager/tests/test_evaluator.py
  modified:
    - src/alarm_manager/src/ems_alarm_manager/evaluator.py
key_decisions:
  - RTN auto-transitions to NORMAL at the top of _evaluate_one before state dispatch — allows the same tick to re-evaluate as NORMAL (handles same-tick re-exceedance correctly)
  - delay_ms=0 activates on the same tick threshold is first exceeded (elapsed=0 >= 0)
  - CLEARED_UNACKED re-activation skips delay — re-activation from cleared state is immediate by IEC 62682 design
  - acknowledge() uses time.monotonic() for acknowledged_at timestamp (not the caller-provided now_ms) since ack is operator-triggered, not tick-driven
  - _check_cleared returns True (cleared) when threshold fields are None — handles degenerate alarm with no thresholds
metrics:
  duration_seconds: 158
  completed_date: "2026-03-15"
  tasks_completed: 2
  files_created: 1
  files_modified: 1
  tests_added: 31
  tests_passing: 47
---

# Phase 15 Plan 02: AlarmEvaluator State Machine Summary

**One-liner:** AlarmEvaluator class implementing full IEC 62682 five-state lifecycle with hysteresis clear thresholds, configurable delay timers, and typed event dicts — pure logic, no I/O.

## Tasks Completed

| Task | Description | Commit | Tests |
|------|-------------|--------|-------|
| 1 (TDD RED) | test_evaluator.py — 31 failing tests | 7962bb1 | 0/31 |
| 2 (TDD GREEN) | AlarmEvaluator implementation | 5be86b2 | 47/47 |

## What Was Built

### evaluator.py — AlarmEvaluator class (added alongside AlarmInstance)

The `AlarmEvaluator` class drives each `AlarmInstance` through the IEC 62682 five-state lifecycle:

```
NORMAL -> ACTIVE_UNACKED -> ACTIVE_ACKED -> RTN -> NORMAL
                         -> CLEARED_UNACKED -> RTN -> NORMAL
```

**evaluate_tick(values, now_ms)** — main entry point called once per 1Hz tick:
- Iterates all instances, skips disabled alarms
- `values` dict maps signal paths to float | None
- `now_ms` is caller-provided monotonic timestamp (enables deterministic testing)
- Returns list of event dicts (empty if no transitions occurred)

**State machine per instance:**

| From State | Condition | To State | Event |
|------------|-----------|----------|-------|
| NORMAL | exceeds threshold for >= delay_ms | ACTIVE_UNACKED | alarm_activated |
| NORMAL | signal recovers before delay_ms | NORMAL | — (timer reset) |
| ACTIVE_UNACKED | signal < high_clear (hysteresis) | CLEARED_UNACKED | alarm_cleared |
| ACTIVE_UNACKED | acknowledge() called | ACTIVE_ACKED | — (ack result) |
| ACTIVE_ACKED | signal < high_clear (hysteresis) | RTN | alarm_rtn |
| CLEARED_UNACKED | acknowledge() called | RTN | — (ack result) |
| CLEARED_UNACKED | signal re-exceeds (no delay) | ACTIVE_UNACKED | alarm_activated |
| RTN | next tick (auto) | NORMAL | — (reset timestamps) |

**acknowledge(alarm_id)** — operator acknowledgement:
- ACTIVE_UNACKED -> ACTIVE_ACKED (returns `{"status": "ok", ...}`)
- CLEARED_UNACKED -> RTN (returns `{"status": "ok", ...}`)
- All other states return `{"status": "error", ...}` (rejected)

**Threshold helpers:**
- `_check_exceeds(inst, value)` — handles high/low/both threshold configurations
- `_check_cleared(inst, value)` — checks value against pre-computed hysteresis clear thresholds

**Event dict format** (all fields present on every event):
```python
{
    "event_type": "alarm_activated",  # or alarm_acknowledged, alarm_cleared, alarm_rtn
    "alarm_id": "cell_voltage_high",
    "signal": "bms.cell_voltage_max",
    "severity": "warning",
    "state": "ACTIVE_UNACKED",        # state AFTER transition
    "value": 3.70,
    "threshold": 3.65,
}
```

### test_evaluator.py — 31 tests across 9 test classes

| Class | Tests | Coverage |
|-------|-------|----------|
| TestNormalState | 6 | delay start/continue/expire, timer reset, zero-delay |
| TestActiveUnacked | 3 | stay active, clear to CLEARED_UNACKED, acknowledge to ACTIVE_ACKED |
| TestActiveAcked | 3 | stay active, clear to RTN, RTN auto-to-NORMAL with timestamp reset |
| TestClearedUnacked | 2 | acknowledge to RTN, re-activation to ACTIVE_UNACKED |
| TestRtnState | 1 | auto-transition to NORMAL with full reset |
| TestHysteresis | 2 | high threshold band, low threshold band |
| TestDisabledAlarms | 2 | disabled stays NORMAL, no events across multiple ticks |
| TestNoneSignal | 2 | None value clears delay timer, missing key treated as None |
| TestEventContent | 3 | severity in event, all 7 fields present, alarm_id matches |
| TestAcknowledgeValidation | 4 | NORMAL/ACTIVE_ACKED/RTN/unknown rejected |
| TestQueryMethods | 3 | get_active_alarms empty/non-empty, get_alarm_config returns all |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed delay timer boundary in test_delay_timer_resets_on_recovery**
- **Found during:** GREEN phase — first pytest run
- **Issue:** Test checked `now_ms=8001` for activation after timer reset to `3002`. Elapsed = `8001-3002 = 4999ms < 5000ms` — correctly not activating. Test expected activation 1ms early.
- **Fix:** Changed test assertion to `now_ms=8002` (elapsed = 5000ms >= 5000ms delay).
- **Files modified:** `src/alarm_manager/tests/test_evaluator.py`
- **Commit:** 5be86b2

## Self-Check

### Files

- src/alarm_manager/tests/test_evaluator.py — EXISTS
- src/alarm_manager/src/ems_alarm_manager/evaluator.py — EXISTS (modified)

### Commits

- 7962bb1 — test(15-02): RED — AlarmEvaluator IEC 62682 lifecycle tests
- 5be86b2 — feat(15-02): GREEN — AlarmEvaluator IEC 62682 state machine

## Self-Check: PASSED
