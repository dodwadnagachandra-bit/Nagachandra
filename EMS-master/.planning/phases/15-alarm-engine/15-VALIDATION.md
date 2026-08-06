---
phase: 15
slug: alarm-engine
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (root) — `[tool.pytest.ini_options]` |
| **Quick run command** | `cd /home/overlord/EMS && uv run pytest src/alarm_manager/tests/ -x -q` |
| **Full suite command** | `cd /home/overlord/EMS && uv run pytest src/alarm_manager/ -v` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest src/alarm_manager/tests/ -x -q`
- **After every plan wave:** Run `uv run pytest src/alarm_manager/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 15-01-01 | 01 | 1 | ALM-01 | unit | `uv run pytest src/alarm_manager/tests/test_resolver.py -x -q` | ❌ W0 | ⬜ pending |
| 15-01-02 | 01 | 1 | ALM-01 | unit | `uv run pytest src/alarm_manager/tests/test_config.py -x -q` | ❌ W0 | ⬜ pending |
| 15-02-01 | 02 | 1 | ALM-03, ALM-04, ALM-05 | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py -x -q` | ❌ W0 | ⬜ pending |
| 15-02-02 | 02 | 1 | ALM-10 | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_disabled_alarm_no_event -x -q` | ❌ W0 | ⬜ pending |
| 15-03-01 | 03 | 2 | ALM-02, ALM-06 | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_alarm_event_payload -x -q` | ❌ W0 | ⬜ pending |
| 15-03-02 | 03 | 2 | ALM-07 | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_get_active_alarms -x -q` | ❌ W0 | ⬜ pending |
| 15-03-03 | 03 | 2 | ALM-07 | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_acknowledge_command -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `src/alarm_manager/tests/__init__.py` — empty marker file
- [ ] `src/alarm_manager/tests/test_config.py` — config loading, schema validation stubs
- [ ] `src/alarm_manager/tests/test_resolver.py` — signal resolution, offline rack exclusion stubs
- [ ] `src/alarm_manager/tests/test_evaluator.py` — AlarmInstance lifecycle, hysteresis, delay stubs
- [ ] `src/alarm_manager/tests/test_loop.py` — ZMQ integration, mock RTDB, command dispatch stubs

*Wave 0 creates all test stubs before implementation begins.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
