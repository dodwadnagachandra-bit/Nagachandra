---
phase: 12-logging
plan: 05
title: "Retention and Cleanup Manager"
subsystem: logger
tags: [retention, cleanup, disk-monitoring, crash-recovery, fifo]
dependency_graph:
  requires: [12-01, 12-02, 12-03]
  provides: [RetentionManager, get_disk_usage_pct, find_expired_parquet, find_expired_jsonl, cleanup_stale_tmp]
  affects: [data/{year}/{month}/{day}/*.parquet, data/events/{year}/{month}/events_*.jsonl]
tech_stack:
  added: []
  patterns: [three-tier-fifo, path-date-parsing, statvfs-disk-monitoring]
key_files:
  created:
    - src/logger/python/src/ems_logger/cleanup.py
  modified:
    - src/logger/python/tests/test_cleanup.py
decisions:
  - "File age from directory path date components, not mtime -- deterministic across filesystems"
  - "Three-tier FIFO: expired Parquet, expired JSONL, survival mode (within-retention oldest-first)"
  - "Empty parent directories removed after file deletion to prevent directory sprawl"
  - "_delete_files stops early on each file check when disk drops below threshold"
metrics:
  duration: "3m 15s"
  completed: "2026-03-14T10:23:19Z"
  tasks: 2
  tests: 12
  files_created: 1
  files_modified: 1
  total_lines: 748
requirements:
  - LOG-05
  - LOG-06
  - LOG-07
---

# Phase 12 Plan 05: Retention and Cleanup Manager Summary

Three-tier FIFO cleanup with path-based date parsing, os.statvfs() disk monitoring, and crash recovery for stale .tmp files.

## Tasks Completed

| Task | Name | Commit | Tests |
|------|------|--------|-------|
| 1 | Retention expiry and crash recovery for stale .tmp files | 6394a41 | 6 |
| 2 | Three-tier FIFO cleanup with disk monitoring | e1b82d4 | 6 |

## Implementation Details

### Task 1: Retention Expiry and Crash Recovery

Created `cleanup.py` with path-based date parsing:
- `_parse_date_from_parquet_path()` extracts year/month/day from directory structure
- `_parse_date_from_jsonl_path()` extracts date from `events_YYYYMMDD.jsonl` filename
- `find_expired_parquet()` and `find_expired_jsonl()` return oldest-first sorted lists
- `find_stale_tmp_files()` and `cleanup_stale_tmp()` handle crash recovery (LOG-07)
- Invalid/non-dated paths silently skipped (no crashes on unexpected directory structures)

### Task 2: Three-Tier FIFO Cleanup

Added `RetentionManager` class with disk-aware cleanup:
- `get_disk_usage_pct()` uses `os.statvfs()` for POSIX disk stats
- Tier 1: Delete expired Parquet (>90 days), oldest first
- Tier 2: Delete expired JSONL (>180 days), oldest first (only if still above threshold)
- Tier 3: Survival mode -- delete within-retention Parquet first, then JSONL, oldest first
- `_delete_files()` stops after each deletion if disk drops below threshold
- Empty parent directories auto-removed after file deletion
- `run_periodic()` async loop runs cleanup every 5 minutes (configurable)
- `startup_recovery()` calls `cleanup_stale_tmp()` on service start

## Decisions Made

1. **Path-date parsing over mtime**: File age determined from directory path structure (YYYY/MM/DD) for Parquet and filename pattern for JSONL. This is deterministic regardless of filesystem timestamp changes during file copies or backups.
2. **Per-file disk check in _delete_files**: After each file deletion, disk usage is rechecked via statvfs(). This minimizes unnecessary deletions at the cost of one syscall per file -- acceptable since cleanup runs at most every 5 minutes.
3. **Empty directory cleanup**: Parent directories are removed bottom-up after file deletion to prevent accumulation of empty date directories over months of operation.

## Deviations from Plan

None -- plan executed exactly as written.

## Verification

All 12 tests pass:
- 6 tests for retention expiry, FIFO ordering, tmp cleanup, invalid path handling
- 6 tests for disk usage, FIFO deletion order, tier 3 survival, early stop, empty dirs, ordering guarantee
