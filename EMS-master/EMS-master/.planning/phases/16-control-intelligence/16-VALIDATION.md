---
phase: 16
slug: control-intelligence
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 16 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` per workspace member |
| **Quick run command** | `cd /home/overlord/EMS && uv run pytest src/control_manager/python/tests src/alarm_manager/tests -x -q` |
| **Full suite command** | `cd /home/overlord/EMS && uv run pytest src/control_manager/python/tests src/alarm_manager/tests -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest src/control_manager/python/tests src/alarm_manager/tests -x -q`
- **After every plan wave:** Run `uv run pytest src/control_manager/python/tests src/alarm_manager/tests -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 16-01-01 | 01 | 1 | CTRL-04 | unit | `uv run pytest src/control_manager/python/tests/test_intelligence.py -x -k "source_priority" -q` | ❌ W0 | ⬜ pending |
| 16-01-02 | 01 | 1 | CTRL-05 | unit | `uv run pytest src/control_manager/python/tests/test_intelligence.py -x -k "soc" -q` | ❌ W0 | ⬜ pending |
| 16-01-03 | 01 | 1 | CTRL-06 | unit | `uv run pytest src/control_manager/python/tests/test_intelligence.py -x -k "derating" -q` | ❌ W0 | ⬜ pending |
| 16-01-04 | 01 | 1 | CTRL-08 | unit | `uv run pytest src/control_manager/python/tests/test_intelligence.py -x -k "ramp" -q` | ❌ W0 | ⬜ pending |
| 16-01-05 | 01 | 1 | CTRL-09 | unit | `uv run pytest src/control_manager/python/tests/test_intelligence.py -x -k "interlock" -q` | ❌ W0 | ⬜ pending |
| 16-02-01 | 02 | 2 | CTRL-11 | unit | `uv run pytest src/control_manager/python/tests/test_loop.py -x -k "hot_reload" -q` | ❌ W0 | ⬜ pending |
| 16-02-02 | 02 | 2 | ALM-08 | unit | `uv run pytest src/control_manager/python/tests/test_loop.py -x -k "alarm_protection" -q` | ❌ W0 | ⬜ pending |
| 16-02-03 | 02 | 2 | ALM-09 | unit | `uv run pytest src/alarm_manager/tests/test_loop.py -x -k "hot_reload" -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `src/control_manager/python/tests/test_intelligence.py` — stubs for CTRL-04, CTRL-05, CTRL-06, CTRL-08, CTRL-09
- [ ] Additional test methods in `src/control_manager/python/tests/test_loop.py` — stubs for CTRL-11, ALM-08
- [ ] Additional test methods in `src/alarm_manager/tests/test_loop.py` — stubs for ALM-09
- [ ] Extended `config/schemas/control_config.schema.json` with derating + ramping sections
- [ ] Extended `config/control_config.yaml` with derating + ramping sections

*Wave 0 creates test stubs and config extensions before implementation begins.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
