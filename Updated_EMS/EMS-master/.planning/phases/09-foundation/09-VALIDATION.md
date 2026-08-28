---
phase: 9
slug: foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-13
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/test_config_manager.py tests/test_data_manager.py -x` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_config_manager.py tests/test_data_manager.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | CONF-01 | unit | `uv run pytest tests/test_config_manager.py::test_startup_all_valid -x` | ❌ W0 | ⬜ pending |
| 09-01-02 | 01 | 1 | CONF-01 | unit | `uv run pytest tests/test_config_manager.py::test_startup_invalid_fails_fast -x` | ❌ W0 | ⬜ pending |
| 09-01-03 | 01 | 1 | CONF-03 | unit | `uv run pytest tests/test_config_manager.py::test_schema_version_mismatch -x` | ❌ W0 | ⬜ pending |
| 09-01-04 | 01 | 1 | CONF-06 | unit | `uv run pytest tests/test_config_manager.py::test_profile_overlay -x` | ❌ W0 | ⬜ pending |
| 09-01-05 | 01 | 1 | CONF-05 | unit | `uv run pytest tests/test_config_manager.py::test_query_get_config -x` | ❌ W0 | ⬜ pending |
| 09-01-06 | 01 | 1 | CONF-05 | unit | `uv run pytest tests/test_config_manager.py::test_query_get_value -x` | ❌ W0 | ⬜ pending |
| 09-01-07 | 01 | 1 | CONF-05 | unit | `uv run pytest tests/test_config_manager.py::test_query_missing_path_error -x` | ❌ W0 | ⬜ pending |
| 09-01-08 | 01 | 1 | CONF-07 | unit | `uv run pytest tests/test_config_manager.py::test_cli_validate -x` | ❌ W0 | ⬜ pending |
| 09-02-01 | 02 | 1 | CONF-02 | integration | `uv run pytest tests/test_config_manager.py::test_hot_reload_within_1s -x` | ❌ W0 | ⬜ pending |
| 09-02-02 | 02 | 1 | CONF-02 | unit | `uv run pytest tests/test_config_manager.py::test_hot_reload_rejects_invalid -x` | ❌ W0 | ⬜ pending |
| 09-02-03 | 02 | 1 | CONF-04 | unit | `uv run pytest tests/test_config_manager.py::test_backup_on_reload -x` | ❌ W0 | ⬜ pending |
| 09-02-04 | 02 | 1 | CONF-08 | unit | `uv run pytest tests/test_config_manager.py::test_reload_event_includes_diff -x` | ❌ W0 | ⬜ pending |
| 09-03-01 | 03 | 1 | DATA-01 | unit | `uv run pytest tests/test_data_manager.py::test_shm_create -x` | ❌ W0 | ⬜ pending |
| 09-03-02 | 03 | 1 | DATA-02 | unit | `uv run pytest tests/test_data_manager.py::test_rtdb_init_header -x` | ❌ W0 | ⬜ pending |
| 09-03-03 | 03 | 1 | DATA-03 | integration | `uv run pytest tests/test_data_manager.py::test_c_python_attach -x` | ❌ W0 | ⬜ pending |
| 09-03-04 | 03 | 1 | DATA-04 | unit | `uv run pytest tests/test_data_manager.py::test_topology_from_config -x` | ❌ W0 | ⬜ pending |
| 09-03-05 | 03 | 1 | DATA-05 | integration | `uv run pytest tests/test_data_manager.py::test_zmq_pub_1hz -x` | ❌ W0 | ⬜ pending |
| 09-03-06 | 03 | 1 | DATA-06 | unit | `uv run pytest tests/test_data_manager.py::test_health_stale_detection -x` | ❌ W0 | ⬜ pending |
| 09-03-07 | 03 | 1 | DATA-07 | unit | `uv run pytest tests/test_data_manager.py::test_systemd_ordering -x` | ❌ W0 | ⬜ pending |
| 09-03-08 | 03 | 1 | DATA-08 | unit | `uv run pytest tests/test_data_manager.py::test_snapshot_periodic -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_config_manager.py` — stubs for CONF-01 through CONF-08
- [ ] `tests/test_data_manager.py` — stubs for DATA-01 through DATA-08
- [ ] `tests/conftest.py` — shared fixtures (tmp config dirs, shm cleanup)
- [ ] `uv add pyzmq --package ems-config-manager && uv add pyzmq --package ems-data-manager`
- [ ] `uv add inotify-simple --package ems-config-manager`
- [ ] SOCK_CONFIG added to ipc.py and ipc_defs.h

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| systemd startup ordering | DATA-07 | Requires real systemd | Deploy unit files, run `systemctl start ems-data-manager`, verify After= ordering |

*All other phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
