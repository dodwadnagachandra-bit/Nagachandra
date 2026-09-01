---
phase: 11
slug: communications
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework (C)** | Custom assert-based (matching safety_manager pattern) |
| **Framework (Python)** | pytest + pytest-asyncio |
| **Config file (C)** | `src/comm_manager/c/tests/CMakeLists.txt` |
| **Config file (Python)** | Root `pyproject.toml` (pytest section) |
| **Quick run command (C)** | `cd build && ctest -R comm_manager -j4 --output-on-failure` |
| **Quick run command (Python)** | `uv run pytest src/comm_manager/python/tests -x -q` |
| **Full suite command** | `cd build && ctest -j4 --output-on-failure && uv run pytest src/comm_manager/python/tests -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest src/comm_manager/python/tests -x -q` (Python) + `cd build && ctest -R comm -j4 --output-on-failure` (C)
- **After every plan wave:** Run full suite (C + Python)
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 11-01-01 | 01 | 1 | COMM-01 | unit (C) | `ctest -R test_can_decode --output-on-failure` | ❌ W0 | ⬜ pending |
| 11-01-02 | 01 | 1 | COMM-02 | unit (C) | `ctest -R test_can_health --output-on-failure` | ❌ W0 | ⬜ pending |
| 11-01-03 | 01 | 1 | COMM-03 | unit (C) | `ctest -R test_can_decode --output-on-failure` | ❌ W0 | ⬜ pending |
| 11-01-04 | 01 | 1 | COMM-04 | integration | Manual (2 vcan interfaces) | ❌ W0 | ⬜ pending |
| 11-02-01 | 02 | 1 | COMM-05 | unit (Python) | `uv run pytest tests/test_modbus_device.py -x` | ❌ W0 | ⬜ pending |
| 11-02-02 | 02 | 1 | COMM-06 | unit (Python) | `uv run pytest tests/test_health.py -x` | ❌ W0 | ⬜ pending |
| 11-02-03 | 02 | 1 | COMM-07 | unit (Python) | `uv run pytest tests/test_modbus_device.py -x` | ❌ W0 | ⬜ pending |
| 11-02-04 | 02 | 1 | COMM-09 | integration | `uv run pytest tests/test_orchestrator.py -x` | ❌ W0 | ⬜ pending |
| 11-02-05 | 02 | 1 | COMM-10 | unit (Python) | `uv run pytest tests/test_events.py -x` | ❌ W0 | ⬜ pending |
| 11-02-06 | 02 | 1 | COMM-11 | unit (Python) | `uv run pytest tests/test_orchestrator.py -x` | ❌ W0 | ⬜ pending |
| 11-02-07 | 02 | 1 | COMM-12 | unit (Python) | `uv run pytest tests/test_modbus_device.py -x` | ❌ W0 | ⬜ pending |
| 11-03-01 | 03 | 2 | COMM-08 | N/A | Already tested in Phase 10 (SAFE-08) | ✅ Phase 10 | ⬜ pending |
| 11-03-02 | 03 | 2 | COMM-13 | integration | Manual (verify both services start) | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `src/comm_manager/c/tests/CMakeLists.txt` — test build config for C comm tests
- [ ] `src/comm_manager/c/tests/test_can_decode.c` — CAN decode logic tests (COMM-01, COMM-03)
- [ ] `src/comm_manager/c/tests/test_can_health.c` — heartbeat timeout tests (COMM-02)
- [ ] `src/comm_manager/python/tests/conftest.py` — shared fixtures
- [ ] `src/comm_manager/python/tests/test_register_map.py` — register map loader
- [ ] `src/comm_manager/python/tests/test_health.py` — device health state machine (COMM-06)
- [ ] `src/comm_manager/python/tests/test_modbus_device.py` — Modbus device polling (COMM-05, COMM-07, COMM-12)
- [ ] `src/comm_manager/python/tests/test_events.py` — ZMQ event publishing (COMM-10)
- [ ] `src/comm_manager/python/tests/test_orchestrator.py` — orchestrator lifecycle (COMM-09, COMM-11)
- [ ] `config/btms_register_map.yaml` — stub BTMS register map
- [ ] `config/meter_register_map.yaml` — stub Meter register map
- [ ] `config/dg_register_map.yaml` — stub DG register map
- [ ] `config/pv_register_map.yaml` — stub PV register map

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Multi-cluster CAN thread-per-interface | COMM-04 | Requires 2+ vcan interfaces | `sudo ip link add vcan0 type vcan && sudo ip link add vcan1 type vcan && sudo ip link set vcan0 up && sudo ip link set vcan1 up` then run CAN process and verify both threads active |
| Hybrid C+Python independent services | COMM-13 | Requires systemd service start | Start both `comm_manager_c.service` and `comm_manager.service`, verify independent lifecycle with `systemctl status` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
