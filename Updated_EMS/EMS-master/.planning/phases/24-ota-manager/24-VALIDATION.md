---
phase: 24
slug: ota-manager
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 24 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest tests/test_ota_manager.py -x` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_ota_manager.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x -m 'not integration and not slow and not rtu'`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 24-01-01 | 01 | 1 | OTA-01 | unit | `uv run pytest tests/test_ota_manager.py::test_http_download_sha256_ok -x` | ❌ W0 | ⬜ pending |
| 24-01-02 | 01 | 1 | OTA-01 | unit | `uv run pytest tests/test_ota_manager.py::test_download_resume_range_header -x` | ❌ W0 | ⬜ pending |
| 24-01-03 | 01 | 1 | OTA-01 | unit | `uv run pytest tests/test_ota_manager.py::test_download_size_limit_rejected -x` | ❌ W0 | ⬜ pending |
| 24-01-04 | 01 | 1 | OTA-02 | unit | `uv run pytest tests/test_ota_manager.py::test_ed25519_verify_valid -x` | ❌ W0 | ⬜ pending |
| 24-01-05 | 01 | 1 | OTA-02 | unit | `uv run pytest tests/test_ota_manager.py::test_ed25519_verify_invalid_raises -x` | ❌ W0 | ⬜ pending |
| 24-02-01 | 02 | 1 | OTA-03 | unit | `uv run pytest tests/test_ota_manager.py::test_boot_flag_rw -x` | ❌ W0 | ⬜ pending |
| 24-02-02 | 02 | 1 | OTA-03 | unit | `uv run pytest tests/test_ota_manager.py::test_state_machine_happy_path -x` | ❌ W0 | ⬜ pending |
| 24-03-01 | 03 | 2 | OTA-04 | unit | `uv run pytest tests/test_ota_manager.py::test_health_check_passes -x` | ❌ W0 | ⬜ pending |
| 24-03-02 | 03 | 2 | OTA-04 | unit | `uv run pytest tests/test_ota_manager.py::test_health_check_timeout_rollback -x` | ❌ W0 | ⬜ pending |
| 24-03-03 | 03 | 2 | OTA-05 | unit | `uv run pytest tests/test_ota_manager.py::test_status_published_on_state_change -x` | ❌ W0 | ⬜ pending |
| 24-03-04 | 03 | 2 | OTA-06 | unit | `uv run pytest tests/test_ota_manager.py::test_version_query_zmq_rep -x` | ❌ W0 | ⬜ pending |
| 24-03-05 | 03 | 2 | OTA-06 | unit | `uv run pytest tests/test_ota_manager.py::test_version_state_persistence -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_ota_manager.py` — stubs for OTA-01 through OTA-06 unit tests
- [ ] `config/ota_config.yaml` — OTA configuration file
- [ ] `config/schemas/ota_config.schema.json` — OTA config JSON Schema
- [ ] `SOCK_OTA_PUB`, `TOPIC_OTA`, `SOCK_OTA_CMD` constants in `ipc.py`
- [ ] `src/ota_manager/pyproject.toml` updated with deps (cryptography, httpx, pyzmq, pyyaml, jsonschema)
- [ ] Test Ed25519 key pair generated in fixtures

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Actual partition write + reboot | OTA-03 | Requires real A/B partitions | Flash test image to standby, swap flag, reboot, verify new version boots |
| Real health check after reboot | OTA-04 | Requires full EMS stack running | After OTA reboot, verify all services start within 300s timeout |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
