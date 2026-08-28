---
phase: 29-hardware-validation
plan: "02"
subsystem: hardware-validation
tags: [pytest, ssh, rtdb, zmq, parquet, duckdb, websocket, arm64, benchmark]
dependency_graph:
  requires: [29-01]
  provides: [tests/hw/test_datapath.py, tests/hw/test_benchmarks.py, tools/hw-validation/benchmark.py]
  affects: []
tech_stack:
  added: []
  patterns: [base64-ssh-script, ecu-venv-python, benchmark-harness, pipeline-integrity-test]
key_files:
  created:
    - tests/hw/test_datapath.py
    - tests/hw/test_benchmarks.py
    - tools/hw-validation/benchmark.py
  modified: []
decisions:
  - "SSH Python scripts base64-encoded to avoid shell quoting issues with complex multi-line scripts"
  - "benchmark.py falls back from ems_common.rtdb to direct mmap /dev/shm write if venv module unavailable"
  - "WebSocket benchmark skips gracefully when websocket-client absent or hmi_server not active"
  - "DuckDB query falls back from 24h window to all-available data when data is insufficient (first boot)"
metrics:
  duration: "7m"
  completed_date: "2026-03-16"
  tasks_completed: 2
  files_created: 3
  files_modified: 0
---

# Phase 29 Plan 02: Stage 3 Data Path Validation and ARM64 Benchmarks Summary

**One-liner:** Stage 3 data pipeline integrity tests (RTDB, ZMQ, Parquet, DuckDB) plus ARM64 performance benchmarks via SSH, with standalone ECU harness writing JSON results to /data/.

## What Was Built

### tests/hw/test_datapath.py — Stage 3: Data Pipeline Integrity

5 tests validating the full CAN sim -> RTDB -> ZMQ -> Parquet pipeline on ARM64:

- `test_rtdb_shm_exists` — confirms data_manager created `/dev/shm/ems_rtdb*` via `ls /dev/shm/ems_rtdb*`
- `test_zmq_telemetry_publishing` — subscribes to `ipc:///run/ems/telemetry.sock` for 10s via Yocto venv, asserts at least one multipart message received
- `test_parquet_files_created` — creates a timestamp marker, waits 5s, checks for Parquet files newer than marker in `/data`
- `test_parquet_row_count_growing` — counts rows at t=0 via pyarrow.parquet.read_metadata, waits 10s, counts again; asserts delta > 0
- `test_duckdb_query_works` — runs `SELECT COUNT(*) FROM read_parquet('/data/**/*.parquet')` on ECU, asserts zero return code and valid output

All tests use `@pytest.mark.usefixtures("ecu_reachable")` for auto-skip.

### tests/hw/test_benchmarks.py — ARM64 Performance Benchmarks

5 performance benchmark tests with defined ARM64 targets:

| Benchmark | Target | Measured via |
|-----------|--------|--------------|
| `test_control_loop_jitter` | <10ms 1-sigma | ZMQ telemetry tick timestamps, 60s |
| `test_rtdb_write_latency` | <1ms p99 | 10K writes via ems_common.rtdb or mmap |
| `test_parquet_throughput` | >=1 rows/sec | pyarrow row count delta over 60s |
| `test_duckdb_query_latency` | <5s | 24h time_series query wall clock |
| `test_websocket_latency` | <100ms p99 | ZMQ timestamp to WebSocket recv delta |

Each test:
- Base64-encodes multi-line Python scripts for clean SSH transfer
- Prints actual measured value alongside target
- Uses assertion message format: `f"metric {actual:.Nf}unit exceeds {target}unit target"`
- Skips gracefully when service is unavailable (WebSocket) or data is insufficient

### tools/hw-validation/benchmark.py — Standalone ECU Harness

Self-contained benchmark script designed to run directly on the ECU without pytest:

- Shebang: `#!/opt/ems/venv/bin/python3` — no laptop needed
- All 5 benchmarks run sequentially with structured stdout output:

```
=== EMS ARM64 Performance Benchmarks ===
[1/5] Control Loop Jitter
  Measured: 3.2ms
  Target:   10ms
  Result:   PASS
...
=== Summary: 5/5 PASS ===
```

- Writes machine-readable results to `/data/benchmark_results.json`
- Each benchmark catches exceptions independently — errors report `ERROR` not crash
- Fallback paths: RTDB mmap fallback, DuckDB all-data fallback, WebSocket skip

## Decisions Made

1. **Base64 SSH encoding for multi-line scripts** — `echo <b64> | base64 -d | python3` pattern avoids quoting hell for complex measurement scripts. Simple one-liners in test_datapath.py use double-quoted inline strings.

2. **ems_common.rtdb with mmap fallback in benchmark.py** — the standalone harness may run before the full venv is configured. The mmap fallback writes directly to `/dev/shm/ems_rtdb` and still produces a valid p99 measurement.

3. **WebSocket benchmark uses graceful skip path** — `websocket-client` may not be in the Yocto venv. The test calls `pytest.skip()` rather than `pytest.fail()`, matching the pattern used in Stage 2 driver tests for optional hardware.

4. **DuckDB 24h window with all-data fallback** — first-boot ECUs have less than 24h of Parquet data. The query falls back to `SELECT COUNT(*)` without a WHERE clause and notes this in output, so the latency measurement is still valid.

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

### Files Exist

- [x] tests/hw/test_datapath.py — exists (130+ lines, 5 tests)
- [x] tests/hw/test_benchmarks.py — exists (240+ lines, 5 tests)
- [x] tools/hw-validation/benchmark.py — exists, executable (chmod +x)

### Commits Exist

- [x] 626bed2 — Task 1: Stage 3 data path tests and ARM64 performance benchmarks
- [x] b1bc5cb — Task 2: standalone ECU benchmark harness

### Verification Commands Passed

- [x] `uv run python -c "import tests.hw.test_datapath; import tests.hw.test_benchmarks; print('imports OK')"` — imports OK
- [x] `uv run pytest tests/hw/test_datapath.py tests/hw/test_benchmarks.py --collect-only` — 10 tests collected
- [x] `python3 -c "import ast; ast.parse(open('tools/hw-validation/benchmark.py').read()); print('syntax OK')"` — syntax OK
- [x] `test -x tools/hw-validation/benchmark.py && echo "executable"` — executable

## Self-Check: PASSED
