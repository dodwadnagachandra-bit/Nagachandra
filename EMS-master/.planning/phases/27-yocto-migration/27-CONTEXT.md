# Phase 27: Yocto Migration - Context

**Gathered:** 2026-03-16
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Yocto recipes for all EMS modules, read-only rootfs configuration, real A/B partition management via U-Boot, and mTLS certificate provisioning. Covers PROD-01 through PROD-04.

</domain>

<decisions>
## Implementation Decisions

### Yocto Recipe Strategy

How should the 12 Python packages and 5 C executables be packaged for Yocto?

**Decision:** One meta-layer (`meta-ems`) with individual recipes per module. Python packages use `python3-setuptools` class, C binaries use `cmake` class.

| Recipe Type | Modules | Yocto Class | Deployment |
|------------|---------|-------------|------------|
| C binary | safety_manager, comm_manager_c, data_manager_c | cmake | `/opt/ems/bin/` |
| Python package | All 12 Python modules | python3-setuptools (uv builds) | `/opt/ems/python/.venv/` |
| Config files | 14 YAML + schemas | FILES recipe | `/etc/ems/config/` |
| Systemd units | 14 service files | systemd class | `/usr/lib/systemd/system/` |
| Frontend build | React HMI (pre-built dist/) | FILES recipe | `/opt/ems/frontend/dist/` |

Key rules:
- `meta-ems/` layer lives in the EMS monorepo under `yocto/meta-ems/`.
- Python virtual environment created during image build (not at runtime) — `ROOTFS_POSTPROCESS_COMMAND`.
- All dependency versions pinned in `requirements.lock` (generated from pyproject.toml via `uv pip compile`).
- C binaries cross-compiled using the Yocto SDK sysroot (uncomment CMAKE_SYSROOT in toolchain file).
- Frontend pre-built on dev machine (`bun run build`), copied as static files — no bun/Node.js in Yocto image.

**Rationale:** Individual recipes per module enable selective rebuilds when only one module changes. The meta-layer pattern is standard Yocto practice (meta-openembedded, meta-qt5). Python venv during build avoids runtime pip, which would require network access on a read-only rootfs. Pre-built frontend avoids needing Node.js/bun in the Yocto SDK.

### Read-Only Rootfs Configuration

How should the read-only rootfs work with EMS's data and runtime requirements?

**Decision:** rootfs read-only with specific tmpfs overlays and a persistent data partition on SSD.

| Mount Point | Type | Purpose |
|------------|------|---------|
| `/` (rootfs) | Read-only ext4 on eMMC | OS + EMS binaries + config |
| `/tmp` | tmpfs (50MB) | Temporary files |
| `/var/run` | tmpfs (10MB) | PID files, systemd runtime |
| `/run/ems` | tmpfs (5MB) | ZMQ IPC sockets |
| `/data` | Read-write ext4 on SSD | Parquet, JSONL, cloud buffer, RTDB snapshots |
| `/etc/ems/certs` | Read-only (in rootfs) | mTLS certificates (baked into image) |
| `/var/log/journal` | tmpfs (50MB) | systemd journal (volatile) |

Key rules:
- Config files (`/etc/ems/config/`) are read-only — hot-reload modifies files on `/data/config/` overlay.
- RTDB shared memory (`/dev/shm/ems_rtdb`) is tmpfs by default — no change needed.
- ZMQ IPC sockets under `/run/ems/` are tmpfs — created at runtime by data_manager.
- SSD data partition survives OTA updates (separate from A/B rootfs partitions).
- Journal is volatile (tmpfs) — structured logs go to logger JSONL on SSD, not systemd journal.

**Rationale:** Read-only rootfs prevents filesystem corruption from power loss (common in BESS installations — no UPS on the controller). tmpfs overlays for runtime data are standard embedded Linux practice. Persistent data on SSD keeps telemetry across reboots and OTA updates. Hot-reload config overlay on `/data/config/` allows site-specific tuning without modifying the rootfs image.

### Real A/B Partition Layout

How should the A/B partitions be organized on the ECU-1170-552A's 64GB eMMC?

**Decision:** Four eMMC partitions plus one SSD partition.

| Partition | Device | Size | Filesystem | Purpose |
|-----------|--------|------|-----------|---------|
| boot | mmcblk0p1 | 256MB | FAT32 | U-Boot + kernel + DTB |
| rootfs_a | mmcblk0p2 | 4GB | ext4 (ro) | System A |
| rootfs_b | mmcblk0p3 | 4GB | ext4 (ro) | System B |
| data | SSD (nvme0n1p1) | Remaining | ext4 (rw) | Parquet, JSONL, buffer, snapshots, config overlay |

Key rules:
- U-Boot environment variable `ems_active_slot=a|b` determines boot partition.
- OTA manager writes to the inactive slot, then `fw_setenv ems_active_slot b` (or a) to swap.
- `ems_boot_count` in U-Boot env incremented by bootscript, cleared by ota_manager health check.
- If `ems_boot_count > 2`, bootscript auto-reverts to previous slot (U-Boot level rollback).
- 4GB per rootfs is sufficient — full EMS with Python venv + React build is ~1.5GB.

**Rationale:** A/B on eMMC with data on SSD is the standard embedded OTA pattern. U-Boot env for boot flag is more reliable than a JSON file on a separate partition (U-Boot env is designed for atomic updates). Boot count in U-Boot handles crashes before the OS even starts — the mock JSON backend (M4) only handles post-boot health. 4GB rootfs provides 2.5GB headroom for future growth.

### mTLS Certificate Provisioning

How should device certificates be generated and deployed?

**Decision:** Build-time provisioning — certificates baked into the Yocto image per-device. Not runtime provisioning.

| Artifact | Location | Generated By | Lifecycle |
|----------|----------|-------------|-----------|
| CA certificate | `/etc/ems/certs/ca.crt` | Build server (shared across fleet) | Rotate annually |
| Device certificate | `/etc/ems/certs/device.crt` | Build server (per serial number) | Rotate annually |
| Device private key | `/etc/ems/certs/device.key` | Build server (chmod 600, ems:ems) | Rotate annually |
| MQTT broker URL | `/etc/ems/config/cloud_config.yaml` | Build server (per deployment) | Static |

Key rules:
- Each device gets a unique certificate with CN=`{site_id}-{serial_number}`.
- Private key file permissions: 600, owned by ems:ems — never readable by other users.
- Certificate generation script in `tools/gen-device-cert.sh` using openssl.
- Certificate rotation: new image with new certs deployed via OTA — no online rotation needed for v1.
- Build server holds the CA private key — never deployed to devices.

**Rationale:** Build-time provisioning is simplest for a small fleet (10-100 devices). Runtime provisioning (EST, CMP, ACME) adds infrastructure complexity for v1. Per-device CN enables broker-side device identification. Annual rotation via OTA image update is operationally simple — the build pipeline regenerates certs and deploys a new image.

### Claude's Discretion

- Yocto layer structure (recipes-ems/, recipes-core/, conf/)
- Yocto image recipe (core-image-ems vs extending core-image-minimal)
- Python venv creation in ROOTFS_POSTPROCESS_COMMAND vs package manager
- U-Boot environment partition location and access method (fw_setenv/fw_printenv)
- Hot-reload config overlay mechanism (/data/config/ bind-mounted over /etc/ems/config/)
- Certificate generation script implementation
- Test strategy (QEMU ARM64 for rootfs validation, or defer to Phase 29 hardware)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `cmake/toolchains/aarch64-linux.cmake` — ARM64 toolchain (CMAKE_SYSROOT commented out for Yocto)
- `tools/setup-dev-env.sh` — Dev environment setup (reference for Yocto SDK requirements)
- `deploy/systemd/` — All 14 service files ready for Yocto image
- `config/` — 14 YAML configs + 16 schemas + 3 profiles
- M4 `ota_manager/partition.py` — MockPartitionBackend to be replaced with real U-Boot backend

### Integration Points
- All C binaries link against `ems_common_c` (shared library in Yocto image)
- All Python packages depend on `ems-common` (workspace package)
- React frontend pre-built (`frontend/dist/`) — no runtime build needed
- OTA manager's `PartitionBackend` interface needs real implementation

</code_context>

<deferred>
## Deferred Ideas

- Runtime certificate rotation (EST/ACME) — v2+ when fleet grows
- RAUC/swupdate integration — custom OTA is simpler for v1
- Secure boot (signed kernel/DTB) — v2+ with SIL-2
- dm-verity for rootfs integrity — v2+

</deferred>

---

*Phase: 27-yocto-migration*
*Context gathered: 2026-03-16*
