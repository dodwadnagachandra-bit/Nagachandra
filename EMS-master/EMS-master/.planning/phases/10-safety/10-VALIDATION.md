---
phase: 10
slug: safety
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (Python integration) + CTest (C unit tests) |
| **Config file** | `pyproject.toml` (pytest section) + `src/safety_manager/tests/CMakeLists.txt` |
| **Quick run command** | `uv run pytest tests/test_safety_manager.py -x` |
| **Full suite command** | `uv run pytest tests/ -x && cmake --build build && ctest --test-dir build` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_safety_manager.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x && cmake --build build && ctest --test-dir build`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 10-01-01 | 01 | 1 | SAFE-01 | integration | `uv run pytest tests/test_safety_manager.py::test_estop_dual_channel -x` | ❌ W0 | ⬜ pending |
| 10-01-02 | 01 | 1 | SAFE-02 | integration | `uv run pytest tests/test_safety_manager.py::test_estop_response_outputs -x` | ❌ W0 | ⬜ pending |
| 10-01-03 | 01 | 1 | SAFE-03 | integration | `uv run pytest tests/test_safety_manager.py::test_fire_dual_confirm -x` | ❌ W0 | ⬜ pending |
| 10-01-04 | 01 | 1 | SAFE-04 | integration | `uv run pytest tests/test_safety_manager.py::test_flood_response -x` | ❌ W0 | ⬜ pending |
| 10-01-05 | 01 | 1 | SAFE-05 | unit (C) | `ctest --test-dir build -R test_watchdog` | ❌ W0 | ⬜ pending |
| 10-01-06 | 01 | 1 | SAFE-06 | unit (C) | `ctest --test-dir build -R test_rt_setup` | ❌ W0 | ⬜ pending |
| 10-01-07 | 01 | 1 | SAFE-07 | integration | `uv run pytest tests/test_safety_manager.py::test_watchdog_thread_priority -x` | ❌ W0 | ⬜ pending |
| 10-01-08 | 01 | 1 | SAFE-08 | integration | `uv run pytest tests/test_safety_manager.py::test_rtdb_gpio_writes -x` | ❌ W0 | ⬜ pending |
| 10-01-09 | 01 | 1 | SAFE-09 | integration | `uv run pytest tests/test_safety_manager.py::test_zmq_safety_events -x` | ❌ W0 | ⬜ pending |
| 10-01-10 | 01 | 1 | SAFE-10 | integration | `uv run pytest tests/test_safety_manager.py::test_independent_lifecycle -x` | ❌ W0 | ⬜ pending |
| 10-01-11 | 01 | 1 | SAFE-11 | integration | `uv run pytest tests/test_safety_manager.py::test_gpio_failure_response -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_safety_manager.py` — integration tests using GPIO harness RTDB backend
- [ ] `src/safety_manager/tests/CMakeLists.txt` — C unit test build config
- [ ] `src/safety_manager/tests/test_response_matrix.c` — pure C unit tests for response matrix logic
- [ ] libgpiod-dev install: `sudo pacman -S libgpiod` (Arch) or `sudo apt install libgpiod-dev` (Ubuntu)
- [ ] CMakeLists.txt update: link gpiod, zmq, mpack, ems_rtdb, pthread, rt

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| <100ms GPIO response on real hardware | SAFE-02 | Requires ECU-1170-552A hardware GPIO | Measure with oscilloscope: assert DI, observe DO transition time |
| SCHED_FIFO priority enforcement | SAFE-06 | Requires root + RT kernel for real priority scheduling | Run as root with `chrt -f 80`, verify via `/proc/[pid]/sched` |
| Hardware watchdog reboot | SAFE-05 | Requires /dev/watchdog device | Stop safety_manager, verify system reboots within timeout |

*All other phase behaviors have automated verification via GPIO harness RTDB backend.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
