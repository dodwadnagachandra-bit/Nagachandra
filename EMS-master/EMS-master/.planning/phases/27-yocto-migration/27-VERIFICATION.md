---
phase: 27-yocto-migration
verified: 2026-03-16T05:00:00Z
status: passed
score: 13/13 must-haves verified
re_verification: false
---

# Phase 27: Yocto Migration Verification Report

**Phase Goal:** All EMS modules build and deploy on Yocto Linux with read-only rootfs and real A/B partition management
**Verified:** 2026-03-16T05:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | meta-ems layer has valid layer.conf with Scarthgap compat and correct LAYERDEPENDS | VERIFIED | `LAYERSERIES_COMPAT_meta-ems = "scarthgap"`, `LAYERDEPENDS_meta-ems = "core meta-ti-bsp meta-oe meta-python"` present in layer.conf |
| 2 | All 5 C binaries have cmake-based recipes with EXTERNALSRC pointing at monorepo | VERIFIED | All 5 recipes (`ems-common-c`, `safety-manager`, `comm-manager-c`, `data-manager-c`, `control-manager-c`) contain `inherit cmake externalsrc` with `EXTERNALSRC = "${TOPDIR}/../../src/<module>"` |
| 3 | Python venv recipe stages pre-built wheels with documented CONTEXT.md deviation | VERIFIED | `ems-python-venv_0.1.0.bb` uses `inherit allarch`, installs to `/opt/ems/python/wheels/`, deviation comment explains uv_build incompatibility with Scarthgap |
| 4 | Image recipe enables read-only-rootfs with tmpfs overlays | VERIFIED | `core-image-ems.bb` has `IMAGE_FEATURES += "read-only-rootfs ssh-server-openssh"`; base-files bbappend provides 4 tmpfs entries + `/data` SSD mount |
| 5 | SSD data partition mounted read-write at /data | VERIFIED | `/dev/nvme0n1p1 /data ext4 defaults,noatime 0 2` in base-files bbappend |
| 6 | IMAGE_INSTALL includes all EMS packages including ems-boot-script and ems-certs | VERIFIED | `core-image-ems.bb` lines 47-66: all 12 EMS packages including `ems-boot-script` and `ems-certs` |
| 7 | IMAGE_BOOT_FILES includes boot.scr for U-Boot A/B bootscript | VERIFIED | `IMAGE_BOOT_FILES:append = " boot.scr"` at line 74 |
| 8 | ROOTFS_POSTPROCESS_COMMAND creates Python venv from staged wheels | VERIFIED | `create_ems_venv()` function defined and registered; uses `--no-index --find-links` with staged wheels |
| 9 | UBootPartitionBackend reads/writes active slot via fw_printenv/fw_setenv subprocess | VERIFIED | `_fw_getenv()` calls `subprocess.run(["fw_printenv", "-n", key])`, `_fw_setenv()` calls `subprocess.run(["fw_setenv", key, value])`; RuntimeError on non-zero exit |
| 10 | Boot count > 2 triggers automatic slot reversion in U-Boot bootscript | VERIFIED | `boot.cmd` lines 61-76: `if test ${ems_boot_count} -gt 2` reverts slot, resets count, calls `saveenv` |
| 11 | Existing PartitionBackend is preserved for dev/test use | VERIFIED | `PartitionBackend(BasePartitionBackend)` class unchanged; `BasePartitionBackend` extracted as shared base |
| 12 | gen-device-cert.sh produces ca.crt, device.crt, device.key with correct permissions | VERIFIED | Script: `openssl genrsa`, `openssl req`, `openssl x509`; `chmod 600 device.key`; `cp ca.crt`; `rm device.csr`; `set -euo pipefail` |
| 13 | Yocto ems-certs recipe installs certificates to /etc/ems/certs/ | VERIFIED | `ems-certs_0.1.0.bb` `do_install()` uses `install -d ${D}/etc/ems/certs`, installs ca.crt/device.crt (0644) and device.key (0600) |

**Score:** 13/13 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `yocto/meta-ems/conf/layer.conf` | Layer registration | VERIFIED | 21 lines; contains `LAYERSERIES_COMPAT_meta-ems`, `LAYERDEPENDS_meta-ems`, `BBFILE_PRIORITY_meta-ems = "10"` |
| `yocto/meta-ems/conf/machine/am65xx-ems.conf` | Custom MACHINE config | VERIFIED | Extends `am65xx-evm.conf`, overrides `SERIAL_CONSOLES`, `UBOOT_MACHINE`, appends `MACHINE_FEATURES` |
| `yocto/meta-ems/recipes-ems/safety-manager/safety-manager_0.1.0.bb` | Safety manager C recipe | VERIFIED | `inherit cmake externalsrc`, `EXTERNALSRC = "${TOPDIR}/../../src/safety_manager"`, installs to `/opt/ems/bin/` |
| `yocto/meta-ems/recipes-ems/ems-python-venv/ems-python-venv_0.1.0.bb` | Python wheel staging recipe | VERIFIED | Contains `/opt/ems/python/wheels`, CONTEXT.md deviation documented |
| `yocto/meta-ems/recipes-ems/ems-common-c/ems-common-c_0.1.0.bb` | C shared library recipe | VERIFIED | `inherit cmake externalsrc` |
| `yocto/meta-ems/recipes-ems/comm-manager-c/comm-manager-c_0.1.0.bb` | Comm manager C recipe | VERIFIED | `inherit cmake externalsrc` |
| `yocto/meta-ems/recipes-ems/data-manager-c/data-manager-c_0.1.0.bb` | Data manager C recipe | VERIFIED | `inherit cmake externalsrc` |
| `yocto/meta-ems/recipes-ems/control-manager-c/control-manager-c_0.1.0.bb` | Control manager C recipe | VERIFIED | `inherit cmake externalsrc` |
| `yocto/meta-ems/recipes-ems/ems-config/ems-config_0.1.0.bb` | Config files recipe | VERIFIED | Installs to `/etc/ems/config/` |
| `yocto/meta-ems/recipes-ems/ems-systemd-units/ems-systemd-units_0.1.0.bb` | Systemd units recipe | VERIFIED | `inherit systemd`, lists 14 service files + ems.target |
| `yocto/meta-ems/recipes-ems/ems-frontend/ems-frontend_0.1.0.bb` | Frontend static assets recipe | VERIFIED | Installs to `/opt/ems/frontend/dist/` |
| `yocto/meta-ems/recipes-support/libubootenv/libubootenv_%.bbappend` | fw_env.config bbappend | VERIFIED | Installs `/etc/fw_env.config` with ECU-1170 eMMC offset `0x3E0000` |
| `yocto/scripts/setup-yocto.sh` | Yocto env init script | VERIFIED | Executable (`-rwxrwxr-x`); clones poky/meta-ti/meta-oe (scarthgap), adds layers, sets `MACHINE = "am65xx-ems"` |
| `yocto/meta-ems/recipes-core/images/core-image-ems.bb` | EMS production image recipe | VERIFIED | 116 lines; `read-only-rootfs`, all packages, `create_ems_venv` ROOTFS_POSTPROCESS_COMMAND |
| `yocto/meta-ems/recipes-core/base-files/base-files_%.bbappend` | fstab with tmpfs and SSD | VERIFIED | 37 lines; contains `tmpfs /run/ems`, `tmpfs /var/log/journal`, `/dev/nvme0n1p1 /data ext4` |
| `deploy/tmpfiles/ems.conf` | systemd-tmpfiles runtime dirs | VERIFIED | `d /run/ems`, `d /data/config`, `d /data/parquet`, `d /data/jsonl`, `d /data/cloud_buffer`, `d /data/rtdb_snapshots` |
| `tests/test_yocto_recipes.py` | Recipe file validation tests | VERIFIED | 265 lines (min 50); 20 test functions; all 34 tests pass |
| `yocto/meta-ems/recipes-ems/ems-boot-script/boot.cmd` | U-Boot A/B boot script | VERIFIED | 121 lines; contains `ems_active_slot`, `ems_boot_count`, `saveenv`, `booti` (24 matches vs 8 required) |
| `yocto/meta-ems/recipes-ems/ems-boot-script/ems-boot-script_0.1.0.bb` | Boot script Yocto recipe | VERIFIED | `DEPENDS = "u-boot-mkimage-native"`, `mkimage -C none -A arm64 -T script`, `do_deploy` to `${DEPLOYDIR}` |
| `src/ota_manager/src/ems_ota_manager/partition.py` | UBootPartitionBackend + PartitionBackend | VERIFIED | 377 lines; exports `BootFlag`, `BasePartitionBackend`, `PartitionBackend`, `UBootPartitionBackend` |
| `tests/test_ota_manager.py` | OTA manager unit tests | VERIFIED | 1759 lines (min 1400); `TestUBootPartitionBackend` with 14 test methods; 56 total tests pass |
| `tools/gen-device-cert.sh` | Per-device mTLS cert generator | VERIFIED | 146 lines (min 30); executable; `openssl genrsa`, `openssl req`, `openssl x509`; `chmod 600 device.key`; `rm device.csr` |
| `tools/gen-ca.sh` | CA generator helper | VERIFIED | 42 lines; executable; `openssl req -x509` self-signed CA |
| `yocto/meta-ems/recipes-ems/ems-certs/ems-certs_0.1.0.bb` | Yocto cert install recipe | VERIFIED | Contains `/etc/ems/certs`; `install -m 0600 device.key`; CA key excluded by design |
| `tests/test_cert_provisioning.py` | Cert provisioning tests | VERIFIED | 360 lines (min 60); 10 tests; all pass in 10s |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `layer.conf` | All recipes-ems packages | BBFILES glob | WIRED | `BBFILES += "${LAYERDIR}/recipes-*/*/*.bb \n ${LAYERDIR}/recipes-*/*/*.bbappend"` |
| `core-image-ems.bb` | All recipes-ems packages | `IMAGE_INSTALL:append` | WIRED | `safety-manager` verified present in IMAGE_INSTALL list |
| `core-image-ems.bb` | ems-boot-script recipe | `IMAGE_INSTALL:append` | WIRED | `ems-boot-script` at line 64 |
| `core-image-ems.bb` | ems-certs recipe | `IMAGE_INSTALL:append` | WIRED | `ems-certs` at line 65 |
| `core-image-ems.bb` | boot.scr on boot partition | `IMAGE_BOOT_FILES:append` | WIRED | `IMAGE_BOOT_FILES:append = " boot.scr"` at line 74 |
| `base-files_%.bbappend` | `/etc/fstab` | `do_install:append` heredoc | WIRED | `tmpfs /run/ems` entry verified present |
| `partition.py` UBootPartitionBackend | `fw_printenv`/`fw_setenv` | `subprocess.run` | WIRED | `_fw_getenv` calls `["fw_printenv", "-n", key]`; `_fw_setenv` calls `["fw_setenv", key, value]` |
| `boot.cmd` | U-Boot env vars | `ems_active_slot`, `ems_boot_count` | WIRED | Both vars read, incremented, saved via `saveenv` |
| `gen-device-cert.sh` | openssl | `genrsa`, `req`, `x509` commands | WIRED | All three openssl invocations present; CA key path never written to output dir |
| `ems-certs_0.1.0.bb` | `/etc/ems/certs/` | `do_install` file copy | WIRED | `install -d ${D}/etc/ems/certs`; three `install` commands for ca.crt, device.crt, device.key |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| PROD-01 | 27-01, 27-02 | Yocto recipe creation for all 12 Python packages and 5 C executables, with pinned dependency versions | SATISFIED | 15 recipe files exist: 5 C cmake+externalsrc recipes, ems-python-venv (wheel staging), ems-config, ems-systemd-units, ems-frontend, ems-boot-script, ems-certs, plus image/bbappend files |
| PROD-02 | 27-02 | Read-only rootfs configuration with tmpfs overlays for /tmp, /var/run, /run/ems — data partition on SSD remains read-write | SATISFIED | `IMAGE_FEATURES += "read-only-rootfs"`; base-files bbappend provides 4 tmpfs entries; `/dev/nvme0n1p1 /data ext4 noatime` verified |
| PROD-03 | 27-03 | Real A/B partition integration replaces mock backend with U-Boot env variable partition swap | SATISFIED | `UBootPartitionBackend` class uses `fw_printenv`/`fw_setenv` via `subprocess.run`; `boot.cmd` implements A/B selection + rollback on `ems_boot_count > 2`; 56 tests pass |
| PROD-04 | 27-04 | mTLS certificate provisioning workflow — device cert generation, CA signing, secure key storage | SATISFIED | `gen-device-cert.sh` produces valid RSA-4096 certs with `chmod 600 device.key`, CA key excluded from output; `ems-certs_0.1.0.bb` installs to `/etc/ems/certs/`; 10 cert tests pass |

All 4 phase requirements satisfied. No orphaned requirements found (PROD-05, PROD-06, PROD-07 are mapped to Phases 28 and 29 per REQUIREMENTS.md).

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `yocto/meta-ems/conf/machine/am65xx-ems.conf` | 18, 28 | `TODO (Phase 29)` — U-Boot defconfig and ECU-1170 DTB unconfirmed | Info | Intentional deferral to Phase 29 hardware bring-up; EVM defaults are valid starting point; explicitly documented |
| `yocto/meta-ems/recipes-support/libubootenv/files/fw_env.config` | 15 | `TODO (Phase 29)` — `CONFIG_ENV_OFFSET` not confirmed for ECU-1170 | Info | Intentional deferral; offset `0x3E0000` is TI EVM default; must verify against Advantech BSP in Phase 29 |
| `yocto/meta-ems/recipes-support/libubootenv/libubootenv_%.bbappend` | 10 | `TODO (Phase 29)` — same offset caveat | Info | Same as above; acceptable until hardware validation |

No blockers. All TODOs are tagged `Phase 29` and represent intentional hardware-dependent deferrals that were prescribed by the PLAN and CONTEXT.md. They do not block the Phase 27 goal.

---

## Human Verification Required

### 1. Bitbake Build on Real Yocto Environment

**Test:** Run `bitbake core-image-ems` after populating `yocto/wheels/` with pre-built EMS wheels and providing device certificates via `FILESEXTRAPATHS:prepend:pn-ems-certs`.
**Expected:** Image builds without errors; ext4 rootfs mounts read-only on hardware; `/run/ems`, `/data/*` directories created at boot.
**Why human:** Full Yocto build requires Scarthgap BitBake environment, TI meta-ti-bsp layer, and target hardware not available in automated verification.

### 2. U-Boot A/B Slot Selection on Hardware

**Test:** Boot the ECU-1170-552A from slot A. Manually increment `ems_boot_count` to 3 via `fw_setenv`. Reboot.
**Expected:** U-Boot bootscript detects `ems_boot_count > 2`, reverts to slot B, resets count to 0, boots from `mmcblk0p3`.
**Why human:** Requires physical hardware with eMMC and U-Boot environment; cannot run bootscript in CI.

### 3. Python Venv Creation from Staged Wheels

**Test:** During bitbake, verify `create_ems_venv` postprocess function successfully installs all 12 EMS Python modules into `/opt/ems/python/.venv` from staged wheels.
**Expected:** Venv is created; `import data_manager`, `import safety_manager` etc. succeed from within the venv; pip/setuptools absent from venv.
**Why human:** Requires a complete Yocto image build with real wheel artifacts staged to `yocto/wheels/`.

### 4. mTLS Cert Bake-in and Cloud Connection

**Test:** Run `gen-device-cert.sh`, set `FILESEXTRAPATHS:prepend:pn-ems-certs`, build image, boot device, start `cloud_manager`.
**Expected:** `cloud_manager` connects to MQTT broker using `/etc/ems/certs/device.crt` and `/etc/ems/certs/device.key`; TLS handshake succeeds.
**Why human:** Requires real MQTT broker with CA-signed server cert and a device image build.

---

## Gaps Summary

No gaps. All 13 observable truths are fully verified. All 25 artifacts exist, are substantive, and are correctly wired. All 4 requirements (PROD-01 through PROD-04) are satisfied by direct code evidence and passing test suites.

The 4 Phase-29-tagged TODOs in machine configuration and fw_env.config are intentional, documented deferrals prescribed by the planning context — they do not block phase 27 goal achievement.

### Test Suite Results (Confirming Automated Verification)

| Test File | Tests | Result |
|-----------|-------|--------|
| `tests/test_yocto_recipes.py` | 34 | All pass |
| `tests/test_ota_manager.py` | 56 | All pass |
| `tests/test_cert_provisioning.py` | 10 | All pass |
| **Total** | **100** | **All pass** |

---

_Verified: 2026-03-16T05:00:00Z_
_Verifier: Claude (gsd-verifier)_
