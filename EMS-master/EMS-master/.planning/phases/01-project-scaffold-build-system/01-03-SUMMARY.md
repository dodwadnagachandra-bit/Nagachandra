---
phase: 01-project-scaffold-build-system
plan: 03
subsystem: infra
tags: [systemd, deploy, ecu, arm64, SocketCAN, gpio, can-utils, uv, bun, cmake]

# Dependency graph
requires:
  - phase: 01-project-scaffold-build-system
    plan: 01
    provides: monorepo directory structure and Makefile scaffold

provides:
  - 12 systemd .service stubs for all EMS modules at /opt/ems/ install paths
  - ems.target grouping all 12 services for unified start/stop
  - tools/verify-dev-env.sh smoke-test script covering 13 dev prerequisites
  - docs/ecu-bringup-checklist.md — 10-step ECU-1170-552A hardware bring-up procedure

affects:
  - All later phases (all module implementations will use these systemd stubs)
  - Phase 8 (Integration) — ems.target used for production deployment validation
  - ECU hardware bring-up milestone (PLAT-01 hardware deferred steps documented)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "systemd PartOf=ems.target pattern — every service declares membership in ems.target for unified lifecycle management"
    - "Hybrid C+Python module pattern — C component launched as subprocess by Python orchestrator, single Python service entry point"
    - "Dev environment smoke test pattern — verify-dev-env.sh checks all prerequisites at setup completion"

key-files:
  created:
    - deploy/systemd/ems.target
    - deploy/systemd/safety_manager.service
    - deploy/systemd/comm_manager.service
    - deploy/systemd/data_manager.service
    - deploy/systemd/logger.service
    - deploy/systemd/config_manager.service
    - deploy/systemd/control_manager.service
    - deploy/systemd/alarm_manager.service
    - deploy/systemd/scheduler.service
    - deploy/systemd/diagnostics.service
    - deploy/systemd/cloud_manager.service
    - deploy/systemd/ota_manager.service
    - deploy/systemd/hmi_server.service
    - tools/verify-dev-env.sh
    - docs/ecu-bringup-checklist.md
  modified:
    - Makefile

key-decisions:
  - "C-only service (safety_manager) uses ExecStart=/opt/ems/bin/safety_manager directly; Python and hybrid services use /opt/ems/python/.venv/bin/python -m ems_{module} for consistent virtualenv isolation"
  - "safety_manager has commented-out RT scheduling directives (LimitRTPRIO, CPUSchedulingPolicy=fifo) so they are discoverable but require explicit enablement when PREEMPT_RT kernel is confirmed on ECU"
  - "gpio-sim check in verify-dev-env.sh is a WARN not FAIL — gpio-sim requires kernel >= 5.17 and may not be present; safety GPIO simulator can use mock driver as fallback"
  - "verify-dev-env.sh uses manual PASS_COUNT/FAIL_COUNT tracking (not set -euo pipefail) to report all failures in one run rather than stopping at first failure"

patterns-established:
  - "systemd service pattern: Type=simple, Restart=on-failure, RestartSec=5, User=ems, Group=ems, WorkingDirectory=/opt/ems, journal logging"
  - "Every service declares PartOf=ems.target in [Unit] AND WantedBy=ems.target in [Install]"

requirements-completed:
  - PLAT-01

# Metrics
duration: 4min
completed: 2026-02-26
---

# Phase 1 Plan 03: Systemd Stubs, Dev Verification, and ECU Checklist Summary

**12 systemd service stubs with ems.target, a 13-check dev environment verification script, and a 10-step ECU-1170-552A hardware bring-up checklist satisfying PLAT-01**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-26T11:28:28Z
- **Completed:** 2026-02-26T11:31:58Z
- **Tasks:** 2
- **Files modified:** 16 (13 created in deploy/systemd/, 2 new files, 1 Makefile edit)

## Accomplishments

- Created ems.target and 12 .service stubs covering all 5 EMS layers (safety, comms, data, app, cloud) with consistent /opt/ems/ paths, ems user/group, and journal logging
- Created tools/verify-dev-env.sh with 13 prerequisite checks (cmake>=3.22, gcc, cross-compiler, uv, Python>=3.12, bun, clang-format, ruff, can-utils, socat, vcan, gpio-sim), callable as `make setup` final step
- Created docs/ecu-bringup-checklist.md with 10 checkbox-driven steps covering BSP flash through systemd service enablement, capturing all deferred PLAT-01 hardware steps
- Updated Makefile `setup` target to call `bash tools/verify-dev-env.sh` as final verification step

## Task Commits

Each task was committed atomically:

1. **Task 1: Create systemd service stubs for all 12 modules** - `8e36d91` (chore)
2. **Task 2: Create dev environment verification script and ECU bring-up checklist** - `fb8e662` (chore)

**Plan metadata:** `6578278` (docs: complete systemd stubs and platform plan — Phase 1 complete)

## Files Created/Modified

- `deploy/systemd/ems.target` - Groups all 12 services, WantedBy=multi-user.target
- `deploy/systemd/safety_manager.service` - L1 Safety, C binary, commented RT scheduling hints
- `deploy/systemd/comm_manager.service` - L2 Comms, Python orchestrator with C subprocess note
- `deploy/systemd/data_manager.service` - L3 Data, Python orchestrator with C subprocess note
- `deploy/systemd/logger.service` - L3 Data, Python orchestrator with C++ subprocess note
- `deploy/systemd/config_manager.service` - L3 Config, Python-only
- `deploy/systemd/control_manager.service` - L4 Control, Python orchestrator with C subprocess note
- `deploy/systemd/alarm_manager.service` - L4 Alarms, Python-only (IEC 62682)
- `deploy/systemd/scheduler.service` - L4 Scheduler, Python-only
- `deploy/systemd/diagnostics.service` - L4 Diagnostics, Python-only
- `deploy/systemd/cloud_manager.service` - L5 Cloud, Python-only (MQTT/TLS)
- `deploy/systemd/ota_manager.service` - L5 OTA, Python-only
- `deploy/systemd/hmi_server.service` - L5 HMI, Python-only with EMS_HMI_PORT=8080
- `tools/verify-dev-env.sh` - Executable bash script, 13 checks, colored PASS/FAIL/WARN output
- `docs/ecu-bringup-checklist.md` - 10-step ECU bring-up, checkbox format, hardware notes
- `Makefile` - Added `bash tools/verify-dev-env.sh` call at end of setup target

## Decisions Made

- C-only service (safety_manager) uses ExecStart=/opt/ems/bin/safety_manager directly; Python and hybrid services use /opt/ems/python/.venv/bin/python -m ems_{module} for consistent virtualenv isolation
- safety_manager has commented-out RT scheduling directives so they are discoverable but require explicit enablement when PREEMPT_RT kernel is confirmed on ECU
- gpio-sim check is WARN not FAIL — kernel >= 5.17 required, mock driver available as fallback for safety GPIO simulator
- verify-dev-env.sh uses manual pass/fail tracking (not set -euo pipefail) so all checks run and all failures appear in one pass

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Deployment structure (deploy/systemd/) is established; module implementations in M1+ phases can reference these stubs
- PLAT-01 hardware requirement is fully documented; ECU bring-up can proceed when hardware arrives without blocking simulator work
- verify-dev-env.sh identifies any missing dev dependencies immediately after `make setup`
- Phase 1 is now complete (all 3 plans executed): scaffold (01-01), CI/CD (01-02), and deployment/platform (01-03)

---
*Phase: 01-project-scaffold-build-system*
*Completed: 2026-02-26*

## Self-Check: PASSED

All created files exist on disk. All task commits (8e36d91, fb8e662) confirmed in git log.
