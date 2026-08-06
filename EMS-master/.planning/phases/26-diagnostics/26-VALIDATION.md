---
phase: 26
slug: diagnostics
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-16
---

# Phase 26 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.3.x |
| **Config file** | `pyproject.toml` (workspace root `[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest src/diagnostics/tests/ -x -q` |
| **Full suite command** | `uv run pytest src/diagnostics/tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest src/diagnostics/tests/ -x -q`
- **After every plan wave:** Run `uv run pytest src/diagnostics/tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 26-01-01 | 01 | 1 | DIAG-01 | unit | `uv run pytest src/diagnostics/tests/test_soh_analyzer.py -x` | ❌ W0 | ⬜ pending |
| 26-01-02 | 01 | 1 | DIAG-01 | unit | `uv run pytest src/diagnostics/tests/test_loop.py::test_soh_published -x` | ❌ W0 | ⬜ pending |
| 26-01-03 | 01 | 1 | DIAG-02 | unit | `uv run pytest src/diagnostics/tests/test_pcs_analyzer.py -x` | ❌ W0 | ⬜ pending |
| 26-01-04 | 01 | 1 | DIAG-02 | unit | `uv run pytest src/diagnostics/tests/test_pcs_analyzer.py::test_idle_skipped -x` | ❌ W0 | ⬜ pending |
| 26-01-05 | 01 | 1 | DIAG-03 | unit | `uv run pytest src/diagnostics/tests/test_thermal_analyzer.py -x` | ❌ W0 | ⬜ pending |
| 26-01-06 | 01 | 1 | DIAG-03 | unit | `uv run pytest src/diagnostics/tests/test_thermal_analyzer.py::test_fan_score -x` | ❌ W0 | ⬜ pending |
| 26-01-07 | 01 | 1 | DIAG-04 | unit | `uv run pytest src/diagnostics/tests/test_comm_analyzer.py -x` | ❌ W0 | ⬜ pending |
| 26-01-08 | 01 | 1 | DIAG-05 | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_get_current -x` | ❌ W0 | ⬜ pending |
| 26-01-09 | 01 | 1 | DIAG-05 | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_get_report -x` | ❌ W0 | ⬜ pending |
| 26-01-10 | 01 | 1 | DIAG-05 | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_predictions_min_days -x` | ❌ W0 | ⬜ pending |
| 26-01-11 | 01 | 1 | DIAG-06 | unit | `uv run pytest src/diagnostics/tests/test_reporter.py::test_linear_regression -x` | ❌ W0 | ⬜ pending |
| 26-01-12 | 01 | 1 | DIAG-06 | unit | `uv run pytest src/diagnostics/tests/test_loop.py::test_predictive_alert_fires -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `src/diagnostics/tests/__init__.py` — package init
- [ ] `src/diagnostics/tests/test_config.py` — covers config loading + JSON Schema validation
- [ ] `src/diagnostics/tests/test_soh_analyzer.py` — covers DIAG-01
- [ ] `src/diagnostics/tests/test_pcs_analyzer.py` — covers DIAG-02
- [ ] `src/diagnostics/tests/test_thermal_analyzer.py` — covers DIAG-03
- [ ] `src/diagnostics/tests/test_comm_analyzer.py` — covers DIAG-04
- [ ] `src/diagnostics/tests/test_reporter.py` — covers DIAG-05, DIAG-06 predictions
- [ ] `src/diagnostics/tests/test_loop.py` — covers loop integration, ZMQ publish, alert firing
- [ ] `config/diagnostics_config.yaml` — default config for residential profile
- [ ] `config/schemas/diagnostics_config.schema.json` — JSON Schema for config validation

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
