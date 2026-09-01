---
phase: 14
slug: control-state-machine
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 14 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.3.x |
| **Config file** | `pyproject.toml` (root) — `[tool.pytest.ini_options]` |
| **Quick run command** | `cd /home/overlord/EMS && uv run pytest src/control_manager/python/tests/ -x -q` |
| **Full suite command** | `cd /home/overlord/EMS && uv run pytest tests/ src/control_manager/python/tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest src/control_manager/python/tests/ -x -q`
- **After every plan wave:** Run `uv run pytest tests/ src/control_manager/python/tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 14-01-01 | 01 | 1 | CTRL-01 | unit | `uv run pytest tests/test_rtdb.py -x -q` | Exists (needs update) | ⬜ pending |
| 14-02-01 | 02 | 1 | CTRL-02 | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py -x -q` | ❌ W0 | ⬜ pending |
| 14-02-02 | 02 | 1 | CTRL-02 | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_state_change_event -x -q` | ❌ W0 | ⬜ pending |
| 14-03-01 | 03 | 2 | CTRL-03 | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py::test_pcs_command_dispatch -x -q` | ❌ W0 | ⬜ pending |
| 14-03-02 | 03 | 2 | CTRL-03 | unit | `uv run pytest src/comm_manager/python/tests/test_pcs_device.py -x -q` | Exists (needs new tests) | ⬜ pending |
| 14-04-01 | 04 | 2 | CTRL-07 | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py::test_fault_handling -x -q` | ❌ W0 | ⬜ pending |
| 14-04-02 | 04 | 2 | CTRL-07 | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py::test_fault_reset_override -x -q` | ❌ W0 | ⬜ pending |
| 14-05-01 | 05 | 3 | CTRL-10 | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_zmq_command_api -x -q` | ❌ W0 | ⬜ pending |
| 14-05-02 | 05 | 3 | CTRL-10 | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_command_rejected_during_transition -x -q` | ❌ W0 | ⬜ pending |
| 14-06-01 | 06 | 3 | CTRL-12 | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_telemetry_publish -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `src/control_manager/python/tests/__init__.py` — test package init
- [ ] `src/control_manager/python/tests/test_state_machine.py` — stubs for CTRL-02, CTRL-03, CTRL-07
- [ ] `src/control_manager/python/tests/test_loop.py` — stubs for CTRL-01, CTRL-10, CTRL-12
- [ ] `src/control_manager/python/tests/test_config.py` — config load and validation stubs
- [ ] Update `tests/test_rtdb.py::C_SIZEOF_RTDB` constant after rtdb.h struct change
- [ ] Update `src/comm_manager/python/tests/test_pcs_device.py` with write_setpoint/process_command tests

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
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
