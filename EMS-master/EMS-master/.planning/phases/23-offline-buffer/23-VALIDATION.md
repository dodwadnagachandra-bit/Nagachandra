---
phase: 23
slug: offline-buffer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-15
---

# Phase 23 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `tests/conftest.py` (project root) |
| **Quick run command** | `uv run pytest tests/test_cloud_manager.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_cloud_manager.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q -m 'not integration and not slow and not rtu'`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 23-01-01 | 01 | 1 | CLOUD-04 | unit | `uv run pytest tests/test_cloud_manager.py -k "buffer" -x` | ❌ W0 | ⬜ pending |
| 23-01-02 | 01 | 1 | CLOUD-04 | unit | `uv run pytest tests/test_cloud_manager.py -k "buffer_rotation" -x` | ❌ W0 | ⬜ pending |
| 23-01-03 | 01 | 1 | CLOUD-04 | unit | `uv run pytest tests/test_cloud_manager.py -k "retention_hours" -x` | ❌ W0 | ⬜ pending |
| 23-01-04 | 01 | 1 | CLOUD-04 | unit | `uv run pytest tests/test_cloud_manager.py -k "retention_mb" -x` | ❌ W0 | ⬜ pending |
| 23-01-05 | 01 | 1 | CLOUD-04 | unit | `uv run pytest tests/test_cloud_manager.py -k "crash_recovery" -x` | ❌ W0 | ⬜ pending |
| 23-02-01 | 02 | 2 | CLOUD-05 | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_fifo" -x` | ❌ W0 | ⬜ pending |
| 23-02-02 | 02 | 2 | CLOUD-05 | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_throttle" -x` | ❌ W0 | ⬜ pending |
| 23-02-03 | 02 | 2 | CLOUD-05 | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_interrupt" -x` | ❌ W0 | ⬜ pending |
| 23-02-04 | 02 | 2 | CLOUD-05 | unit | `uv run pytest tests/test_cloud_manager.py -k "buffer_status" -x` | ❌ W0 | ⬜ pending |
| 23-02-05 | 02 | 2 | CLOUD-05 | unit | `uv run pytest tests/test_cloud_manager.py -k "replay_cleanup" -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_cloud_manager.py` — Add `TestBufferManager` and `TestBufferedCloudLoop` test classes
- [ ] `src/cloud_manager/src/ems_cloud_manager/buffer.py` — Does not exist; Wave 0 creates it

*Existing infrastructure (pytest, conftest) covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Long outage + replay timing | CLOUD-05 | Requires real MQTT broker and timed disconnect | Start Mosquitto, connect, kill broker, buffer for 60s, restart broker, verify replay completes |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
