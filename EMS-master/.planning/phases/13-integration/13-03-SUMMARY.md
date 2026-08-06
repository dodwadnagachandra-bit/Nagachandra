---
phase: 13-integration
plan: "03"
subsystem: testing
tags: [integration-tests, e2e-pipeline, duckdb, parquet, zmq]
dependency_graph:
  requires: [13-01]
  provides: [e2e-pipeline-tests, duckdb-query-validation]
  affects: []
tech_stack:
  added: [duckdb-dev-dep, pyarrow-dev-dep]
  patterns: [e2e-pipeline-fixture, zmq-req-rep-query, parquet-validation, duckdb-inline-query]
key_files:
  created:
    - tests/integration/test_e2e_pipeline.py
  modified:
    - pyproject.toml
    - uv.lock
decisions:
  - "Added duckdb and pyarrow as root dev dependencies for integration tests (were only in ems-logger sub-package)"
  - "Used system_total_soc as query signal (matches actual Parquet schema field name, not generic pack_soc)"
  - "ZMQ query API test uses encode_command_request/decode_command_response from ems_common.ipc (matches QueryServer protocol)"
  - "sys.path manipulation used to import conftest module constants (BUILD_DIR, PROFILES, ModuleProcess)"
metrics:
  duration: "3m 37s"
  completed: "2026-03-14"
  tasks_completed: 1
  tasks_total: 1
---

# Phase 13 Plan 03: End-to-End Pipeline Tests Summary

7 integration tests validating full data pipeline from simulator input through Parquet persistence to DuckDB query API response.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create end-to-end pipeline tests | e470806 | tests/integration/test_e2e_pipeline.py, pyproject.toml, uv.lock |

## Test Coverage

### TestEndToEndPipeline (7 tests)

| # | Test | What It Validates |
|---|------|-------------------|
| 1 | test_rtdb_has_data | CAN cell voltages + Modbus PCS fields non-zero in RTDB |
| 2 | test_parquet_files_created | At least 1 Parquet file with >= 25 rows, ts column present |
| 3 | test_parquet_data_matches_simulator | system_total_soc values in 20-80 +/- 1% range via DuckDB |
| 4 | test_duckdb_query_returns_results | DuckDB view over Parquet: row count > 0, time span >= 20s |
| 5 | test_jsonl_events_valid | JSONL lines parse as JSON with ts, src, event_type fields |
| 6 | test_zmq_telemetry_received | ZMQ SUB receives multipart telemetry, msgpack-decodable |
| 7 | test_duckdb_query_via_zmq_api | REQ/REP to SOCK_LOGGER_QUERY returns time_series with SOC data |

### pipeline_env Fixture (class scope)

Launches modules in systemd order: data_manager_c -> data_manager_python -> config_manager -> safety_manager -> comm_manager_c -> comm_manager_python -> logger. Starts CAN and Modbus simulators. 35-second warm-up for data flow. Deterministic seed (42). Temp directory isolation for Parquet/JSONL output. Teardown kills simulators then modules in reverse order with process group cleanup.

### Graceful Skip Conditions

- data_manager_c binary not found -> skip RTDB tests
- vcan0 not available -> skip CAN simulator and comm_manager_c
- Logger not available -> skip Parquet/DuckDB/JSONL/ZMQ query tests
- ZMQ endpoints unreachable -> skip with descriptive message

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added duckdb and pyarrow as root dev dependencies**
- **Found during:** Task 1
- **Issue:** duckdb and pyarrow were only dependencies of ems-logger sub-package, not available in root workspace where pytest runs
- **Fix:** `uv add --dev duckdb pyarrow`
- **Files modified:** pyproject.toml, uv.lock
- **Commit:** e470806

**2. [Rule 1 - Bug] Fixed conftest import path**
- **Found during:** Task 1
- **Issue:** `from conftest import ...` fails because pytest conftest.py is not a regular importable module
- **Fix:** Used sys.path manipulation to add integration test directory to import path
- **Files modified:** tests/integration/test_e2e_pipeline.py
- **Commit:** e470806

**3. [Rule 1 - Bug] Used correct schema field name**
- **Found during:** Task 1
- **Issue:** Plan references `pack_soc` but actual system Parquet schema uses `system_total_soc`
- **Fix:** Used `system_total_soc` in DuckDB queries and ZMQ API requests
- **Files modified:** tests/integration/test_e2e_pipeline.py
- **Commit:** e470806

## Verification

```
$ uv run python -c "import ast; ast.parse(open('tests/integration/test_e2e_pipeline.py').read()); print('syntax ok')"
syntax ok

$ uv run pytest tests/integration/test_e2e_pipeline.py --collect-only
collected 7 items
  <Class TestEndToEndPipeline>
    <Function test_rtdb_has_data>
    <Function test_parquet_files_created>
    <Function test_parquet_data_matches_simulator>
    <Function test_duckdb_query_returns_results>
    <Function test_jsonl_events_valid>
    <Function test_zmq_telemetry_received>
    <Function test_duckdb_query_via_zmq_api>
```
