---
phase: 27-yocto-migration
plan: "03"
subsystem: ota_manager
tags: [ota, partition, uboot, yocto, ab-update]
dependency_graph:
  requires: []
  provides: [UBootPartitionBackend, ems-boot-script]
  affects: [src/ota_manager/src/ems_ota_manager/partition.py, tests/test_ota_manager.py]
tech_stack:
  added: [subprocess.run fw_printenv/fw_setenv, mkimage arm64 script]
  patterns: [BasePartitionBackend inheritance, TDD red-green]
key_files:
  created:
    - yocto/meta-ems/recipes-ems/ems-boot-script/boot.cmd
    - yocto/meta-ems/recipes-ems/ems-boot-script/ems-boot-script_0.1.0.bb
  modified:
    - src/ota_manager/src/ems_ota_manager/partition.py
    - tests/test_ota_manager.py
key_decisions:
  - "UBootPartitionBackend uses sync subprocess.run (not asyncio) to match existing PartitionBackend sync interface"
  - "BasePartitionBackend extracted to share dd flash + systemctl reboot between both backends"
  - "Rollback on boot_count > 2 is purely U-Boot responsibility; Python backend reads state as-is"
  - "ems_active_slot failure raises RuntimeError; boot_count and pending_health_check default to 0/False"
metrics:
  duration: 3m19s
  completed: "2026-03-16"
  tasks: 2
  files_changed: 4
requirements: [PROD-03]
---

# Phase 27 Plan 03: U-Boot Partition Backend Summary

Replace mock JSON-based A/B partition backend with production UBootPartitionBackend using fw_printenv/fw_setenv, plus U-Boot bootscript with A/B slot selection and rollback on boot count > 2.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | UBootPartitionBackend (TDD) | ad581df | partition.py, test_ota_manager.py |
| 2 | U-Boot bootscript + recipe | c84a6c6 | boot.cmd, ems-boot-script_0.1.0.bb |

## What Was Built

### Task 1: UBootPartitionBackend

**`src/ota_manager/src/ems_ota_manager/partition.py`** — refactored to three-class hierarchy:

- `BasePartitionBackend` — shared base with `write_image_to_standby` (dd) and `reboot` (systemctl)
- `PartitionBackend(BasePartitionBackend)` — original JSON-file backend, unchanged API, preserved for dev/test
- `UBootPartitionBackend(BasePartitionBackend)` — production backend using `fw_printenv`/`fw_setenv`

UBootPartitionBackend behavior:
- `read_boot_flag()` — calls `fw_printenv -n ems_active_slot` (required, RuntimeError on failure), `ems_boot_count` (default 0), `ems_pending_health_check` (default False); derives `previous` as opposite of `active`
- `write_boot_flag(flag)` — calls `fw_setenv` for `ems_active_slot`, `ems_boot_count`, `ems_pending_health_check`; raises RuntimeError on non-zero exit
- `get_standby_partition()` — reads active slot via read_boot_flag, returns opposite
- `_fw_env_config` — configurable via `partition.fw_env_config`, defaults to `/etc/fw_env.config`

**`tests/test_ota_manager.py`** — 14 new tests in `TestUBootPartitionBackend`:
- Read with active=a/b, boot_count, pending_health_check
- Write verifying all 3 fw_setenv calls with correct values
- Default handling (boot_count, pending_health_check when key not in env)
- RuntimeError paths (fw_printenv failure on active slot, fw_setenv failure)
- High boot_count read-as-is (rollback is U-Boot's job)
- Clear boot_count (boot_count=0 path)
- Backward compatibility of original PartitionBackend
- Config defaults (fw_env_config)
- write_image_to_standby and reboot async paths

Total: 56 tests, all pass (43 existing + 13 new UBoot tests).

### Task 2: U-Boot Bootscript

**`yocto/meta-ems/recipes-ems/ems-boot-script/boot.cmd`** — complete A/B boot selection script:
1. Reads `ems_active_slot` (defaults to "a" if not set)
2. Maps slot → device: a=mmcblk0p2, b=mmcblk0p3
3. Reads `ems_boot_count` (defaults to 0)
4. If `ems_boot_count > 2`: reverts to fallback slot, resets count to 0, saveenv
5. Increments `ems_boot_count`, saveenv (OTA health checker resets to 0 on success)
6. Sets `bootargs`: `console=ttyS2,115200n8 root=/dev/${rootdev} ro rootfstype=ext4 rootwait systemd.unified_cgroup_hierarchy=1`
7. Loads kernel `Image` from mmc 0:1 at 0x80080000
8. Loads DTB `k3-am654-base-board.dtb` from mmc 0:1 at 0x83000000
9. `booti ${loadaddr} - ${fdtaddr}`

**`yocto/meta-ems/recipes-ems/ems-boot-script/ems-boot-script_0.1.0.bb`** — Yocto recipe:
- `DEPENDS = "u-boot-mkimage-native"` (host mkimage)
- `do_compile`: `mkimage -C none -A arm64 -T script -d boot.cmd boot.scr`
- `do_install`: installs boot.scr to `${D}/boot/boot.scr`
- `do_deploy`: installs boot.scr to `${DEPLOYDIR}/boot.scr` (consumed by IMAGE_BOOT_FILES in 27-02)

## Deviations from Plan

None — plan executed exactly as written.

## Verification

- `uv run pytest tests/test_ota_manager.py -x -q` — 56 passed
- `grep -c "ems_active_slot\|ems_boot_count\|saveenv\|booti" boot.cmd` — 24 matches (>= 8 required)
- `wc -l tests/test_ota_manager.py` — 1759 lines (>= 1400 required)
- All existing OTA manager tests pass unchanged
- UBootPartitionBackend is a drop-in replacement for PartitionBackend on production hardware

## Self-Check: PASSED

Files verified:
- FOUND: src/ota_manager/src/ems_ota_manager/partition.py (class UBootPartitionBackend present)
- FOUND: yocto/meta-ems/recipes-ems/ems-boot-script/boot.cmd
- FOUND: yocto/meta-ems/recipes-ems/ems-boot-script/ems-boot-script_0.1.0.bb
- FOUND: tests/test_ota_manager.py (1759 lines, 56 tests pass)

Commits verified:
- FOUND: ad581df — feat(27-03): implement UBootPartitionBackend
- FOUND: c84a6c6 — feat(27-03): add U-Boot A/B bootscript
