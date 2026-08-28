---
phase: 12-logging
plan: "04"
subsystem: logger
tags: [duckdb, parquet, query, zmq, jsonl, time-series]
dependency_graph:
  requires: [logger_config, parquet_schema, event_writer]
  provides: [query_handler, query_server]
  affects: [logger, hmi_server, diagnostics]
tech_stack:
  added: [duckdb]
  patterns: [integer-bucket-downsampling, signal-allowlist, date-narrowed-glob, zmq-rep-dispatch]
key_files:
  created:
    - src/logger/python/src/ems_logger/query_handler.py
    - src/logger/python/tests/test_query_handler.py
  modified: []
decisions:
  - "Integer floor division (ts // interval_ms) for bucketing -- avoids DuckDB pytz dependency"
  - "Signal allowlist built from Parquet schema field names -- prevents SQL injection via column names"
  - "event_log queries read JSONL files via JsonlEventWriter.read_events() and filter in Python"
  - "Energy totals use CASE WHEN on pcs_active_power sign for charge/discharge, MAX-MIN delta for grid"
  - "QueryServer uses run_in_executor for blocking DuckDB queries inside async dispatch"
  - "Date-narrowed glob patterns avoid scanning all 90 days for short time ranges"
metrics:
  duration: "6m 20s"
  completed: "2026-03-14"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 12
  tests_passed: 12
---

# Phase 12 Plan 04: DuckDB Query Handler Summary

DuckDB query handler with 6 predefined query types (time_series, latest, range_stats, event_log, energy_totals, cell_snapshot) served via ZMQ REP using stateless in-memory DuckDB connections reading Parquet files directly, with integer-bucket downsampling, signal allowlist injection prevention, and per-type timeout enforcement.

## Task Completion

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | DuckDB query functions for 6 types (TDD) | 4b9f150, 4e53966 | query_handler.py, test_query_handler.py |
| 2 | ZMQ REP query server with dispatch and timeouts (TDD) | 5bcc4f8 | query_handler.py, test_query_handler.py |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] DuckDB float division produced no bucketing**
- **Found during:** Task 1 GREEN phase
- **Issue:** DuckDB `ts / interval_ms` uses float division on BIGINT, producing unique values per row instead of bucketed groups
- **Fix:** Changed to `ts // interval_ms` (integer floor division operator in DuckDB)
- **Files modified:** query_handler.py
- **Commit:** 4e53966

**2. [Rule 3 - Blocking] DuckDB time_bucket requires pytz**
- **Found during:** Task 1 GREEN phase
- **Issue:** `time_bucket(INTERVAL, to_timestamp())` requires pytz module which is not installed
- **Fix:** Replaced with integer arithmetic bucketing `(ts // interval_ms) * interval_ms` which avoids timestamp conversion entirely
- **Files modified:** query_handler.py
- **Commit:** 4e53966

**3. [Rule 1 - Bug] Test helper LIST column detection matched flat fields**
- **Found during:** Task 1 GREEN phase
- **Issue:** `"cell_v" in f.name` matched flat fields like `rack0_min_cell_v`, inserting list values into float32 columns
- **Fix:** Used `pa.types.is_list(f.type)` for reliable LIST column detection
- **Files modified:** test_query_handler.py
- **Commit:** 4e53966

## Verification Results

- All 6 query types return correct results from test Parquet/JSONL files
- ZMQ REQ/REP round-trip works with MessagePack encoding
- Unknown query types return structured error with "Unknown query type" message
- Timeouts enforced -- 0s timeout triggers timeout error response
- No persistent DuckDB database files created (in-memory connection only)
- Signal allowlist rejects SQL injection attempts ("DROP TABLE users; --")
- Row limit enforcement returns QueryError with specific limit hit
- All 12 new tests pass; all 60 logger tests pass total

## Self-Check: PASSED

- query_handler.py: 770 lines (min 200)
- test_query_handler.py: 612 lines (min 150)
- All 3 commits verified in git log
- All created files exist on disk
