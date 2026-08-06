---
phase: 07
slug: gpio-test-harness
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-13
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (already configured) |
| **Config file** | pyproject.toml [tool.pytest] |
| **Quick run command** | `uv run pytest tests/test_gpio_harness.py -x -q` |
| **Full suite command** | `uv run pytest -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_gpio_harness.py -x -q`
- **After every plan wave:** Run `uv run pytest -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_set_di_all_pins -x` | No -- W0 | pending |
| 07-01-02 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_get_do_all_pins -x` | No -- W0 | pending |
| 07-01-03 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_estop_dual_channel -x` | No -- W0 | pending |
| 07-01-04 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_fire_dual_confirm -x` | No -- W0 | pending |
| 07-01-05 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_flood_signal -x` | No -- W0 | pending |
| 07-01-06 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_acdb_feedback -x` | No -- W0 | pending |
| 07-01-07 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_pin_name_resolution -x` | No -- W0 | pending |
| 07-01-08 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_logical_polarity -x` | No -- W0 | pending |
| 07-01-09 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_multi_pin_atomic -x` | No -- W0 | pending |
| 07-01-10 | 01 | 1 | SIM-05 | integration | `uv run pytest tests/test_gpio_harness.py::test_cli_set_get -x` | No -- W0 | pending |
| 07-01-11 | 01 | 1 | SIM-05 | unit | `uv run pytest tests/test_gpio_harness.py::test_backend_autodetect -x` | No -- W0 | pending |
| 07-01-12 | 01 | 1 | SIM-05 | integration | `uv run pytest tests/test_gpio_harness.py -m gpio_sim -x` | No -- W0 | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_gpio_harness.py` — stubs for SIM-05 (all DI/DO, CLI, backend tests)
- [ ] SharedMemory test fixture (create/teardown RTDB shm per test)

*Existing infrastructure covers framework install (pytest already in workspace).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| gpio-sim backend (kernel module) | SIM-05 | Requires root + kernel >= 5.17 | `sudo modprobe gpio-sim && uv run pytest tests/test_gpio_harness.py -m gpio_sim -x` |

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
