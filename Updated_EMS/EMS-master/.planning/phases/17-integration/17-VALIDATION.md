---
phase: 17
slug: integration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 17 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (root workspace) |
| **Quick run command** | `cd /home/overlord/EMS && uv run pytest tests/integration/test_m2_integration.py -v -m integration --timeout=300 -x` |
| **Full suite command** | `cd /home/overlord/EMS && uv run pytest tests/integration/ -v -m integration --timeout=900` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/integration/test_m2_integration.py -v -m integration --timeout=300 -x`
- **After every plan wave:** Run `uv run pytest tests/integration/ -v -m integration --timeout=900`
- **Before `/gsd:verify-work`:** Full integration suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 17-01-01 | 01 | 1 | SC-1, SC-4 | integration | `uv run pytest tests/integration/test_startup.py tests/integration/test_crash_recovery.py -v -m integration --timeout=300 -x` | Extend existing | ⬜ pending |
| 17-01-02 | 01 | 1 | CTRL-02, ALM-02, ALM-08 | integration | `uv run pytest tests/integration/test_m2_integration.py::TestProtectionFlow -v --timeout=300 -x` | ❌ W0 | ⬜ pending |
| 17-01-03 | 01 | 1 | CTRL-01, CTRL-03, CTRL-04 | integration | `uv run pytest tests/integration/test_m2_integration.py::TestDispatchFlow -v --timeout=300 -x` | ❌ W0 | ⬜ pending |
| 17-01-04 | 01 | 1 | CTRL-11, ALM-09 | integration | `uv run pytest tests/integration/test_m2_integration.py::TestHotReload -v --timeout=300 -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/integration/test_m2_integration.py` — new file covering protection flow, dispatch flow, hot-reload
- [ ] Extend `tests/integration/test_crash_recovery.py` — add control_manager + alarm_manager to CRASH_MATRIX
- [ ] Extend `tests/integration/test_startup.py` — add M2 modules to startup order

*Wave 0 creates test stubs and extends existing test infrastructure.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification via integration tests.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
