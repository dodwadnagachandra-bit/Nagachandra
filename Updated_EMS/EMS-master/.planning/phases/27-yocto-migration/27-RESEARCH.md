# Phase 27: Yocto Migration - Research

**Researched:** 2026-03-16
**Domain:** Yocto Project meta-layer, read-only rootfs, U-Boot A/B partitioning, mTLS certificate provisioning
**Confidence:** HIGH (core Yocto patterns from official docs) / MEDIUM (Python venv in image, TI BSP availability)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Yocto Recipe Strategy:**
- One meta-layer (`meta-ems`) with individual recipes per module, lives at `yocto/meta-ems/` in the EMS monorepo.
- C binaries: `cmake` bbclass — deploys to `/opt/ems/bin/`
- Python packages: `python3-setuptools` class (uv builds) — deploys to `/opt/ems/python/.venv/`
- Config files (14 YAML + schemas): FILES recipe — `/etc/ems/config/`
- Systemd units (14 service files): `systemd` class — `/usr/lib/systemd/system/`
- Frontend build (React HMI pre-built dist/): FILES recipe — `/opt/ems/frontend/dist/`
- Python venv created during image build via `ROOTFS_POSTPROCESS_COMMAND` (not at runtime)
- All Python dependency versions pinned in `requirements.lock` (from `uv pip compile`)
- C binaries cross-compiled using Yocto SDK sysroot (uncomment CMAKE_SYSROOT in toolchain file)
- Frontend pre-built on dev machine (`bun run build`), copied as static files — no bun/Node.js in image

**Read-Only Rootfs:**
| Mount Point | Type | Purpose |
|---|---|---|
| `/` (rootfs) | Read-only ext4 on eMMC | OS + EMS binaries + config |
| `/tmp` | tmpfs (50MB) | Temporary files |
| `/var/run` | tmpfs (10MB) | PID files, systemd runtime |
| `/run/ems` | tmpfs (5MB) | ZMQ IPC sockets |
| `/data` | Read-write ext4 on SSD | Parquet, JSONL, cloud buffer, RTDB snapshots |
| `/etc/ems/certs` | Read-only (in rootfs) | mTLS certificates (baked into image) |
| `/var/log/journal` | tmpfs (50MB) | systemd journal (volatile) |
- Config files at `/etc/ems/config/` are read-only; hot-reload modifies `/data/config/` overlay
- RTDB shared memory (`/dev/shm/ems_rtdb`) is tmpfs by default — no change needed
- ZMQ IPC sockets under `/run/ems/` are tmpfs — created at runtime by data_manager
- SSD data partition survives OTA updates (separate from A/B rootfs partitions)
- Journal is volatile (tmpfs) — structured logs go to logger JSONL on SSD

**Real A/B Partition Layout (64GB eMMC):**
| Partition | Device | Size | Filesystem | Purpose |
|---|---|---|---|---|
| boot | mmcblk0p1 | 256MB | FAT32 | U-Boot + kernel + DTB |
| rootfs_a | mmcblk0p2 | 4GB | ext4 (ro) | System A |
| rootfs_b | mmcblk0p3 | 4GB | ext4 (ro) | System B |
| data | SSD (nvme0n1p1) | Remaining | ext4 (rw) | Parquet, JSONL, buffer, snapshots, config overlay |
- U-Boot env variable `ems_active_slot=a|b` determines boot partition
- OTA manager writes to inactive slot, then `fw_setenv ems_active_slot b` to swap
- `ems_boot_count` in U-Boot env incremented by bootscript, cleared by ota_manager health check
- If `ems_boot_count > 2`, bootscript auto-reverts to previous slot (U-Boot level rollback)

**mTLS Certificate Provisioning:**
- Build-time provisioning — baked into Yocto image per-device, not runtime provisioning
- Each device gets unique cert with CN=`{site_id}-{serial_number}`
- CA cert: `/etc/ems/certs/ca.crt`, Device cert: `/etc/ems/certs/device.crt`, Key: `/etc/ems/certs/device.key`
- Private key: chmod 600, owned by ems:ems
- Certificate generation script: `tools/gen-device-cert.sh` using openssl
- Certificate rotation via new OTA image (no online rotation for v1)
- Build server holds CA private key — never deployed to devices
- MQTT broker URL: `/etc/ems/config/cloud_config.yaml`

### Claude's Discretion

- Yocto layer structure (recipes-ems/, recipes-core/, conf/)
- Yocto image recipe (core-image-ems vs extending core-image-minimal)
- Python venv creation in ROOTFS_POSTPROCESS_COMMAND vs package manager
- U-Boot environment partition location and access method (fw_setenv/fw_printenv)
- Hot-reload config overlay mechanism (/data/config/ bind-mounted over /etc/ems/config/)
- Certificate generation script implementation
- Test strategy (QEMU ARM64 for rootfs validation, or defer to Phase 29 hardware)

### Deferred Ideas (OUT OF SCOPE)

- Runtime certificate rotation (EST/ACME) — v2+ when fleet grows
- RAUC/swupdate integration — custom OTA is simpler for v1
- Secure boot (signed kernel/DTB) — v2+
- dm-verity for rootfs integrity — v2+
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROD-01 | Yocto recipe creation for all 12 Python packages and 5 C executables, with pinned dependency versions matching Ubuntu 24.04 development | meta-layer structure, cmake bbclass, python3-setuptools/setuptools_build_meta bbclass, EXTERNALSRC pattern for monorepo, requirements.lock pinning |
| PROD-02 | Read-only rootfs configuration with tmpfs overlays for /tmp, /var/run, and /run/ems — data partition on SSD remains read-write | IMAGE_FEATURES read-only-rootfs, fstab generation, systemd volatilefiles, postinstall script constraints |
| PROD-03 | Real A/B partition integration replaces mock partition backend with actual rootfs partition swap via U-Boot environment variables | libubootenv fw_setenv/fw_printenv, fw_env.config for eMMC, UBootPartitionBackend replacing MockPartitionBackend, U-Boot bootscript logic |
| PROD-04 | mTLS certificate provisioning workflow — device certificate generation, CA signing, secure key storage, certificate rotation plan | openssl commands for CA + per-device cert, gen-device-cert.sh script, Yocto FILES recipe integration, key file permissions |
</phase_requirements>

---

## Summary

Phase 27 converts the EMS from Ubuntu 24.04 development deployment to a production Yocto Linux image on the Advantech ECU-1170-552A (TI AM6548). The core work is creating a `meta-ems` Yocto layer that packages all 12 Python modules and 5 C binaries, configures a read-only rootfs with appropriate tmpfs overlays, replaces the mock A/B partition backend with real U-Boot environment management, and generates per-device mTLS certificates baked into each device image.

The biggest technical challenge is the Python venv approach: Yocto's default Python3 installation is minimal and missing stdlib modules needed for venv. The recommended approach is to add `python3-modules` to the image (full stdlib) and either use `python3-venv` during `ROOTFS_POSTPROCESS_COMMAND` or adopt the simpler system-install pattern where Python packages are installed as Yocto packages into a dedicated prefix. The venv approach is locked in the CONTEXT.md, so the `ROOTFS_POSTPROCESS_COMMAND` must include `python3-modules`, `python3-venv`, and pre-built wheels from `requirements.lock`.

The U-Boot A/B partition management is well-understood: replace `PartitionBackend`'s JSON-based boot flag with `fw_setenv`/`fw_printenv` calls via `libubootenv`. The existing `PartitionBackend` interface in `partition.py` is already structured for this replacement — `read_boot_flag()` becomes `fw_printenv ems_active_slot`, and `write_boot_flag()` becomes `fw_setenv ems_active_slot b`. No Advantech-specific machine configuration was found in public repositories; the ECU-1170 requires either the Advantech BSP GitHub (`adv-ti-yocto-bsp`) or a custom MACHINE conf derived from `am65xx-evm`.

**Primary recommendation:** Use Yocto Scarthgap (5.0 LTS, supported until April 2028) as the base release. Start meta-ems with `bitbake-layers create-layer`. For Python, use `python_setuptools_build_meta` bbclass (not the legacy `setuptools3`) with `uv_build` as build backend — but verify uv_build is available in Scarthgap; if not, pre-build wheels offline and install via pip in `ROOTFS_POSTPROCESS_COMMAND`.

---

## Standard Stack

### Core
| Library/Tool | Version | Purpose | Why Standard |
|---|---|---|---|
| Yocto / Poky | Scarthgap 5.0 (LTS) | Build system for embedded Linux images | LTS until 2028, widest BSP support |
| BitBake | 2.8.x (with Scarthgap) | Task scheduler for recipe builds | Native to Yocto |
| meta-ti-bsp | scarthgap branch | TI AM65xx hardware BSP layer | Official TI layer, includes AM65x MACHINE conf |
| meta-openembedded | scarthgap branch | Extra recipe collections (meta-oe, meta-python) | Provides many dependency recipes |
| libubootenv | 0.3.x | fw_setenv/fw_printenv userspace tools | Replacement for U-Boot tools/env, board-independent |
| openssl | 3.x | Certificate generation (CA + device certs) | Industry standard, available in all distros |

### Supporting
| Library/Tool | Version | Purpose | When to Use |
|---|---|---|---|
| devtool | (Yocto built-in) | Rapid recipe iteration with local source | Iterative development on a recipe |
| EXTERNALSRC | bbclass | Point recipe at local monorepo source | Avoids SRC_URI fetching during development |
| python3-modules | Yocto package | Full Python stdlib for venv | Required before `python3 -m venv` works in image |
| python3-venv | Yocto package | venv creation support | Must be in image if venv is built at rootfs post-process time |
| python3-pip | Yocto package | pip for offline wheel installation | Needed in ROOTFS_POSTPROCESS_COMMAND; exclude from final image if size matters |
| meta-readonly-rootfs-overlay | optional | Declarative volatile bind mounts | Alternative to hand-written fstab for /data overlay mounts |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|---|---|---|
| python_setuptools_build_meta | setuptools3 (legacy) | setuptools3 is deprecated; setuptools_build_meta is the PEP-517 compliant class |
| libubootenv fw_setenv | U-Boot tools/env (fw_setenv from u-boot source) | libubootenv is board-independent; u-boot tools/env requires U-Boot source match |
| ROOTFS_POSTPROCESS_COMMAND venv | System-install each Python package | System-install creates many small Yocto packages; venv provides isolation and pip's dependency resolver |
| Custom MACHINE conf (am65xx-ems) | am65xx-evm from meta-ti-bsp | Custom MACHINE is required for production; am65xx-evm is a starting point only |
| Scarthgap (5.0 LTS) | Nanbield (4.3) | Nanbield EOL May 2024; Scarthgap supported until 2028 |

### Installation

The Yocto build environment requires a host machine (Ubuntu 22.04/24.04) with BitBake dependencies:

```bash
# Ubuntu 24.04 host prerequisites
sudo apt-get install -y gawk wget git diffstat unzip texinfo gcc build-essential \
    chrpath socat cpio python3 python3-pip python3-pexpect xz-utils debianutils \
    iputils-ping python3-git python3-jinja2 python3-subunit zstd liblz4-tool file \
    locales libacl1

# Clone Poky (Scarthgap)
git clone -b scarthgap git://git.yoctoproject.org/poky
# Clone meta-ti-bsp
git clone -b scarthgap https://git.yoctoproject.org/meta-ti
# Clone meta-openembedded
git clone -b scarthgap https://github.com/openembedded/meta-openembedded.git
```

---

## Architecture Patterns

### Recommended Project Structure

```
yocto/
├── meta-ems/                          # EMS custom layer (in monorepo)
│   ├── conf/
│   │   ├── layer.conf                 # Layer registration
│   │   └── machine/
│   │       └── am65xx-ems.conf        # Custom MACHINE config (extends am65xx-evm)
│   ├── recipes-core/
│   │   ├── images/
│   │   │   └── core-image-ems.bb      # Image recipe
│   │   └── base-files/
│   │       └── base-files_%.bbappend  # fstab additions
│   ├── recipes-ems/
│   │   ├── ems-common-c/
│   │   │   └── ems-common-c_0.1.0.bb  # C shared library
│   │   ├── safety-manager/
│   │   │   └── safety-manager_0.1.0.bb
│   │   ├── comm-manager-c/
│   │   │   └── comm-manager-c_0.1.0.bb
│   │   ├── data-manager-c/
│   │   │   └── data-manager-c_0.1.0.bb
│   │   ├── control-manager-c/
│   │   │   └── control-manager-c_0.1.0.bb
│   │   ├── ems-python-venv/
│   │   │   └── ems-python-venv_0.1.0.bb  # Python venv build recipe
│   │   ├── ems-config/
│   │   │   └── ems-config_0.1.0.bb    # YAML configs + schemas
│   │   ├── ems-systemd-units/
│   │   │   └── ems-systemd-units_0.1.0.bb  # All 14 service files
│   │   └── ems-frontend/
│   │       └── ems-frontend_0.1.0.bb  # Pre-built React dist/
│   └── recipes-support/
│       └── libubootenv/
│           └── libubootenv_%.bbappend # fw_env.config for ECU-1170 eMMC layout
├── build/                             # Yocto build directory (gitignored)
│   └── conf/
│       ├── bblayers.conf              # Layer list
│       └── local.conf                 # Machine, distro, SSTATE_DIR
└── scripts/
    └── setup-yocto.sh                 # Initializes build environment
tools/
└── gen-device-cert.sh                 # Per-device mTLS certificate generator
```

### Pattern 1: Layer Registration (layer.conf)

**What:** Every Yocto layer must have `conf/layer.conf` declaring its identity, file patterns, priority, and dependencies.
**When to use:** Once per layer — this is the mandatory entry point.

```bitbake
# Source: https://docs.yoctoproject.org/dev-manual/layers.html
# yocto/meta-ems/conf/layer.conf

BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb ${LAYERDIR}/recipes-*/*/*.bbappend"

BBFILE_COLLECTIONS += "meta-ems"
BBFILE_PATTERN_meta-ems = "^${LAYERDIR}/"
BBFILE_PRIORITY_meta-ems = "10"

LAYERDEPENDS_meta-ems = "core meta-ti-bsp meta-oe meta-python"
LAYERSERIES_COMPAT_meta-ems = "scarthgap"
```

### Pattern 2: C Binary Recipe (cmake bbclass)

**What:** Recipes for C executables cross-compiled from the monorepo via EXTERNALSRC.
**When to use:** For all 5 C binaries — safety_manager, comm_manager_c, data_manager_c, control_manager_c, ems_common_c.

```bitbake
# yocto/meta-ems/recipes-ems/safety-manager/safety-manager_0.1.0.bb
SUMMARY = "EMS safety manager — PREEMPT_RT GPIO watchdog"
LICENSE = "CLOSED"

inherit cmake externalsrc

# Point at monorepo source during development; replace with git SRC_URI for CI
EXTERNALSRC = "${TOPDIR}/../src/safety_manager"
EXTERNALSRC_BUILD = "${WORKDIR}/build"

DEPENDS = "libzmq libgpiod ems-common-c"

EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"

do_install() {
    install -d ${D}${bindir}/../opt/ems/bin
    install -m 0755 ${B}/safety_manager ${D}/opt/ems/bin/safety_manager
}

FILES:${PN} = "/opt/ems/bin/safety_manager"
```

**Note:** The `cmake` bbclass auto-generates a `toolchain.cmake` that sets the Yocto sysroot — this is what replaces the commented-out `CMAKE_SYSROOT` in `cmake/toolchains/aarch64-linux.cmake`. Do NOT set `CMAKE_TOOLCHAIN_FILE` in `EXTRA_OECMAKE`; let the bbclass handle it.

### Pattern 3: Python Venv via ROOTFS_POSTPROCESS_COMMAND

**What:** Build a Python virtual environment during image construction using pre-built wheels from `requirements.lock`.
**When to use:** Required because read-only rootfs cannot run pip at runtime, and Yocto's minimal Python3 lacks the stdlib needed for venv.

```bitbake
# yocto/meta-ems/recipes-core/images/core-image-ems.bb
require recipes-core/images/core-image-minimal.bb

DESCRIPTION = "EMS production image for Advantech ECU-1170-552A"
IMAGE_FEATURES += "read-only-rootfs ssh-server-openssh"

# Python runtime requirements for venv creation
IMAGE_INSTALL:append = " \
    python3 \
    python3-modules \
    python3-venv \
    python3-pip \
    python3-wheel \
    ems-common-c \
    ems-python-venv \
    ems-config \
    ems-systemd-units \
    ems-frontend \
    safety-manager \
    comm-manager-c \
    data-manager-c \
    control-manager-c \
    libubootenv \
    libubootenv-bin \
"

DISTRO_FEATURES:append = " systemd"
VIRTUAL-RUNTIME_init_manager = "systemd"

# Build venv during rootfs creation (not at device runtime)
create_ems_venv() {
    # Wheels are pre-built and copied to the image by ems-python-venv recipe
    python3 -m venv --without-pip ${IMAGE_ROOTFS}/opt/ems/python/.venv
    ${IMAGE_ROOTFS}/opt/ems/python/.venv/bin/python3 -m pip install \
        --no-index \
        --find-links=${IMAGE_ROOTFS}/opt/ems/python/wheels \
        -r ${IMAGE_ROOTFS}/opt/ems/python/requirements.lock
    # Remove pip from venv to reduce image size (read-only rootfs, pip not needed at runtime)
    rm -rf ${IMAGE_ROOTFS}/opt/ems/python/.venv/lib/python*/site-packages/pip*
}
ROOTFS_POSTPROCESS_COMMAND += "create_ems_venv; "
```

**Critical issue:** Yocto's default `python3` install is minimal — it excludes many stdlib modules. Without `python3-modules`, `python3 -m venv` fails with `ModuleNotFoundError`. Always include `python3-modules` in `IMAGE_INSTALL`.

### Pattern 4: Read-Only Rootfs with fstab Overlays

**What:** Enable `IMAGE_FEATURES += "read-only-rootfs"` and configure tmpfs mount points for runtime directories.
**When to use:** Required for PROD-02.

```bitbake
# Method: bbappend to base-files to add fstab entries
# yocto/meta-ems/recipes-core/base-files/base-files_%.bbappend

do_install:append() {
    # Add EMS-specific fstab entries for tmpfs and data partition
    cat >> ${D}${sysconfdir}/fstab << 'EOF'
# EMS runtime directories
tmpfs   /tmp            tmpfs   defaults,size=50m,mode=1777   0 0
tmpfs   /var/run        tmpfs   defaults,size=10m,mode=0755   0 0
tmpfs   /run/ems        tmpfs   defaults,size=5m,mode=0755    0 0
tmpfs   /var/log/journal tmpfs  defaults,size=50m,mode=0755   0 0
# SSD data partition (survives OTA updates)
/dev/nvme0n1p1  /data   ext4    defaults,noatime              0 2
EOF
}
```

**Alternative:** Use `systemd-tmpfiles` for creating `/run/ems` at boot (preferred for systemd-based images):

```
# deploy/tmpfiles/ems.conf
d /run/ems 0755 ems ems -
d /data/config 0755 ems ems -
d /data/parquet 0755 ems ems -
```

### Pattern 5: U-Boot Partition Backend (replacing MockPartitionBackend)

**What:** Replace the JSON-based `PartitionBackend` with one that calls `fw_setenv`/`fw_printenv` from `libubootenv`.
**When to use:** PROD-03 — replaces `src/ota_manager/src/ems_ota_manager/partition.py` mock behavior.

The existing `PartitionBackend` is already structured with the right interface. The replacement adds a `UBootPartitionBackend` class that delegates to subprocess calls:

```python
# Pattern for UBootPartitionBackend
# read_boot_flag() -> calls: fw_printenv ems_active_slot ems_boot_count
# write_boot_flag() -> calls: fw_setenv ems_active_slot <value>
# Boot count management -> calls: fw_setenv ems_boot_count <value>
```

The `fw_env.config` file must be deployed to `/etc/fw_env.config` on the device, describing where the U-Boot environment lives on eMMC:

```
# /etc/fw_env.config — format: device  offset  size  [sectorsize  [nrofcopies]]
# ECU-1170 eMMC: U-Boot env at end of boot partition, two redundant copies
/dev/mmcblk0    0x3E0000    0x20000    0x200    1
/dev/mmcblk0    0x3E0000    0x20000    0x200    1
```

**Note:** The exact offset depends on the ECU-1170 eMMC layout. Verify with Advantech BSP or U-Boot environment configuration. The offset must match `CONFIG_ENV_OFFSET` in the U-Boot build config for this board.

### Pattern 6: U-Boot Bootscript for A/B Logic

**What:** U-Boot script that reads `ems_active_slot` and `ems_boot_count`, selects the correct rootfs partition, implements rollback on boot count > 2.
**When to use:** PROD-03 — deployed as `boot.scr` to the FAT32 boot partition.

```uboot
# boot.cmd — compiled to boot.scr with mkimage
# Logic:
#   1. Read ems_active_slot (a or b)
#   2. Increment ems_boot_count
#   3. If ems_boot_count > 2, revert to previous slot
#   4. Set bootargs with correct rootfs partition
#   5. Load kernel and boot

if test "${ems_active_slot}" = "b"; then
    setenv rootdev mmcblk0p3
    setenv prev_slot a
else
    setenv rootdev mmcblk0p2
    setenv prev_slot b
fi

if test "${ems_boot_count}" -gt "2"; then
    echo "Boot count exceeded — reverting to slot ${prev_slot}"
    setenv ems_active_slot ${prev_slot}
    setenv ems_boot_count 0
    saveenv
    if test "${prev_slot}" = "b"; then
        setenv rootdev mmcblk0p3
    else
        setenv rootdev mmcblk0p2
    fi
fi

setexpr ems_boot_count ${ems_boot_count} + 1
saveenv

setenv bootargs "console=ttyS2,115200n8 root=/dev/${rootdev} ro rootfstype=ext4 \
    rootwait systemd.unified_cgroup_hierarchy=1"
load mmc 0:1 ${loadaddr} Image
load mmc 0:1 ${fdtaddr} k3-am654-base-board.dtb
booti ${loadaddr} - ${fdtaddr}
```

### Anti-Patterns to Avoid

- **Calling pip or uv at runtime on the device:** The rootfs is read-only — all Python dependencies must be installed during image build.
- **Using `setuptools3` (legacy) for new Python recipes:** Use `python_setuptools_build_meta` or build wheels externally; `setuptools3` calls the deprecated `setup.py install` method.
- **Setting `CMAKE_TOOLCHAIN_FILE` in `EXTRA_OECMAKE`:** The `cmake` bbclass generates its own toolchain file with the correct sysroot; overriding it breaks cross-compilation.
- **Writing U-Boot env variables from Python without `fw_env.config`:** `fw_setenv` requires `/etc/fw_env.config` to know the eMMC location. Missing this file causes silent failure.
- **Running postinstall scripts that require the target architecture:** With `read-only-rootfs`, postinstall scripts run at build time on the host. Scripts that try to execute ARM64 binaries will fail. Use `$D` prefix for all paths.
- **Hardcoding mmcblk0p2/p3 device names:** Use U-Boot variables to parameterize the rootfs device — device node names can change across kernel versions.
- **Storing the CA private key in the Yocto image:** The CA key must stay on the build server. Only the signed device cert and its private key go into the image.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| U-Boot env read/write from Linux | Custom /dev/mmcblk0 reader | `libubootenv` (fw_setenv/fw_printenv) | Handles redundant env copies, CRC, offsets correctly |
| Python package index in image | Custom pip server | Pre-built wheels + `--no-index --find-links` | Deterministic, no network required, reproducible builds |
| Read-only rootfs volatile directories | Custom init scripts | systemd-tmpfiles.d + fstab tmpfs | Systemd handles ordering, creation, and permissions |
| A/B boot selection | Custom bootloader patch | U-Boot bootscript (boot.scr) | Standard U-Boot mechanism, no bootloader rebuild needed |
| Cross-compilation toolchain setup | Hand-crafted compiler flags | `cmake` bbclass auto-generated toolchain.cmake | Handles sysroot, includes, library search paths |
| Yocto layer scaffolding | Manual directory creation | `bitbake-layers create-layer meta-ems` | Generates correct layer.conf template |

**Key insight:** Most "hard" problems in embedded Linux image building are already solved by Yocto bbclasses and systemd utilities. The EMS meta-layer is primarily configuration and wiring — not custom tooling.

---

## Common Pitfalls

### Pitfall 1: Python venv Creation Fails in ROOTFS_POSTPROCESS_COMMAND

**What goes wrong:** `python3 -m venv` fails with `ModuleNotFoundError: No module named 'ensurepip'` or `No module named 'timeit'`.
**Why it happens:** Yocto's default `python3` recipe installs only a minimal subset of the standard library. Modules like `ensurepip`, `timeit`, `distutils`, and others are packaged separately.
**How to avoid:** Add `python3-modules` to `IMAGE_INSTALL` — this provides the full stdlib. Also add `python3-venv` and `python3-pip`.
**Warning signs:** Error messages referencing missing stdlib modules during image build do_rootfs task.

### Pitfall 2: ROOTFS_POSTPROCESS_COMMAND Runs on Host — Not Target

**What goes wrong:** Scripts in `ROOTFS_POSTPROCESS_COMMAND` that call target architecture binaries (e.g., running the venv's Python to test it) will fail because the host is x86_64.
**Why it happens:** Rootfs post-processing executes on the build host using the host's kernel.
**How to avoid:** Only use host-compatible operations: file copies, `sed`, `chmod`, or QEMU-wrapped calls. The `python3 -m venv` call works because it uses the host's Python3 (which must match version). Use `nativesdk-python3` for host-side pip operations.
**Warning signs:** `Exec format error` or `cannot execute binary file` during do_rootfs.

### Pitfall 3: U-Boot env.config Offset Mismatch

**What goes wrong:** `fw_printenv` reports "Warning: Bad CRC, using default environment" or returns empty values.
**Why it happens:** `/etc/fw_env.config` specifies the wrong offset or size for the U-Boot environment partition. The offset must exactly match `CONFIG_ENV_OFFSET` from the U-Boot defconfig used to build the bootloader on the ECU-1170.
**How to avoid:** Verify the offset by reading the U-Boot source for the am65xx platform: typically `0x3E0000` for the default eMMC layout, but Advantech may customize this. Test with `fw_printenv -c /etc/fw_env.config` before relying on it in the OTA manager.
**Warning signs:** `fw_setenv ems_active_slot b` succeeds with exit code 0 but reboot still boots the same slot.

### Pitfall 4: read-only-rootfs Breaks systemd Service Startup

**What goes wrong:** Services that write to `/etc/` or `/var/` at startup fail because the filesystem is read-only.
**Why it happens:** Some packages (e.g., openssh, dbus) write runtime files to locations that become read-only.
**How to avoid:** Use `IMAGE_FEATURES += "read-only-rootfs"` during early development to catch failures at build time (it fails the build if postinstall scripts need write access). For runtime write needs, ensure `/var/run` and `/tmp` are on tmpfs — systemd mounts these before starting services.
**Warning signs:** Services entering failed state immediately; journalctl showing "Read-only file system" errors.

### Pitfall 5: EXTERNALSRC Cache Invalidation

**What goes wrong:** Recipe rebuild doesn't pick up source changes when using `EXTERNALSRC`.
**Why it happens:** BitBake's incremental build cache uses checksums; `EXTERNALSRC` changes require manual cache invalidation.
**How to avoid:** Set `BB_GENERATE_MIRROR_TARBALLS = "0"` and use `bitbake -C fetch recipe-name` to force a rebuild, or use `devtool modify` workflow for iterative development.
**Warning signs:** Source changes don't appear in the built image without a `bitbake -C compile` forced rebuild.

### Pitfall 6: Advantech ECU-1170 No Pre-Built Machine Config

**What goes wrong:** No `am65xx-ems` MACHINE config exists in public meta-ti-bsp for the ECU-1170 specifically.
**Why it happens:** meta-ti-bsp only ships configurations for TI reference boards (am65xx-evm). Advantech's `adv-ti-yocto-bsp` repository (GitHub: ADVANTECH-Corp/adv-ti-yocto-bsp) may have a config, but was last updated in 2017 and targets older Yocto releases.
**How to avoid:** Create a custom `am65xx-ems.conf` MACHINE file in `meta-ems/conf/machine/` that includes the `am65xx-evm` config and overrides the `UBOOT_MACHINE`, `SERIAL_CONSOLES`, and `MACHINE_FEATURES` for the ECU-1170 hardware.
**Warning signs:** Missing machine-specific DTB, wrong UART console, wrong eMMC device path.

---

## Code Examples

Verified patterns from official sources and research:

### layer.conf Skeleton
```bitbake
# Source: https://docs.yoctoproject.org/dev-manual/layers.html
# yocto/meta-ems/conf/layer.conf
BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb ${LAYERDIR}/recipes-*/*/*.bbappend"
BBFILE_COLLECTIONS += "meta-ems"
BBFILE_PATTERN_meta-ems = "^${LAYERDIR}/"
BBFILE_PRIORITY_meta-ems = "10"
LAYERDEPENDS_meta-ems = "core meta-ti-bsp meta-oe meta-python"
LAYERSERIES_COMPAT_meta-ems = "scarthgap"
```

### C Binary Recipe with cmake bbclass
```bitbake
# Source: https://docs.yoctoproject.org/ref-manual/classes.html (cmake section)
SUMMARY = "EMS safety manager"
LICENSE = "CLOSED"
inherit cmake externalsrc

EXTERNALSRC = "${TOPDIR}/../src/safety_manager"
EXTERNALSRC_BUILD = "${WORKDIR}/build"
DEPENDS = "libzmq libgpiod ems-common-c"
EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"

do_install() {
    install -d ${D}/opt/ems/bin
    install -m 0755 ${B}/safety_manager ${D}/opt/ems/bin/
}
FILES:${PN} = "/opt/ems/bin/safety_manager"
```

### Python Package Recipe with setuptools_build_meta
```bitbake
# Source: https://docs.yoctoproject.org/ref-manual/classes.html
SUMMARY = "EMS common Python library"
LICENSE = "CLOSED"
inherit python_setuptools_build_meta externalsrc

EXTERNALSRC = "${TOPDIR}/../src/common/python"
EXTERNALSRC_BUILD = "${WORKDIR}/build"

RDEPENDS:${PN} = "python3-core python3-modules"
```

### fw_env.config for eMMC U-Boot Environment
```
# Source: https://github.com/sbabic/libubootenv
# /etc/fw_env.config
# Format: device  offset  size  [sectorsize  [nrofcopies]]
# These values must match CONFIG_ENV_OFFSET in U-Boot defconfig for ECU-1170
/dev/mmcblk0    0x3E0000    0x20000    0x200    1
/dev/mmcblk0    0x3E0000    0x20000    0x200    1
```

### UBootPartitionBackend Sketch (replacing mock)
```python
# Pattern for partition.py UBootPartitionBackend
# replaces JSON-based BootFlag with fw_setenv/fw_printenv calls
import subprocess

async def _fw_getenv(key: str) -> str:
    result = await asyncio.create_subprocess_exec(
        "fw_printenv", "-n", key,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await result.communicate()
    return stdout.decode().strip()

async def _fw_setenv(key: str, value: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "fw_setenv", key, value,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"fw_setenv {key}={value} failed: {stderr.decode()}")
```

### mTLS Certificate Generation Script
```bash
#!/usr/bin/env bash
# tools/gen-device-cert.sh
# Usage: ./gen-device-cert.sh <site_id> <serial_number> <ca_key> <ca_cert> <out_dir>
# Source: https://victoronsoftware.com/posts/mtls-hello-world/

SITE_ID="$1"; SERIAL="$2"; CA_KEY="$3"; CA_CERT="$4"; OUT="$5"
CN="${SITE_ID}-${SERIAL}"

# Generate device private key (RSA 4096 for MQTT mTLS)
openssl genrsa -out "${OUT}/device.key" 4096
chmod 600 "${OUT}/device.key"

# Generate CSR with device CN
openssl req -new -key "${OUT}/device.key" \
    -subj "/CN=${CN}/O=ReVx-Energy/OU=EMS" \
    -out "${OUT}/device.csr"

# CA signs the CSR
openssl x509 -req \
    -in "${OUT}/device.csr" \
    -CA "${CA_CERT}" \
    -CAkey "${CA_KEY}" \
    -CAcreateserial \
    -days 365 \
    -sha256 \
    -out "${OUT}/device.crt"

# Copy CA cert (for MQTT broker verification)
cp "${CA_CERT}" "${OUT}/ca.crt"

# Cleanup CSR
rm "${OUT}/device.csr"
echo "Certificates generated for ${CN} in ${OUT}/"
```

### Image Recipe with read-only-rootfs
```bitbake
# Source: https://docs.yoctoproject.org/5.0.6/dev-manual/read-only-rootfs.html
require recipes-core/images/core-image-minimal.bb

IMAGE_FEATURES += "read-only-rootfs ssh-server-openssh"
IMAGE_INSTALL:append = " python3 python3-modules python3-venv python3-pip \
    libubootenv libubootenv-bin safety-manager ems-config ems-systemd-units"

create_ems_venv() {
    python3 -m venv --without-pip ${IMAGE_ROOTFS}/opt/ems/python/.venv
    ${IMAGE_ROOTFS}/opt/ems/python/.venv/bin/python3 -m pip install \
        --no-index \
        --find-links=${IMAGE_ROOTFS}/opt/ems/python/wheels \
        -r ${IMAGE_ROOTFS}/opt/ems/python/requirements.lock
}
ROOTFS_POSTPROCESS_COMMAND += "create_ems_venv; "
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|---|---|---|---|
| `setuptools3` bbclass (setup.py install) | `python_setuptools_build_meta` (PEP-517 wheels) | Yocto 4.0 Kirkstone (2022) | Must use build_meta for packages with `uv_build` backend |
| U-Boot tools/env (built with U-Boot source) | `libubootenv` (board-independent) | ~2018, mainstream since ~2021 | No need to match U-Boot source version |
| `RAUC`/`swupdate` for OTA A/B | Custom U-Boot bootscript + dd | N/A (decision was to avoid RAUC for v1) | Simpler for small fleet, less infrastructure |
| Yocto Nanbield (4.3) | Yocto Scarthgap (5.0 LTS) | April 2024 | Nanbield EOL May 2024; Scarthgap supported to 2028 |
| `pip install` at runtime | Pre-built wheels in image | Always best practice for embedded | Required with read-only rootfs |

**Deprecated/outdated:**
- `setuptools3` bbclass: Still present in Yocto but deprecated in favor of `python_setuptools_build_meta` for PEP-517 build backends
- Yocto Nanbield (4.3): EOL since May 2024 — do not start new projects on it
- `adv-ti-yocto-bsp` (Advantech GitHub): Last update 2017, targets old Arago/Yocto releases — not usable for Scarthgap

---

## Open Questions

1. **Advantech ECU-1170 U-Boot CONFIG_ENV_OFFSET**
   - What we know: The offset must be read from the actual U-Boot defconfig for the ECU-1170, not guessed. The typical am65xx-evm value is `0x3E0000`.
   - What's unclear: Advantech may use a different offset or a dedicated U-Boot env partition (mmcblk0boot0).
   - Recommendation: Document this as a prerequisite — get the actual defconfig from Advantech before implementing `UBootPartitionBackend`. Use a fallback config path (e.g., `/data/fw_env.config`) so it can be corrected without rebuilding the image.

2. **Advantech ECU-1170 Machine Config Source**
   - What we know: No ECU-1170 machine config exists in public meta-ti-bsp (scarthgap). `adv-ti-yocto-bsp` is outdated.
   - What's unclear: Whether Advantech provides a private/NDA BSP layer, or whether `am65xx-evm` is close enough to start.
   - Recommendation: Begin with `am65xx-evm` as the MACHINE. Create `am65xx-ems.conf` in meta-ems that `require conf/machine/am65xx-evm.conf` and overrides only what's needed. Defer hardware validation to Phase 29.

3. **Python venv host-side vs target-side execution**
   - What we know: `ROOTFS_POSTPROCESS_COMMAND` runs on the build host (x86_64). Installing ARM64 wheels with `pip --no-index` works IF the wheels are pure-Python or pre-compiled for aarch64.
   - What's unclear: Some EMS dependencies (pyzmq, msgpack) have C extensions and must be compiled for aarch64 or have aarch64 wheels available.
   - Recommendation: Build Python packages as Yocto packages (inheriting `python_setuptools_build_meta`) using the cross-compilation toolchain for any C-extension packages, then copy the compiled `.so` files into the venv. Pure-Python packages can be installed from wheels in `ROOTFS_POSTPROCESS_COMMAND`.

4. **QEMU ARM64 for read-only-rootfs testing**
   - What we know: QEMU aarch64 can boot a Yocto ext4 image for basic validation. `IMAGE_FSTYPES = "ext4 wic"` enables both formats.
   - What's unclear: Whether QEMU testing is needed in Phase 27 or if Phase 29 hardware validation covers it.
   - Recommendation: Add QEMU smoke test as optional — if the CI environment has qemu-system-aarch64, boot test the image and verify `/` is read-only and `/tmp` is writable. Don't block Phase 27 completion on it.

---

## Validation Architecture

### Test Framework

| Property | Value |
|---|---|
| Framework | pytest 8.x + pytest-asyncio |
| Config file | `/home/overlord/EMS/pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/test_ota_manager.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROD-01 | Yocto recipes build without errors (bitbake dry-run / layer syntax check) | smoke | `bitbake-layers show-recipes 2>&1 \| grep ems` | ❌ Wave 0 (Yocto build env required) |
| PROD-01 | C binary CMakeLists cross-compile flag (CMAKE_SYSROOT) is uncommented | unit | `uv run pytest tests/test_yocto_recipes.py::test_cmake_sysroot_configured -x` | ❌ Wave 0 |
| PROD-02 | Image fstab contains tmpfs entries for /tmp, /var/run, /run/ems | unit | `uv run pytest tests/test_yocto_recipes.py::test_fstab_tmpfs_entries -x` | ❌ Wave 0 |
| PROD-03 | UBootPartitionBackend.read_boot_flag calls fw_printenv subprocess | unit | `uv run pytest tests/test_ota_manager.py::test_uboot_backend_read_active_slot -x` | ❌ Wave 0 |
| PROD-03 | UBootPartitionBackend.write_boot_flag calls fw_setenv with correct args | unit | `uv run pytest tests/test_ota_manager.py::test_uboot_backend_set_active_slot -x` | ❌ Wave 0 |
| PROD-03 | Boot count rollback logic: boot_count > 2 triggers slot reversion | unit | `uv run pytest tests/test_ota_manager.py::test_uboot_rollback_on_high_boot_count -x` | ❌ Wave 0 |
| PROD-04 | gen-device-cert.sh produces ca.crt, device.crt, device.key | unit | `uv run pytest tests/test_cert_provisioning.py::test_gen_device_cert_outputs -x` | ❌ Wave 0 |
| PROD-04 | device.key has permissions 600 | unit | `uv run pytest tests/test_cert_provisioning.py::test_device_key_permissions -x` | ❌ Wave 0 |
| PROD-04 | device.crt CN matches site_id-serial_number format | unit | `uv run pytest tests/test_cert_provisioning.py::test_device_cert_cn -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_ota_manager.py tests/test_cert_provisioning.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_yocto_recipes.py` — covers PROD-01 (recipe syntax), PROD-02 (fstab content)
- [ ] `tests/test_cert_provisioning.py` — covers PROD-04 (gen-device-cert.sh output validation)
- [ ] New test cases in `tests/test_ota_manager.py` — covers PROD-03 (`UBootPartitionBackend` with mocked subprocess)
- [ ] `yocto/meta-ems/` directory structure — required before any bitbake validation

**Note on PROD-01 Yocto build validation:** Full `bitbake` execution requires a Yocto build host (Ubuntu 22.04/24.04 with 50GB+ disk, not feasible in unit test). PROD-01 validation in this phase is limited to: recipe file existence checks, layer.conf syntax validation, and `bitbake-layers show-layers` output. Full bitbake smoke test deferred to Phase 29 hardware validation.

---

## Sources

### Primary (HIGH confidence)
- [Yocto Scarthgap Layer Guide](https://docs.yoctoproject.org/dev-manual/layers.html) — layer.conf structure, BBFILES, BBFILE_COLLECTIONS, LAYERDEPENDS
- [Yocto Read-Only Rootfs (5.0.6)](https://docs.yoctoproject.org/5.0.6/dev-manual/read-only-rootfs.html) — IMAGE_FEATURES, postinstall script constraints
- [Yocto Classes Reference](https://docs.yoctoproject.org/ref-manual/classes.html) — cmake, setuptools3, python_setuptools_build_meta, systemd, externalsrc
- [libubootenv GitHub](https://github.com/sbabic/libubootenv) — fw_setenv/fw_printenv interface, fw_env.config format
- [meta-ti-bsp (scarthgap)](https://layers.openembedded.org/layerindex/branch/scarthgap/layer/meta-ti-bsp/) — AM65xx platform support confirmed

### Secondary (MEDIUM confidence)
- [Toradex: Yocto Python venv problems](https://community.toradex.com/t/problems-with-yocto-python3-virtual-environments/26791) — python3-modules required, verified against multiple community sources
- [Mender Hub: Python in Yocto](https://hub.mender.io/t/how-to-work-with-python-applications-and-modules-in-yocto-project/1135) — pypi + setuptools3 recipe patterns
- [mTLS OpenSSL commands](https://victoronsoftware.com/posts/mtls-hello-world/) — CA + device cert generation commands
- [Yocto release notes: Kirkstone Python wheel migration](https://docs.yoctoproject.org/migration-guides/migration-4.0.html) — setuptools3 → setuptools_build_meta migration

### Tertiary (LOW confidence)
- Advantech adv-ti-yocto-bsp (GitHub: ADVANTECH-Corp/adv-ti-yocto-bsp) — outdated (2017), not usable for Scarthgap; flagged for validation
- U-Boot bootscript A/B logic pattern — inferred from STM32MP, Variscite community posts; exact implementation depends on ECU-1170 U-Boot defconfig

---

## Metadata

**Confidence breakdown:**
- Standard stack (Yocto Scarthgap, meta-ti-bsp, libubootenv): HIGH — confirmed via official Yocto docs and layer index
- Architecture (meta-ems structure, cmake/python recipes): HIGH — from official bbclass docs
- Python venv in image: MEDIUM — confirmed python3-modules requirement from community source; exact wheel installation approach needs validation with actual Yocto build
- U-Boot fw_env.config offsets: LOW — ECU-1170 specific offset unknown; verified fw_env.config format but not board-specific values
- mTLS cert generation: HIGH — standard openssl commands, well-documented
- Advantech ECU-1170 BSP: LOW — no current public BSP for Scarthgap found; adv-ti-yocto-bsp is 2017 vintage

**Research date:** 2026-03-16
**Valid until:** 2026-06-16 (90 days — Yocto Scarthgap is stable LTS; Python tooling stable; U-Boot env tools stable)
