---
phase: 28-production-hardening
plan: "03"
subsystem: deploy/systemd
tags: [systemd, hardening, security, prod-06, yocto]
dependency_graph:
  requires: []
  provides: [hardened-systemd-units]
  affects: [all-ems-services]
tech_stack:
  added: []
  patterns: [ProtectSystem=strict, RuntimeDirectory, CapabilityBoundingSet, SystemCallFilter]
key_files:
  created: []
  modified:
    - deploy/systemd/safety_manager.service
    - deploy/systemd/comm_manager_c.service
    - deploy/systemd/ems-data-manager.service
    - deploy/systemd/cloud_manager.service
    - deploy/systemd/hmi_server.service
    - deploy/systemd/ota_manager.service
    - deploy/systemd/data_manager.service
    - deploy/systemd/comm_manager.service
    - deploy/systemd/control_manager.service
    - deploy/systemd/alarm_manager.service
    - deploy/systemd/scheduler.service
    - deploy/systemd/diagnostics.service
    - deploy/systemd/logger.service
    - deploy/systemd/config_manager.service
    - deploy/systemd/ems-data-manager-python.service
    - deploy/systemd/ems-config-manager.service
decisions:
  - "safety_manager uses NoNewPrivileges=no (preserved) to allow AmbientCapabilities elevation for RT scheduling"
  - "C services (safety_manager, comm_manager_c, ems-data-manager) omit CPUQuota — critical real-time services must not be CPU-throttled"
  - "ReadWritePaths uses /data (SSD) not /opt/ems/logs — matches production data path decision from CONTEXT.md"
  - "ota_manager and comm_manager ExecStart fixed to /opt/ems/python/.venv/bin/python — uv not present on Yocto rootfs"
metrics:
  duration: 3m5s
  completed_date: "2026-03-16"
  tasks_completed: 2
  files_modified: 16
---

# Phase 28 Plan 03: Systemd Service Hardening Summary

Hardened all 16 EMS systemd service files with tiered security directives (ProtectSystem=strict for all, MemoryMax=64M for C/safety, 256M for Python, CPUQuota=50% for Python only) and fixed ExecStart paths to use the Yocto venv.

## What Was Built

Applied production security hardening (PROD-06) across all 16 EMS systemd service units using a three-tier security model:

**Tier 1 — safety_manager (strictest):**
- Preserved `NoNewPrivileges=no` for AmbientCapabilities (RT scheduling)
- Fixed `ReadWritePaths` to use `/data` (SSD) instead of old `/opt/ems/logs`
- Added `CapabilityBoundingSet=CAP_SYS_NICE CAP_SYS_RAWIO` (least-privilege caps)
- Added `SystemCallFilter=@system-service @io-event` (GPIO I/O events)
- Added `MemoryMax=64M / MemoryHigh=48M`, `RuntimeDirectory=ems`
- No CPUQuota — safety must never be CPU-starved

**Tier 2 — C binary services (comm_manager_c, ems-data-manager):**
- Full hardening block: ProtectSystem=strict, PrivateTmp, ProtectHome, NoNewPrivileges
- `MemoryMax=64M / MemoryHigh=48M`, `RuntimeDirectory=ems`
- comm_manager_c: `NoNewPrivileges=no` and `CapabilityBoundingSet=CAP_NET_RAW` (preserves CAP_NET_RAW for CAN)
- No CPUQuota — lightweight C binaries do not warrant throttling

**Tier 3 — Python services (13 services):**
- Standard hardening: ProtectSystem=strict, ProtectHome, PrivateTmp, NoNewPrivileges=yes
- `MemoryMax=256M / MemoryHigh=200M`, `CPUQuota=50%`, `RuntimeDirectory=ems`
- `SystemCallFilter=@system-service`

**ExecStart path fixes:**
- `ota_manager.service`: `uv run python` → `/opt/ems/python/.venv/bin/python`
- `comm_manager.service`: `/usr/bin/env uv run python` → `/opt/ems/python/.venv/bin/python`
- `ems-data-manager-python.service`: `/opt/ems/venv/bin/python` → `/opt/ems/python/.venv/bin/python`
- `ems-config-manager.service`: `/opt/ems/venv/bin/python` → `/opt/ems/python/.venv/bin/python`

## Verification Results

- 16/16 service files contain `ProtectSystem=strict`
- 16/16 service files contain `MemoryMax`
- 16/16 service files contain `RuntimeDirectory=ems`
- 0 service files contain `uv run` in ExecStart
- 0 service files contain `/opt/ems/venv/` (old path)
- `safety_manager.service` preserves `NoNewPrivileges=no`
- `comm_manager.service` preserves `SupplementaryGroups=dialout`
- 13 Python services have `CPUQuota=50%`
- 3 C/safety services have no CPUQuota

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Harden safety_manager + C service units | 2094396 |
| 2 | Harden all Python service units + fix ExecStart paths | 78cb3d4 |

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

All 16 service files verified present and contain required directives.
Commits 2094396 and 78cb3d4 verified in git log.
