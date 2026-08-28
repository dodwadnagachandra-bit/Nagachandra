---
phase: 29-hardware-validation
plan: "03"
subsystem: hardware-validation
tags: [pytest, ssh, scp, bash, oscilloscope, gpio, can, modbus, soak-test, report-generator, markdown]
dependency_graph:
  requires: [29-01]
  provides:
    - tools/hw-validation/stage4-gpio-timing.md
    - tools/hw-validation/stage5-soak.sh
    - tools/hw-validation/soak_monitor.py
    - tests/hw/test_soak.py
    - tools/hw-validation/hw_validation_report.py
    - docs/hardware-validation-report.md
  affects: []
tech_stack:
  added: []
  patterns:
    - oscilloscope-measurement-procedure
    - continuous-soak-monitor-with-jsonl
    - nohup-remote-process-via-ssh
    - pytest-25h-timeout-soak-wrapper
    - offline-report-generator-with-live-ssh-collection
key_files:
  created:
    - tools/hw-validation/stage4-gpio-timing.md
    - tools/hw-validation/soak_monitor.py
    - tools/hw-validation/stage5-soak.sh
    - tests/hw/test_soak.py
    - tools/hw-validation/hw_validation_report.py
    - docs/hardware-validation-report.md
  modified: []
decisions:
  - "Stage 4 procedure uses 100-event minimum with oscilloscope p99 (not software timestamps) — only way to capture kernel scheduling jitter and GPIO driver overhead on real hardware"
  - "soak_monitor.py uses /proc/<pid>/status for RSS (not psutil) to avoid adding a dev dependency to the production Yocto venv"
  - "RSS baseline computed from median of first 60 samples (1 hour) to avoid declaring leak during startup transients"
  - "stage5-soak.sh fails fast on missing simulators before wasting 24 hours — CAN candump -T 5000 and Modbus pymodbus one-liner as pre-checks"
  - "hw_validation_report.py accepts Stage 4 via CLI args (not SSH) since oscilloscope data is manual and cannot be automated"
  - "Report generator supports --offline mode for post-soak report generation without ECU still connected"
metrics:
  duration: "7m38s"
  completed_date: "2026-03-16"
  tasks_completed: 2
  files_created: 6
  files_modified: 0
---

# Phase 29 Plan 03: Stage 4-5 Soak Test and Hardware Validation Report Summary

**One-liner:** Oscilloscope GPIO timing procedure (100-event, dual-channel), 24-hour soak monitor with RSS/restart/Parquet checks, CAN+Modbus pre-flight launcher, pytest wrapper with 25h timeout, and markdown report generator reading all 5 stage results.

## What Was Built

### tools/hw-validation/stage4-gpio-timing.md — Oscilloscope Measurement Procedure

299-line procedure document for validating safety_manager GPIO response time on physical hardware:

- **Pin connections table**: CH1 = DI-6 (ESTOP_NO), CH2 = DO-5 (PCS_STOP) with references to gpio_config.yaml signal definitions
- **Oscilloscope setup**: 50ms/div timebase, CH1 rising edge trigger at 1.65V (50% of 3.3V), statistics mode enabled
- **Pre-test checklist**: ems.target active, SCHED_FIFO priority verified via chrt, PREEMPT_RT kernel confirmed via uname -r
- **100-event measurement protocol**: Manual E-Stop trigger sequence with 3-5s recovery between events
- **Pass criteria**: p99 < 100ms AND all 100 events triggered DO-5
- **Dual-channel cross-check**: 3 tests — valid E-Stop (both agree → DO-5 asserts), DI-6-only fault (→ no assert), DI-7-only fault (→ no assert)
- **Failure investigation**: PREEMPT_RT check → SCHED_FIFO priority → CPU load → IRQ storms → RT throttling (sched_rt_runtime_us)
- **Data recording template**: Fill-in form for tester to record all measurements

### tools/hw-validation/soak_monitor.py — 24-Hour Continuous Monitor

426-line Python daemon for ECU-side soak monitoring:

- **Shebang**: `#!/opt/ems/venv/bin/python3` — runs from Yocto venv
- **Every 60 seconds checks**:
  1. Service restart counts via `systemctl show {svc} --property=NRestarts` for all 14 services
  2. RSS growth via `/proc/<pid>/status VmRSS` — baseline set to median of first 60 samples (1 hour), flags >10% growth
  3. Parquet freshness — most recent .parquet in /data/ must be <10s old
  4. Disk usage — warns if /data >80%
- **JSONL output** to `/data/soak_monitor.jsonl` — one heartbeat record per check cycle
- **Final summary record**: `{"check": "summary", "result": "PASS/FAIL", ...}` on completion or signal
- **PASS criteria**: 0 restarts, max RSS growth <10%, 0 data gaps
- **Signal handling**: SIGTERM/SIGINT caught, summary written before exit

### tools/hw-validation/stage5-soak.sh — Pre-flight + Soak Launcher

Bash launcher with mandatory pre-condition checks before committing to 24h run:

- **Pre-check 1**: ECU reachable via SSH
- **Pre-check 2**: `systemctl is-active ems.target` — all services must be running
- **Pre-check 3**: CAN traffic — `candump can0 -n 1 -T 5000` — exits 1 with fix instructions if no frame
- **Pre-check 4**: Modbus response — pymodbus one-liner on /dev/ttyS1 — exits 1 with fix instructions if ERR
- **Only on all-pass**: SCP soak_monitor.py to ECU, launch via nohup, print monitoring commands
- Post-launch guidance: tail command, status check command, full log path

### tests/hw/test_soak.py — pytest Soak Test Wrapper

309-line module with `pytestmark = [pytest.mark.hw, pytest.mark.timeout(90000)]`:

- **`test_soak_preconditions`**: CAN traffic check + Modbus response check; clear failure messages with fix instructions per simulator type
- **`test_soak_24h`**: Copies soak_monitor.py via SCP, launches via nohup, polls every 5 minutes by reading last JSONL line until `check == "summary"`, asserts `result == "PASS"` with failure details
- **`test_soak_results_exist`**: Quick post-soak check — reads last line of /data/soak_monitor.jsonl, asserts summary exists and result is PASS; for use after independent soak run

### tools/hw-validation/hw_validation_report.py — Report Generator

797-line report generator with live SSH and offline modes:

- **Stage 1**: SSH to check all 14 service statuses and `systemd-analyze time`
- **Stage 2**: SSH for CAN interface existence, GPIO chip detection, network interface count, DRM subsystem
- **Stage 3**: SSH for RTDB shm, ZMQ socket, Parquet file existence, DuckDB query
- **Stage 4**: `--gpio-p50/p95/p99/gpio-samples` CLI args (manual oscilloscope data)
- **Stage 5**: SCP /data/soak_monitor.jsonl, read last summary record
- **Benchmarks**: SCP /data/benchmark_results.json from Plan 02 benchmark.py
- **--offline mode**: Reads cached files only, no SSH — for post-soak report generation
- **Executive summary table**: Overall PASS/FAIL with per-stage results
- **Exit code**: 0 if all stages with data PASS, 1 if any FAIL

### docs/hardware-validation-report.md — Report Template

188-line markdown template with placeholder values for all 5 stages, benchmark table, hardware configuration table, and sign-off rows. Both human-fillable and machine-fillable by hw_validation_report.py.

## Decisions Made

1. **soak_monitor.py uses /proc/<pid>/status for RSS, not psutil** — avoids adding psutil as a Yocto venv dependency. VmRSS from procfs is always available on Linux, has no Python library requirement, and reads the same data psutil would.

2. **RSS baseline from first 60 samples (1 hour), not from startup** — startup RSS is artificially low before caches warm. The 60-sample median baseline avoids false-positive leak detection during the first hour while still catching genuine leaks over the remaining 23 hours.

3. **stage5-soak.sh fails fast on missing simulators** — a 24-hour soak with no CAN or Modbus inputs is a wasted day. The pre-checks add ~10 seconds but save hours of debugging. CAN check uses `candump -T 5000` timeout (5 seconds) to avoid hanging.

4. **Stage 4 CLI args for oscilloscope data** — oscilloscope measurements cannot be automated. The report generator accepts p50/p95/p99/samples via `--gpio-*` flags rather than prompting interactively or requiring a CSV file, keeping the workflow simple for a manual step.

5. **--offline mode for report generator** — after the ECU is decommissioned post-validation, the report may need to be regenerated or reviewed. Offline mode reads the SCP'd cached files without requiring the ECU to still be connected.

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

### Files Exist

- [x] tools/hw-validation/stage4-gpio-timing.md — 299 lines (target: >=50)
- [x] tools/hw-validation/soak_monitor.py — 426 lines (target: >=80), executable
- [x] tools/hw-validation/stage5-soak.sh — executable
- [x] tests/hw/test_soak.py — 309 lines (target: >=40), 3 tests collected
- [x] tools/hw-validation/hw_validation_report.py — 797 lines (target: >=60), executable
- [x] docs/hardware-validation-report.md — 188 lines (target: >=40)

### Key Links Verified

- [x] soak_monitor.py writes to `/data/soak_monitor.jsonl` (line: `f"{DATA_DIR}/soak_monitor.jsonl"`)
- [x] hw_validation_report.py reads `/data/benchmark_results.json` (line: `BENCHMARK_JSON_PATH`)
- [x] test_soak.py launches soak_monitor.py as subprocess (nohup via SSH)
- [x] stage5-soak.sh checks `candump can0` and `pymodbus` before launching monitor

### Commits Exist

- [x] 6d25efc — Task 1: Stage 4 procedure, soak monitor, launcher, test wrapper
- [x] c6ad4b6 — Task 2: report generator and template

### Verification Commands Passed

- [x] `test -f tools/hw-validation/stage4-gpio-timing.md` — exists
- [x] `python3 -c "import ast; ast.parse(open('tools/hw-validation/soak_monitor.py').read())"` — parses OK
- [x] `uv run pytest tests/hw/test_soak.py --collect-only` — 3 tests collected
- [x] `test -f docs/hardware-validation-report.md` — exists
- [x] All scripts have proper shebangs and are executable

## Self-Check: PASSED
