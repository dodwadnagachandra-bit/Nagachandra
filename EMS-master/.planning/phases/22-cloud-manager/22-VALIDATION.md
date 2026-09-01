---
phase: 22
slug: cloud-manager
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 22 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.3.x |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest tests/test_cloud_manager.py -x` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_cloud_manager.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x -m 'not integration and not slow and not rtu'`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 22-01-01 | 01 | 1 | CLOUD-01 | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_mtls_connect -x` | ❌ W0 | ⬜ pending |
| 22-01-02 | 01 | 1 | CLOUD-01 | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_reconnect_backoff -x` | ❌ W0 | ⬜ pending |
| 22-01-03 | 01 | 1 | CLOUD-01 | unit | `uv run pytest tests/test_cloud_manager.py::test_config_cert_validation -x` | ❌ W0 | ⬜ pending |
| 22-02-01 | 02 | 2 | CLOUD-02 | unit | `uv run pytest tests/test_cloud_manager.py::test_telemetry_accumulator -x` | ❌ W0 | ⬜ pending |
| 22-02-02 | 02 | 2 | CLOUD-02 | unit (mock timer) | `uv run pytest tests/test_cloud_manager.py::test_periodic_publish -x` | ❌ W0 | ⬜ pending |
| 22-02-03 | 02 | 2 | CLOUD-02 | unit | `uv run pytest tests/test_cloud_manager.py::test_missing_topics_omitted -x` | ❌ W0 | ⬜ pending |
| 22-02-04 | 02 | 2 | CLOUD-03 | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_event_qos1 -x` | ❌ W0 | ⬜ pending |
| 22-03-01 | 03 | 2 | CLOUD-06 | unit (mock ZMQ) | `uv run pytest tests/test_cloud_manager.py::test_command_dispatch -x` | ❌ W0 | ⬜ pending |
| 22-03-02 | 03 | 2 | CLOUD-06 | unit | `uv run pytest tests/test_cloud_manager.py::test_command_invalid_rejected -x` | ❌ W0 | ⬜ pending |
| 22-03-03 | 03 | 2 | CLOUD-06 | unit | `uv run pytest tests/test_cloud_manager.py::test_command_rate_limit -x` | ❌ W0 | ⬜ pending |
| 22-03-04 | 03 | 2 | CLOUD-07 | unit (mock timer) | `uv run pytest tests/test_cloud_manager.py::test_heartbeat_payload -x` | ❌ W0 | ⬜ pending |
| 22-01-04 | 01 | 1 | CLOUD-08 | unit (mock paho) | `uv run pytest tests/test_cloud_manager.py::test_connection_status_zmq -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_cloud_manager.py` — stubs for CLOUD-01 through CLOUD-08 unit tests (mock paho.mqtt.client)
- [ ] `tests/conftest.py` — add mock_paho_client fixture
- [ ] `uv add paho-mqtt>=2.1.0,<3.0` in `src/cloud_manager/pyproject.toml`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| mTLS handshake with real broker | CLOUD-01 | Requires TLS certs + broker | Generate self-signed certs, start Mosquitto with TLS, verify connect |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
