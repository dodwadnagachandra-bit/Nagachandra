---
phase: 8
slug: integration-validation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-13
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/test_integration.py -v -m integration -x` |
| **Full suite command** | `uv run pytest tests/ -v` |
| **Estimated runtime** | ~35 seconds (integration), ~15 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_integration.py -v -m integration -x`
- **After every plan wave:** Run `uv run pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 35 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | 1 | SIM-06 | unit | `uv run pytest tests/test_config_validation.py -x` | Partially | ⬜ pending |
| 08-01-02 | 01 | 1 | SIM-06 | unit | `uv run pytest tests/test_can_simulator.py::test_fault_injection -x` | ❌ W0 | ⬜ pending |
| 08-01-03 | 01 | 1 | SIM-06 | unit | `uv run pytest tests/test_modbus_simulator.py::test_fault_injection -x` | ❌ W0 | ⬜ pending |
| 08-01-04 | 01 | 1 | SIM-06 | unit | `uv run pytest tests/test_gpio_harness.py::test_fault_injection -x` | ❌ W0 | ⬜ pending |
| 08-01-05 | 01 | 1 | SIM-06 | integration | `uv run pytest tests/test_integration.py -v -m integration -x` | ❌ W0 | ⬜ pending |
| 08-01-06 | 01 | 1 | SIM-06 | smoke | `bash tools/sim-all.sh --profile residential` | ❌ W0 | ⬜ pending |
| 08-01-07 | 01 | 1 | SIM-06 | CI | `.github/workflows/pr-check.yml` integration-test job | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_integration.py` — integration smoke tests (SIM-06a through SIM-06d)
- [ ] `integration` marker in `pyproject.toml` pytest markers list
- [ ] vcan0 setup step in CI workflow for CAN integration test

*Existing infrastructure covers config validation (schema tests) and individual simulator unit tests.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| sim-all.sh stdout summary display | SIM-06 | Visual output format check | Run `make sim-all`, verify 3-simulator status line appears |
| Simultaneous dev workstation operation | SIM-06 | Resource contention is hardware-dependent | Run `make sim-all`, monitor CPU/memory for 30s |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 35s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
