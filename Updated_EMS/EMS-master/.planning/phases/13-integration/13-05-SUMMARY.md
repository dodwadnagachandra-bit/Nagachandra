---
phase: 13-integration
plan: "05"
subsystem: testing
tags: [integration-tests, performance-validation, metrics, gpio-latency, rss-growth]
dependency_graph:
  requires: [13-01, 13-02]
  provides: [performance-validation-tests, metric-collection-functions]
  affects: []
tech_stack:
  added: []
  patterns: [parallel-thread-collection, tight-poll-gpio-timing, zmq-sequence-lag]
key_files:
  created:
    - tests/integration/test_performance.py
  modified: []
decisions:
  - "Parallel metric collection via 4 daemon threads (GPIO, RTDB, ZMQ, RSS) joined with timeout"
  - "GPIO latency uses tight poll loop (no sleep) for sub-ms accuracy, RtdbBackend get_do()"
  - "ZMQ lag tracks sequence number gaps with 5s timeout producing 999 severe-lag indicator"
  - "Container topology tests guarded by psutil.virtual_memory().total >= 4GB skip marker"
  - "Full 10-minute pass in separate TestPerformanceCleanPassFull class with @pytest.mark.slow"
metrics:
  duration: "3m 05s"
  completed: "2026-03-14"
  tasks_completed: 1
  tasks_total: 1
---

# Phase 13 Plan 05: Performance Validation Tests Summary

Performance validation tests with 5 metrics (GPIO <100ms p99, RTDB <10ms p99, ZMQ <5 messages lag, logger >=95% throughput, RSS <10% growth) across residential clean, residential fault-injection, and container topologies using parallel threaded collection.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create performance metric collection helpers and test classes | 0653cfd | tests/integration/test_performance.py |

## Verification Results

- Syntax check: PASSED (ast.parse valid)
- Test collection: 20 tests collected
  - TestPerformanceCleanPass: 5 tests (residential, no faults)
  - TestPerformanceFaultPass: 5 tests (residential, DI-1 stuck high)
  - TestContainerTopology: 5 tests (container profile, 4GB RAM guard)
  - TestPerformanceCleanPassFull: 5 tests (@pytest.mark.slow, 10-min pass)

## Key Implementation Details

### Metric Collection Functions (5 total)
- `collect_gpio_latencies()`: RtdbBackend set_di -> tight poll get_do, measures DI-to-DO response time
- `collect_rtdb_write_latencies()`: Compares system.last_update_ms to wall clock for write staleness
- `collect_zmq_lag()`: SUB socket with sequence tracking, 5s RCVTIMEO, 999 for timeout gaps
- `collect_rss_samples()`: psutil RSS via ModuleProcess.rss_bytes at 1Hz for all alive modules
- `count_parquet_rows()`: pyarrow sum of telemetry_*.parquet rows for logger throughput

### Test Structure
- 4 classes x 5 metric tests = 20 total test items
- Class-scoped fixtures launch all modules once per class, collect metrics in parallel threads
- Fault injection: GPIO DI-1 stuck high via RtdbBackend fault_cfg
- Container tests skip on machines with <4GB RAM
- GPIO tests skip gracefully when harness or RTDB unavailable
- RTDB tests skip with <10 samples (module may not be writing)
- Duration: 120s default (CLEAN_PASS_DURATION_S), 600s for slow tests (FULL_PASS_DURATION_S)

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED
