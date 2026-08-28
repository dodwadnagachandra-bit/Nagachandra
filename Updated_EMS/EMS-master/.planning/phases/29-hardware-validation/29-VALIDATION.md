---
phase: 29
slug: hardware-validation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-16
---

# Phase 29 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.0+ with pytest-timeout 2.4.0 |
| **Config file** | `pyproject.toml` (workspace root `[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest tests/hw/ -x --timeout=120` |
| **Full suite command** | `uv run pytest tests/hw/ -v --timeout=3600` |
| **Estimated runtime** | ~30 minutes (excluding soak test) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/hw/test_boot.py -x --timeout=120`
- **After every plan wave:** Run `uv run pytest tests/hw/ -v --ignore=tests/hw/test_soak.py --timeout=3600`
- **Before `/gsd:verify-work`:** Full suite + soak + oscilloscope measurements documented
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 29-01-01 | 01 | 1 | PROD-07 | integration | `uv run pytest tests/hw/test_boot.py -x` | ❌ W0 | ⬜ pending |
| 29-01-02 | 01 | 1 | PROD-07 | integration | `uv run pytest tests/hw/test_drivers.py -x` | ❌ W0 | ⬜ pending |
| 29-01-03 | 01 | 1 | PROD-07 | integration | `uv run pytest tests/hw/test_datapath.py -x` | ❌ W0 | ⬜ pending |
| 29-01-04 | 01 | 1 | PROD-07 | manual | Oscilloscope procedure | N/A | ⬜ pending |
| 29-01-05 | 01 | 1 | PROD-07 | integration | `uv run pytest tests/hw/test_benchmarks.py -x` | ❌ W0 | ⬜ pending |
| 29-01-06 | 01 | 1 | PROD-07 | integration | `uv run pytest tests/hw/test_soak.py --timeout=90000` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/hw/__init__.py` — package init
- [ ] `tests/hw/conftest.py` — ECU SSH fixture, shared constants
- [ ] `tests/hw/test_boot.py` — Stage 1: service health checks
- [ ] `tests/hw/test_drivers.py` — Stage 2: CAN, RS485, GPIO driver tests
- [ ] `tests/hw/test_datapath.py` — Stage 3: data pipeline integrity
- [ ] `tests/hw/test_benchmarks.py` — Stage 5: ARM64 performance benchmarks
- [ ] `tests/hw/test_soak.py` — Stage 6: 24-hour soak test
- [ ] `tools/hw-validation/` — bash wrappers and procedures

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| GPIO DO asserts correct voltage | PROD-07 | Requires multimeter | Measure DO pin voltage on command |
| Safety GPIO response <100ms p99 | PROD-07 | Requires oscilloscope | Trigger E-Stop, measure GPIO→PCS-stop latency on scope |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
