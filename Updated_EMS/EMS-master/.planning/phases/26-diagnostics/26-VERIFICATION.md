---
phase: 26-diagnostics
verified: 2026-03-16T00:00:00Z
status: gaps_found
score: 7/8 must-haves verified
re_verification: false
gaps:
  - truth: "Diagnostics loop routes live BMS SOH from telemetry into SohAnalyzer"
    status: failed
    reason: "Field name mismatch: data_manager publishes 'pack_soh' but loop.py reads payload.get('pack_soh_bms', 100.0). The fallback 100.0 silently replaces every real SOH value, making SOH trending permanently static in production."
    artifacts:
      - path: "src/diagnostics/src/ems_diagnostics/loop.py"
        issue: "Line 269: payload.get('pack_soh_bms', 100.0) — key does not match publisher output"
      - path: "src/data_manager/python/src/ems_data_manager/publisher.py"
        issue: "Line 85: publishes 'pack_soh', not 'pack_soh_bms'"
    missing:
      - "Change payload.get('pack_soh_bms', 100.0) to payload.get('pack_soh', 100.0) in loop.py _route_telemetry()"
      - "Update test_loop.py fixture to use 'pack_soh' key to match the real publisher (currently uses 'pack_soh_bms' which passes but masks the production bug)"
human_verification:
  - test: "Start ems_diagnostics with a live data_manager publishing BMS telemetry and observe get_current() response over time"
    expected: "soh_pct values in the response should vary (e.g., 93.5, 94.0) not remain fixed at 100.0"
    why_human: "The field name mismatch causes silent fallback — automated tests pass because they inject the wrong field name; only a live integration test can confirm SOH flows correctly"
---

# Phase 26: Diagnostics Verification Report

**Phase Goal:** Diagnostics module monitors system health, tracks degradation trends, and generates reports queryable by HMI and cloud
**Verified:** 2026-03-16
**Status:** gaps_found — 1 wiring bug blocks SOH trending in production
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | SOCK_DIAGNOSTICS_CMD and SOCK_DIAGNOSTICS_PUB constants exist in ipc.py | VERIFIED | Lines 28-29 of ipc.py; TOPIC_DIAGNOSTICS at line 64 |
| 2 | diagnostics_config.yaml loads and validates against JSON Schema | VERIFIED | File exists with all 5 sections; Draft202012Validator in config.py |
| 3 | Config exposes all thresholds, intervals, enable flags, and battery rated capacity | VERIFIED | DiagnosticsConfig dataclass with 5 nested typed sub-configs |
| 4 | SohAnalyzer tracks BMS-reported SOH per rack and detects full charge/discharge cycles | VERIFIED (unit) | 3-state machine (IDLE/CHARGED/DISCHARGING) in soh.py; 133-line test file, all pass |
| 5 | PcsAnalyzer computes AC/DC efficiency and skips idle states | VERIFIED | pcs.py lines 67-87; IDLE_THRESHOLD_W=500W; both charge/discharge formulas present |
| 6 | ThermalAnalyzer and CommAnalyzer are substantive and fully wired into DiagnosticsLoop | VERIFIED | thermal.py uses outlet_temp (not ambient); comm.py uses event_log query; loop.py instantiates all 4 analyzers |
| 7 | DiagnosticsLoop runs 5 async tasks and serves 3 REP query types | VERIFIED | asyncio.gather of 5 tasks in loop.py; get_current/get_report/get_predictions all dispatch; REP always replies |
| 8 | SOH telemetry field name in loop matches data_manager publisher output | FAILED | loop.py reads `pack_soh_bms`; publisher emits `pack_soh` — silent 100.0 fallback in production |

**Score:** 7/8 truths verified

---

## Required Artifacts

| Artifact | Min Lines | Actual | Status | Details |
|----------|-----------|--------|--------|---------|
| `src/common/python/src/ems_common/ipc.py` | — | 186 | VERIFIED | SOCK_DIAGNOSTICS_CMD, SOCK_DIAGNOSTICS_PUB, TOPIC_DIAGNOSTICS all present |
| `src/diagnostics/src/ems_diagnostics/config.py` | — | 173 | VERIFIED | load_diagnostics_config + DiagnosticsConfig + 5 sub-dataclasses |
| `config/diagnostics_config.yaml` | — | 33 | VERIFIED | soh_warning_pct and all required keys present |
| `config/schemas/diagnostics_config.schema.json` | — | — | VERIFIED | Exists; Draft 2020-12 confirmed in SUMMARY |
| `src/diagnostics/src/ems_diagnostics/analyzers/soh.py` | 60 | 172 | VERIFIED | SohAnalyzer with update(), get_current(), get_history(), add_history_point() |
| `src/diagnostics/src/ems_diagnostics/analyzers/pcs.py` | 40 | 107 | VERIFIED | PcsAnalyzer with update(), get_current(); dc_voltage*dc_current calculation |
| `src/diagnostics/src/ems_diagnostics/analyzers/thermal.py` | 40 | 85 | VERIFIED | ThermalAnalyzer with outlet_temp (not ambient_temp) |
| `src/diagnostics/src/ems_diagnostics/analyzers/comm.py` | 40 | 152 | VERIFIED | CommAnalyzer with update_from_event_log() and get_current() |
| `src/diagnostics/src/ems_diagnostics/reporter.py` | 80 | 217 | VERIFIED | ReportBuilder with build_current(), build_report(), build_predictions() using statistics.linear_regression |
| `src/diagnostics/src/ems_diagnostics/loop.py` | 150 | 535 | VERIFIED | DiagnosticsLoop with all 5 async tasks; all 5 ZMQ socket types present |
| `src/diagnostics/src/ems_diagnostics/__main__.py` | 30 | 108 | VERIFIED | argparse + asyncio.run + signal handlers (SIGTERM/SIGINT) + cleanup in finally |
| `src/diagnostics/tests/test_soh_analyzer.py` | 80 | 133 | VERIFIED | 7+ tests; cycle state machine, double-count prevention, history tracking |
| `src/diagnostics/tests/test_pcs_analyzer.py` | 60 | 102 | VERIFIED | 6+ tests; idle skip, charge/discharge formulas, rolling average |
| `src/diagnostics/tests/test_thermal_analyzer.py` | 50 | 117 | VERIFIED | 6+ tests; delta_t calculation, fan rolling average, None before first update |
| `src/diagnostics/tests/test_comm_analyzer.py` | 50 | 206 | VERIFIED | 7+ tests; fault counting, rate calculation, status thresholds, reset behavior |
| `src/diagnostics/tests/test_reporter.py` | 100 | 393 | VERIFIED | 19 tests; all 3 query types, linear regression, severity classification |
| `src/diagnostics/tests/test_loop.py` | 80 | 581 | VERIFIED | 8 tests; inproc:// ZMQ, telemetry routing, report server, predictive alerts |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `config.py` | `diagnostics_config.yaml` | yaml.safe_load + Draft202012Validator | WIRED | Lines 108, 117 of config.py |
| `analyzers/soh.py` | `DiagnosticsConfig` | rated_capacity_ah | WIRED | __init__ accepts rated_capacity_ah; loop passes config.battery.rated_capacity_ah |
| `analyzers/pcs.py` | ZMQ pcs telemetry | active_power, dc_voltage, dc_current | WIRED | All 3 fields present in publisher.py line 103-106 and loop.py 276-281 |
| `analyzers/thermal.py` | ZMQ btms telemetry | outlet_temp field | WIRED | publisher.py line 143 emits outlet_temp; loop.py line 288 reads it |
| `analyzers/comm.py` | logger event_log query | comm_fault / device_id pattern | WIRED | loop.py _run_trend_update queries event_log; results passed to update_from_event_log() |
| `loop.py` | `SohAnalyzer` (pack_soh field) | pack_soh_bms from BMS payload | BROKEN | loop.py line 269 reads `pack_soh_bms`; publisher.py line 85 emits `pack_soh` — field name mismatch causes silent 100.0 fallback |
| `loop.py` | `SOCK_TELEMETRY` | ZMQ SUB for 1Hz BMS/PCS/BTMS | WIRED | _sub connected to SOCK_TELEMETRY, subscribed to 3 topics |
| `loop.py` | `SOCK_LOGGER_QUERY` | ZMQ REQ for range_stats/event_log | WIRED | _req connected to SOCK_LOGGER_QUERY; used in _run_trend_update |
| `loop.py` | `SOCK_DIAGNOSTICS_CMD` | ZMQ REP for report queries | WIRED | _rep bound to SOCK_DIAGNOSTICS_CMD; _report_server drains it |
| `loop.py` | `SOCK_DIAGNOSTICS_PUB` | ZMQ PUB for broadcasting | WIRED | _pub bound to SOCK_DIAGNOSTICS_PUB; _diagnostics_publisher sends to it |
| `reporter.py` | `statistics.linear_regression` | SOH trend projection | WIRED | Line 165 of reporter.py calls statistics.linear_regression(x_days, y_soh) |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| DIAG-01 | 26-01, 26-02, 26-04 | SOH trending tracks battery State of Health per rack over time | PARTIAL | SohAnalyzer unit tested and wired into loop; history mechanism works; BUT `pack_soh_bms` field mismatch means live BMS SOH is never read — trending always runs on the 100.0 default |
| DIAG-02 | 26-02, 26-04 | PCS efficiency calculates AC/DC conversion efficiency, tracks degradation | SATISFIED | PcsAnalyzer correct formula; dc_voltage*dc_current; field names match publisher; rolling average tracked; published in diagnostics snapshot |
| DIAG-03 | 26-03, 26-04 | Thermal analysis monitors BTMS — cooling effectiveness, temp delta, fan duty | SATISFIED | ThermalAnalyzer uses outlet_temp (not ambient); delta_t = max_cell_t - outlet_temp; fan rolling average; wired into loop from BMS+BTMS telemetry |
| DIAG-04 | 26-03, 26-04 | Communication health scoring rates each device by response/timeout/CRC rate | SATISFIED | CommAnalyzer scores per device_id from event_log; healthy/degraded/unhealthy thresholds; updated hourly via logger query |
| DIAG-05 | 26-04 | Diagnostic report generation creates daily/weekly summaries queryable via ZMQ REQ/REP | SATISFIED | REP server dispatches get_current, get_report, build_report queries logger for 24h/168h windows; results returned in structured dict |
| DIAG-06 | 26-04 | Predictive alerts detect degradation trends and publish warning events before alarm thresholds | SATISFIED (conditional) | ReportBuilder.build_predictions() uses linear_regression; fires PUSH events when days_to_threshold < 90; BUT alerting is based on SOH history which is populated from the same broken field — predictions are only as good as the SOH data flowing in |

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `loop.py` | 269 | `payload.get("pack_soh_bms", 100.0)` — key mismatch with publisher | BLOCKER | All SOH trending silently uses 100.0 in production; DIAG-01 non-functional on real hardware; DIAG-06 predictions never detect real degradation |
| `tests/test_loop.py` | 151 | Fixture injects `pack_soh_bms` key (masks production bug) | WARNING | Tests pass but don't catch the field name mismatch; after fixing loop.py the test fixture must also be updated to `pack_soh` |

No stub implementations found. No TODO/FIXME comments in source files. No placeholder returns in logic paths. All `return []` instances are legitimate guard clauses (CommAnalyzer before first update; loop query error path).

---

## Human Verification Required

### 1. Live SOH Field Routing (post-fix)

**Test:** After correcting the field name to `pack_soh`, start the diagnostics service with a live or simulated data_manager publishing BMS telemetry. Query `get_current` over 10 minutes.
**Expected:** `soh[0].soh_pct` values reflect the actual BMS-reported SOH (e.g., 93.5) and are not frozen at 100.0.
**Why human:** The field name fallback is a silent failure — automated tests currently pass because they inject the wrong key. Only a live integration confirms the fix is end-to-end correct.

### 2. HMI Diagnostics Screen Integration

**Test:** Open the HMI diagnostics screen and trigger a `get_current` query, a `get_report` (daily), and a `get_predictions` request.
**Expected:** All three views populate with live data rather than empty/null responses. PCS efficiency shows a percentage, thermal shows delta_t, comm health shows device list.
**Why human:** The HMI integration point (ZMQ REP consumer) cannot be verified programmatically without the HMI running.

### 3. Predictive Alert End-to-End

**Test:** Load a SohAnalyzer with synthetic declining SOH history (7+ points trending from 95% to 83%) and confirm that a PUSH event appears in the logger event_log with `event_type=predictive_alert`.
**Expected:** Logger receives the event; HMI or cloud consumer sees the alert before the SOH alarm threshold fires.
**Why human:** Requires the logger PUSH socket to be live and the event_log queryable.

---

## Gaps Summary

One production wiring bug blocks DIAG-01 (SOH trending) and partially undermines DIAG-06 (predictive alerts):

**Root cause:** `loop.py` line 269 reads `payload.get("pack_soh_bms", 100.0)` when routing BMS rack telemetry to `SohAnalyzer.update()`. The data_manager publisher (`publisher.py` line 85) emits this field as `pack_soh` (no `_bms` suffix). The fallback default of `100.0` silently replaces every real BMS SOH reading, so the SOH analyzer history is a flat line at 100.0 in production.

The fix is a single-line change in `loop.py` and a corresponding update to the test fixture in `test_loop.py`. All other diagnostics functionality (PCS efficiency, thermal, comm health, report server, predictive alert infrastructure, entry point) is correctly implemented and wired.

**Six requirements mapped to this phase — five are satisfied; DIAG-01 is partially blocked and DIAG-06 is degraded by the same root cause.**

---

_Verified: 2026-03-16_
_Verifier: Claude (gsd-verifier)_
