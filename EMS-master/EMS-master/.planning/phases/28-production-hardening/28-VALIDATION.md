---
phase: 28
slug: production-hardening
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-16
---

# Phase 28 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio (backend), vitest (frontend) |
| **Config file** | `src/hmi_server/pyproject.toml` + `src/hmi_server/frontend/vitest.config.ts` |
| **Quick run command** | `cd src/hmi_server && uv run pytest tests/ -x -q` |
| **Full suite command** | `cd src/hmi_server && uv run pytest tests/ -v && cd frontend && bun run test --run` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd src/hmi_server && uv run pytest tests/ -x -q`
- **After every plan wave:** Run `cd src/hmi_server && uv run pytest tests/ -v && cd frontend && bun run test --run`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 28-01-01 | 01 | 1 | PROD-05 | integration | `cd src/hmi_server && uv run pytest tests/test_ws.py -x -q` | extend | ⬜ pending |
| 28-01-02 | 01 | 1 | PROD-05 | unit | `cd src/hmi_server/frontend && bun run test --run` | extend | ⬜ pending |
| 28-01-03 | 01 | 1 | PROD-05 | integration | `cd src/hmi_server && uv run pytest tests/test_schedule.py -x -q` | ❌ W0 | ⬜ pending |
| 28-02-01 | 02 | 1 | PROD-06 | file validation | `grep -r "ProtectSystem=strict" deploy/systemd/` | N/A | ⬜ pending |
| 28-02-02 | 02 | 1 | PROD-06 | file validation | `grep -r "MemoryMax" deploy/systemd/` | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `src/hmi_server/tests/test_schedule.py` — covers schedule save endpoint (PROD-05)
- [ ] `src/hmi_server/frontend/src/__tests__/CloudStatusIndicator.test.tsx` — covers cloud status component (PROD-05)
- [ ] conftest.py `test_config` — remove `websocket_port` field to match schema v2.0
- [ ] `src/hmi_server/tests/test_ws.py` — extend to include cloud and ota topics

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| systemd-analyze security scores | PROD-06 | Requires running systemd | Run `systemd-analyze security ems-*.service` on target |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
