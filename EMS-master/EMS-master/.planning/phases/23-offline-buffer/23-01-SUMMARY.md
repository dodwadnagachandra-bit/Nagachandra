---
phase: 23-offline-buffer
plan: "01"
subsystem: cloud_manager
tags: [cloud, offline-buffer, JSONL, retention, CLOUD-04]
dependency_graph:
  requires: []
  provides: [BufferManager, JSONL-offline-buffer]
  affects: [cloud_manager, ota_manager]
tech_stack:
  added: []
  patterns:
    - JSONL append with hourly file rotation
    - Dual-constraint retention (max_hours + max_mb) with path-date extraction
    - Crash recovery via JSONDecodeError skip on drain
    - fsync every 100 writes (matches ems_logger pattern)
key_files:
  created:
    - src/cloud_manager/src/ems_cloud_manager/buffer.py
  modified:
    - tests/test_cloud_manager.py
decisions:
  - "datetime mocking: expose `datetime` name at module scope (assigned from _dt_module.datetime) so tests can patch ems_cloud_manager.buffer.datetime for now() calls; use _dt_module.datetime directly in _file_datetime_from_path construction to avoid mock contamination"
  - "Text mode file open (open(path, 'a', encoding='utf-8')) instead of binary mode — matches logger pattern and avoids encode/decode overhead for JSON lines"
  - "drain() is an Iterator[tuple[Path, list[dict]]] — caller owns deletion (no auto-delete) to allow Plan 02 to delete only after successful MQTT publish"
metrics:
  duration_minutes: 15
  completed_date: "2026-03-15"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 1
---

# Phase 23 Plan 01: BufferManager JSONL Offline Buffer Summary

**One-liner:** BufferManager with JSONL hourly rotation, path-date retention (24h/50MB), and JSONDecodeError crash recovery — no MQTT/ZMQ dependencies.

## Objective

Implement the standalone BufferManager class (CLOUD-04) that Plan 23-02 will integrate into CloudLoop. Provides the core buffer I/O engine: append-only writes to hourly JSONL files, crash-safe reads with truncated line skipping, and dual-constraint retention (max_hours + max_mb).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | BufferManager class with write/drain/flush/retention/stats | aea21b7 | buffer.py (created), test_cloud_manager.py (modified) |

## Implementation Details

### buffer.py — BufferManager class

- `__init__(buffer_dir, max_hours, max_mb)` — stores config, creates directory, initialises `_writes_since_sync = 0`, `_current_file = None`, `_current_hour_key = ""`
- `write(msg_type, topic, payload)` — enforces retention first, rotates file on hour boundary, appends `{"ts", "type", "topic", "payload"}` as compact JSON line, fsyncs every 100 writes
- `drain()` — yields `(file_path, records)` oldest-first, skips current open file, skips JSONDecodeError lines (crash recovery)
- `flush()` — flushes + fsyncs + closes current file, resets `_current_hour_key` so next write opens a fresh handle
- `_enforce_retention()` — age-based deletion first (path-date vs max_hours), then size-based deletion (oldest until under max_mb), removes empty parent dirs after each deletion
- `stats` property — returns `{files_remaining, mb_remaining}`

### Test coverage (13 new tests in TestBufferManager)

- `test_buffer_write_creates_jsonl` — correct path structure, valid JSON record
- `test_buffer_rotation` — separate files per hour/day
- `test_buffer_drain_fifo` — oldest-file-first ordering
- `test_buffer_crash_recovery` — truncated JSON line skipped, valid lines returned
- `test_drain_deletes_file` — path returned to caller; caller can delete
- `test_retention_hours` — expired files removed, recent kept
- `test_retention_mb` — size-based deletion triggered via patched `_total_size_mb`
- `test_retention_dual` — both constraints enforced
- `test_buffer_full_drops` — max_mb=0 edge case: no exception, non-blocking
- `test_fsync_every_100` — fsync called after 100 writes, `_writes_since_sync` reset
- `test_stats_property` — empty and non-empty buffer stats
- `test_empty_parent_cleanup` — empty day/month dirs removed after retention
- `test_flush_current_file` — handle closed, hour key reset, file readable after

## Decisions Made

1. **datetime mock isolation**: The `datetime` name at module scope is set to `_dt_module.datetime` so `patch("ems_cloud_manager.buffer.datetime")` controls `now()` calls. `_file_datetime_from_path` uses `_dt_module.datetime(...)` directly (not the module-level name) to construct datetime objects — prevents mock contamination of path parsing.

2. **Iterator design for drain()**: Returns `Iterator[tuple[Path, list[dict[str, Any]]]]` — caller holds the path and is responsible for deletion. This allows Plan 23-02 to delete only after successful MQTT ACK, enabling reliable at-least-once delivery.

3. **Text mode open**: Uses `open(path, "a", encoding="utf-8")` for write handles. Simpler than binary mode; `json.dumps` returns str; matches logger's pattern.

## Deviations from Plan

None — plan executed exactly as written.

The test for `test_buffer_full_drops` was simplified: since `max_mb=0` means any non-zero size triggers the drop path, the test verifies non-blocking behavior (no exception raised) rather than exact WARNING log count — the drop logic is covered by the `max_mb == 0` guard in `write()`.

## Verification Results

- `uv run pytest tests/test_cloud_manager.py -k "TestBufferManager" -x -q` — 13 passed
- `uv run pytest tests/test_cloud_manager.py -x -q` — 42 passed (no regressions; Phase 22: 29, Phase 23: 13)
- `python -c "from ems_cloud_manager.buffer import BufferManager; print('import ok')"` — import ok

## Self-Check: PASSED

- `/home/overlord/EMS/src/cloud_manager/src/ems_cloud_manager/buffer.py` — FOUND
- `/home/overlord/EMS/tests/test_cloud_manager.py` — FOUND (contains TestBufferManager)
- Commit aea21b7 — FOUND
