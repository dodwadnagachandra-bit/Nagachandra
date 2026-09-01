---
phase: 27
slug: yocto-migration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-16
---

# Phase 27 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (workspace root `[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest tests/test_ota_manager.py tests/test_cert_provisioning.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_ota_manager.py tests/test_cert_provisioning.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 27-01-01 | 01 | 1 | PROD-01 | smoke | `bitbake-layers show-recipes 2>&1 \| grep ems` | ❌ W0 | ⬜ pending |
| 27-01-02 | 01 | 1 | PROD-01 | unit | `uv run pytest tests/test_yocto_recipes.py::test_cmake_sysroot_configured -x` | ❌ W0 | ⬜ pending |
| 27-01-03 | 01 | 1 | PROD-02 | unit | `uv run pytest tests/test_yocto_recipes.py::test_fstab_tmpfs_entries -x` | ❌ W0 | ⬜ pending |
| 27-02-01 | 02 | 2 | PROD-03 | unit | `uv run pytest tests/test_ota_manager.py::test_uboot_backend_read_active_slot -x` | ❌ W0 | ⬜ pending |
| 27-02-02 | 02 | 2 | PROD-03 | unit | `uv run pytest tests/test_ota_manager.py::test_uboot_backend_set_active_slot -x` | ❌ W0 | ⬜ pending |
| 27-02-03 | 02 | 2 | PROD-03 | unit | `uv run pytest tests/test_ota_manager.py::test_uboot_rollback_on_high_boot_count -x` | ❌ W0 | ⬜ pending |
| 27-03-01 | 03 | 2 | PROD-04 | unit | `uv run pytest tests/test_cert_provisioning.py::test_gen_device_cert_outputs -x` | ❌ W0 | ⬜ pending |
| 27-03-02 | 03 | 2 | PROD-04 | unit | `uv run pytest tests/test_cert_provisioning.py::test_device_key_permissions -x` | ❌ W0 | ⬜ pending |
| 27-03-03 | 03 | 2 | PROD-04 | unit | `uv run pytest tests/test_cert_provisioning.py::test_device_cert_cn -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_yocto_recipes.py` — covers PROD-01 (recipe syntax), PROD-02 (fstab content)
- [ ] `tests/test_cert_provisioning.py` — covers PROD-04 (gen-device-cert.sh output validation)
- [ ] New test cases in `tests/test_ota_manager.py` — covers PROD-03 (UBootPartitionBackend with mocked subprocess)
- [ ] `yocto/meta-ems/` directory structure — required before any bitbake validation

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Full bitbake build completes | PROD-01 | Requires Yocto build host (50GB+ disk) | Run `bitbake ems-image` on build host, verify exit 0 |
| A/B partition swap on real hardware | PROD-03 | Requires ECU-1170 with U-Boot | Flash both slots, verify `fw_printenv boot_slot` toggles |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
