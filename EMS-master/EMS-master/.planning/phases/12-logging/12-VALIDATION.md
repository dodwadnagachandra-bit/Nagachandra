---
phase: 12
slug: logging
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 12 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio |
| **Config file** | pyproject.toml [tool.pytest.ini_options] |
| **Quick run command** | `uv run pytest src/logger/python/tests/test_logger.py -x` |
| **Full suite command** | `uv run pytest src/logger/python/tests/ -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest src/logger/python/tests/test_logger.py -x`
- **After every plan wave:** Run `uv run pytest src/logger/python/tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 12-01-01 | 01 | 1 | LOG-01 | unit | `uv run pytest tests/test_logger.py::test_parquet_write_from_telemetry -x` | ❌ W0 | ⬜ pending |
| 12-01-02 | 01 | 1 | LOG-08 | unit | `uv run pytest tests/test_logger.py::test_snappy_compression -x` | ❌ W0 | ⬜ pending |
| 12-01-03 | 01 | 1 | LOG-02 | unit | `uv run pytest tests/test_logger.py::test_parquet_hourly_rotation -x` | ❌ W0 | ⬜ pending |
| 12-01-04 | 01 | 1 | LOG-09 | unit | `uv run pytest tests/test_logger.py::test_parquet_directory_structure -x` | ❌ W0 | ⬜ pending |
| 12-02-01 | 02 | 1 | LOG-04 | unit | `uv run pytest tests/test_logger.py::test_jsonl_event_append -x` | ❌ W0 | ⬜ pending |
| 12-03-01 | 03 | 2 | LOG-03 | unit | `uv run pytest tests/test_logger.py::test_duckdb_time_series_query -x` | ❌ W0 | ⬜ pending |
| 12-04-01 | 04 | 2 | LOG-05 | unit | `uv run pytest tests/test_logger.py::test_retention_expiry -x` | ❌ W0 | ⬜ pending |
| 12-04-02 | 04 | 2 | LOG-06 | unit | `uv run pytest tests/test_logger.py::test_fifo_deletion_order -x` | ❌ W0 | ⬜ pending |
| 12-05-01 | 05 | 2 | LOG-07 | unit | `uv run pytest tests/test_logger.py::test_crash_recovery -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `src/logger/python/tests/test_logger.py` — stubs for LOG-01 through LOG-09
- [ ] `src/logger/python/tests/conftest.py` — shared fixtures (tmp_path data dirs, sample telemetry/event dicts)
- [ ] `cd src/logger/python && uv add pyarrow duckdb` — install dependencies
- [ ] Add `logging` marker to pyproject.toml [tool.pytest.ini_options]

*Wave 0 creates all test stubs before implementation begins.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SSD 80% threshold triggers cleanup | LOG-06 | Requires near-full disk state | Fill tmp disk to 80%, verify cleanup runs |
| Crash mid-write loses ≤1s data | LOG-07 | Requires process kill during write | Kill logger process, verify .tmp cleanup and JSONL integrity |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
