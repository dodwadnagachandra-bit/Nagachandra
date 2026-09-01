---
phase: 27-yocto-migration
plan: 01
subsystem: infra
tags: [yocto, meta-ems, bitbake, layer-conf, machine-conf, cmake, externalsrc, python-venv, systemd, libubootenv]

# Dependency graph
requires:
  - phase: 26-diagnostics
    provides: "completed diagnostics module — Phase 26 all plans done"
provides:
  - "yocto/meta-ems/conf/layer.conf: Scarthgap-compatible layer registration with LAYERDEPENDS"
  - "yocto/meta-ems/conf/machine/am65xx-ems.conf: custom MACHINE config extending am65xx-evm"
  - "5 C binary recipes with cmake+externalsrc pattern (safety-manager, comm-manager-c, data-manager-c, control-manager-c, ems-common-c)"
  - "ems-python-venv_0.1.0.bb: wheel-staging recipe with CONTEXT.md deviation documented"
  - "ems-config, ems-systemd-units, ems-frontend supporting recipes"
  - "libubootenv bbappend with fw_env.config for ECU-1170 eMMC layout"
  - "yocto/scripts/setup-yocto.sh: Yocto environment initialization script"
affects: [27-02, 27-03, 27-04, 29-hardware-validation]

# Tech tracking
tech-stack:
  added: [Yocto Scarthgap 5.0 LTS, BitBake layer.conf, cmake bbclass with EXTERNALSRC, libubootenv fw_env.config, meta-ems custom layer]
  patterns:
    - "Layer registration: BBFILES glob for recipes-*/*/*.bb and *.bbappend, BBFILE_PRIORITY=10"
    - "C binary recipes: inherit cmake externalsrc, EXTERNALSRC = ${TOPDIR}/../../src/<module>"
    - "Python wheel staging: FILES class (allarch), pre-built wheels staged to /opt/ems/python/wheels/"
    - "libubootenv bbappend: FILESEXTRAPATHS + do_install:append deploys fw_env.config to /etc/"
    - "Machine conf: require am65xx-evm.conf then override SERIAL_CONSOLES, UBOOT_MACHINE, MACHINE_FEATURES"

key-files:
  created:
    - yocto/meta-ems/conf/layer.conf
    - yocto/meta-ems/conf/machine/am65xx-ems.conf
    - yocto/meta-ems/recipes-ems/ems-common-c/ems-common-c_0.1.0.bb
    - yocto/meta-ems/recipes-ems/safety-manager/safety-manager_0.1.0.bb
    - yocto/meta-ems/recipes-ems/comm-manager-c/comm-manager-c_0.1.0.bb
    - yocto/meta-ems/recipes-ems/data-manager-c/data-manager-c_0.1.0.bb
    - yocto/meta-ems/recipes-ems/control-manager-c/control-manager-c_0.1.0.bb
    - yocto/meta-ems/recipes-ems/ems-python-venv/ems-python-venv_0.1.0.bb
    - yocto/meta-ems/recipes-ems/ems-config/ems-config_0.1.0.bb
    - yocto/meta-ems/recipes-ems/ems-systemd-units/ems-systemd-units_0.1.0.bb
    - yocto/meta-ems/recipes-ems/ems-frontend/ems-frontend_0.1.0.bb
    - yocto/meta-ems/recipes-support/libubootenv/libubootenv_%.bbappend
    - yocto/meta-ems/recipes-support/libubootenv/files/fw_env.config
    - yocto/scripts/setup-yocto.sh
  modified: []

key-decisions:
  - "Python venv recipe uses FILES class (wheel staging) not python3-setuptools: uv_build backend is incompatible with python_setuptools_build_meta in Yocto Scarthgap — wheels are pre-built offline and staged to /opt/ems/python/wheels/"
  - "EXTERNALSRC paths use ${TOPDIR}/../../src/<module> pattern: build/ sits under yocto/, so ../../ traverses to monorepo root"
  - "safety_manager EXTERNALSRC points at src/safety_manager/ (CMakeLists.txt at root, not in c/ subdirectory)"
  - "cmake bbclass must NOT receive CMAKE_TOOLCHAIN_FILE in EXTRA_OECMAKE: bbclass auto-generates toolchain.cmake with correct Yocto sysroot"
  - "am65xx-ems.conf defers DTB and U-Boot defconfig validation to Phase 29: Advantech ECU-1170-552A BSP not publicly available for Scarthgap"
  - "fw_env.config offset 0x3E0000 is TI AM65xx-evm default: must be verified against actual ECU-1170 U-Boot CONFIG_ENV_OFFSET in Phase 29"

requirements-completed: [PROD-01]

# Metrics
duration: 5m45s
completed: 2026-03-16
---

# Phase 27 Plan 01: meta-ems Layer Structure and Module Recipes Summary

**meta-ems Yocto layer with layer.conf (Scarthgap), machine config extending am65xx-evm, 5 C cmake+externalsrc recipes, Python wheel-staging recipe, config/systemd/frontend FILES recipes, libubootenv fw_env.config bbappend, and setup-yocto.sh initialization script**

## Performance

- **Duration:** ~5m45s
- **Started:** 2026-03-15T22:15:26Z
- **Completed:** 2026-03-15T22:21:11Z
- **Tasks:** 1/1
- **Files created:** 14

## Accomplishments

- `yocto/meta-ems/conf/layer.conf` declares `LAYERSERIES_COMPAT_meta-ems = "scarthgap"`, `LAYERDEPENDS_meta-ems = "core meta-ti-bsp meta-oe meta-python"`, `BBFILE_PRIORITY = 10`
- `yocto/meta-ems/conf/machine/am65xx-ems.conf` extends `am65xx-evm` from meta-ti-bsp, overrides `SERIAL_CONSOLES = "115200;ttyS2"`, sets `UBOOT_MACHINE = "am65x_evm_a53_defconfig"`, appends `ext4 vfat efi` to `MACHINE_FEATURES`
- 5 C binary recipes (`ems-common-c`, `safety-manager`, `comm-manager-c`, `data-manager-c`, `control-manager-c`) all use `inherit cmake externalsrc`, with `EXTERNALSRC = "${TOPDIR}/../../src/<module>"` pointing at monorepo; `EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"` only — cmake bbclass handles toolchain
- `ems-python-venv_0.1.0.bb` uses `inherit allarch` (FILES/wheel-staging approach) with documented CONTEXT.md deviation: `uv_build` backend is not supported by `python_setuptools_build_meta` in Scarthgap
- `ems-config_0.1.0.bb` stages 14 YAML configs + JSON schemas + profiles to `/etc/ems/config/`
- `ems-systemd-units_0.1.0.bb` uses `inherit systemd`, lists all 14 service files + ems.target, `SYSTEMD_AUTO_ENABLE = "enable"`
- `ems-frontend_0.1.0.bb` stages pre-built React dist/ to `/opt/ems/frontend/dist/` — no bun/Node.js in image
- `libubootenv_%.bbappend` installs `fw_env.config` to `/etc/fw_env.config` with ECU-1170 eMMC offset 0x3E0000 (pending Phase 29 hardware verification)
- `yocto/scripts/setup-yocto.sh` clones poky + meta-ti + meta-openembedded (Scarthgap), sources oe-init-build-env, adds layers via `bitbake-layers add-layer`, sets `MACHINE = "am65xx-ems"` in local.conf

## Task Commits

All plan 27-01 files were committed across multiple sessions (executed out of order with later plans):

| File Group | Commit | Plan Session |
|---|---|---|
| layer.conf, am65xx-ems.conf, ems-config, ems-python-venv, libubootenv, setup-yocto.sh | `68e7c15` | 27-04 docs |
| C binary recipes (5), ems-systemd-units, ems-frontend | `0d6253b` | 27-02 feat |

## Files Created

- `/home/overlord/EMS/yocto/meta-ems/conf/layer.conf` — Layer registration for Scarthgap
- `/home/overlord/EMS/yocto/meta-ems/conf/machine/am65xx-ems.conf` — Custom MACHINE extending am65xx-evm
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/ems-common-c/ems-common-c_0.1.0.bb` — C shared library recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/safety-manager/safety-manager_0.1.0.bb` — Safety manager cmake recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/comm-manager-c/comm-manager-c_0.1.0.bb` — Comm manager cmake recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/data-manager-c/data-manager-c_0.1.0.bb` — Data manager cmake recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/control-manager-c/control-manager-c_0.1.0.bb` — Control manager cmake recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/ems-python-venv/ems-python-venv_0.1.0.bb` — Python wheel staging recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/ems-config/ems-config_0.1.0.bb` — Config files recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/ems-systemd-units/ems-systemd-units_0.1.0.bb` — Systemd units recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-ems/ems-frontend/ems-frontend_0.1.0.bb` — Frontend static assets recipe
- `/home/overlord/EMS/yocto/meta-ems/recipes-support/libubootenv/libubootenv_%.bbappend` — fw_env.config bbappend
- `/home/overlord/EMS/yocto/meta-ems/recipes-support/libubootenv/files/fw_env.config` — U-Boot env eMMC location config
- `/home/overlord/EMS/yocto/scripts/setup-yocto.sh` — Yocto environment initialization script (executable)

## Decisions Made

- **Python wheel staging over python3-setuptools:** `uv_build` backend (used by all EMS Python packages) is incompatible with `python_setuptools_build_meta` in Scarthgap. Pre-built wheels + `pip --no-index --find-links` is the correct pattern. Documented inline in recipe.
- **cmake bbclass handles toolchain:** Do NOT pass `CMAKE_TOOLCHAIN_FILE` in `EXTRA_OECMAKE`. The cmake bbclass generates a `toolchain.cmake` with the correct sysroot, target compiler, and library search paths for the Yocto cross-compilation environment.
- **safety_manager source at module root:** `src/safety_manager/` has CMakeLists.txt at root (not in a `c/` subdirectory). Other C modules follow `src/<module>/c/` pattern.
- **Phase 29 hardware deferred:** am65xx-ems.conf DTB (`k3-am654-base-board.dtb`) and UBOOT_MACHINE are TI EVM starting points. Advantech ECU-1170-552A BSP is not publicly available for Scarthgap. Phase 29 validates on hardware.
- **fw_env.config offset 0x3E0000:** TI AM65xx-evm default. Must be verified against actual ECU-1170 `CONFIG_ENV_OFFSET` before OTA A/B switching works. Phase 29 validation required.

## Deviations from Plan

### Execution Order

**Context:** Plan 27-01 was executed after plans 27-02, 27-03, and 27-04, which had already been run in a prior session. When 27-02 ran first, it created the C binary recipes as a Rule 2 deviation (missing critical prerequisites). The remaining files were created by 27-04.

All files specified in 27-01-PLAN.md exist in the repository with correct content. This SUMMARY retrospectively documents plan 27-01 as complete.

- **Files specified in plan:** All 14 present and matching specification
- **Additional files created by other plans:** `ems-boot-script_0.1.0.bb`, `boot.cmd`, `ems-certs_0.1.0.bb`, `core-image-ems.bb` (plans 27-02, 27-03, 27-04 scope)
- **Commits:** `68e7c15` (layer.conf, machine conf, config, python-venv, libubootenv, setup script), `0d6253b` (C binary recipes, systemd units, frontend)

## Self-Check: PASSED

Checked all 14 plan-specified files exist:

- `yocto/meta-ems/conf/layer.conf` — FOUND (68e7c15)
- `yocto/meta-ems/conf/machine/am65xx-ems.conf` — FOUND (68e7c15)
- `yocto/meta-ems/recipes-ems/ems-common-c/ems-common-c_0.1.0.bb` — FOUND (0d6253b)
- `yocto/meta-ems/recipes-ems/safety-manager/safety-manager_0.1.0.bb` — FOUND (0d6253b)
- `yocto/meta-ems/recipes-ems/comm-manager-c/comm-manager-c_0.1.0.bb` — FOUND (0d6253b)
- `yocto/meta-ems/recipes-ems/data-manager-c/data-manager-c_0.1.0.bb` — FOUND (0d6253b)
- `yocto/meta-ems/recipes-ems/control-manager-c/control-manager-c_0.1.0.bb` — FOUND (0d6253b)
- `yocto/meta-ems/recipes-ems/ems-python-venv/ems-python-venv_0.1.0.bb` — FOUND (68e7c15)
- `yocto/meta-ems/recipes-ems/ems-config/ems-config_0.1.0.bb` — FOUND (68e7c15)
- `yocto/meta-ems/recipes-ems/ems-systemd-units/ems-systemd-units_0.1.0.bb` — FOUND (0d6253b)
- `yocto/meta-ems/recipes-ems/ems-frontend/ems-frontend_0.1.0.bb` — FOUND (0d6253b)
- `yocto/meta-ems/recipes-support/libubootenv/libubootenv_%.bbappend` — FOUND (68e7c15)
- `yocto/scripts/setup-yocto.sh` — FOUND (68e7c15, executable)
- layer.conf contains `LAYERSERIES_COMPAT_meta-ems = "scarthgap"` — VERIFIED
- C recipes inherit `cmake externalsrc` — VERIFIED (all 5)
- Python venv recipe documents CONTEXT.md deviation — VERIFIED
- Machine config extends am65xx-evm — VERIFIED

---

*Phase: 27-yocto-migration*
*Completed: 2026-03-16*
