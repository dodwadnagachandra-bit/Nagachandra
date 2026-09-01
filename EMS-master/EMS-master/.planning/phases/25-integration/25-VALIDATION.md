---
phase: 25
slug: integration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 25 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest tests/integration/test_m4_integration.py -v -m integration --timeout=300` |
| **Full suite command** | `uv run pytest tests/integration/ -v -m integration --timeout=900` |
| **Estimated runtime** | ~120 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/integration/test_m4_integration.py -v -m integration --timeout=300`
- **After every plan wave:** Run `uv run pytest tests/integration/ -v -m integration --timeout=900`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 25-01-01 | 01 | 1 | CLOUD-01,02,03,07,08 | integration | `uv run pytest tests/integration/test_m4_integration.py::TestM4Startup tests/integration/test_m4_integration.py::TestE2ERemoteCommand -v -m integration --timeout=120` | :x: W0 | :white_large_square: pending |
| 25-01-02 | 01 | 1 | CLOUD-06 | integration | `uv run pytest tests/integration/test_m4_integration.py::TestE2ERemoteCommand -v -m integration --timeout=120` | :x: W0 | :white_large_square: pending |
| 25-02-01 | 02 | 2 | CLOUD-04,05 | integration | `uv run pytest tests/integration/test_m4_integration.py::TestOfflineTransition -v -m integration --timeout=180` | :x: W0 | :white_large_square: pending |
| 25-02-02 | 02 | 2 | OTA-01..06 | integration | `uv run pytest tests/integration/test_m4_integration.py::TestOtaCycle -v -m integration --timeout=120` | :x: W0 | :white_large_square: pending |
| 25-02-03 | 02 | 2 | — | integration | `uv run pytest tests/integration/test_m4_integration.py::TestM4CrashRecovery -v -m integration --timeout=120` | :x: W0 | :white_large_square: pending |

*Status: :white_large_square: pending · :white_check_mark: green · :x: red · :warning: flaky*

---

## Timeout Exceptions

| Task ID | Timeout | Exceeds 120s? | Justification |
|---------|---------|---------------|---------------|
| 25-02-01 | 180s | Yes | TestOfflineTransition requires a 30-second offline accumulation period (per locked decision: 30s offline at 10s telemetry interval = ~3 buffered messages) followed by a 60-second buffer drain window (buffer replay has 5s backoff per Pitfall 6 from RESEARCH.md). The 30s offline + 60s drain = 90s minimum test body, plus module startup overhead, makes 120s infeasible. 180s provides adequate margin. |

---

## Wave 0 Requirements

- [ ] `tests/integration/test_m4_integration.py` — all M4 integration tests
- [ ] Mosquitto added to `make setup` apt-get line

*Existing infrastructure (conftest.py, ModuleProcess, port allocation) covers all framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real MQTT/TLS with cloud broker | CLOUD-01 | Requires production certs + cloud broker | Configure real broker URL + certs, verify connect + telemetry flow |
| Real A/B partition OTA | OTA-03 | Requires ECU hardware | Flash update to standby, reboot, verify new version boots |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s (with documented exceptions)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
