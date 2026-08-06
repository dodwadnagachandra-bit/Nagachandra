# Phase 1: Project Scaffold & Build System - Context

**Gathered:** 2026-02-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Monorepo structure, build system (CMake + uv + bun), CI/CD pipeline, cross-compilation toolchain, and dev workstation environment setup. ECU-1170 hardware is not available — all work targets the dev workstation with virtual interfaces (vcan, gpio-sim, socat). Physical ECU bring-up deferred until hardware arrives.

</domain>

<decisions>
## Implementation Decisions

### Monorepo layout
- Flat `src/{module}/` structure — all 12 modules as peers under src/
- Shared code in `src/common/` with c/ and python/ subdirs
- Hybrid modules (comm_manager, data_manager, control_manager, logger) use `c/` and `python/` subdirs within the module directory
- Simulators live in `tools/simulators/` (separate from production src/)
- Config files and JSON Schemas in `config/`
- Integration tests in `tests/`

### Shared code (src/common/)
- C: rtdb.h, seqlock.h, ipc_topics.h, ems_types.h — shared headers as static lib
- Python: ems_common package with rtdb.py (ctypes access), ipc.py (ZMQ helpers), schemas.py (MessagePack schemas)
- Both C and Python common code have their own build files (CMakeLists.txt / pyproject.toml)

### Build system
- CMake: Top-level CMakeLists.txt with add_subdirectory() for each C module. Devs can build all or target a single module
- Python: uv workspace — root pyproject.toml as workspace, each module has its own pyproject.toml. `uv sync` installs all
- HMI: bun for React frontend in src/hmi_server/frontend/
- Cross-compilation: cmake/toolchains/aarch64-linux.cmake toolchain file, triggered via `make build-arm`

### Developer workflow
- Makefile as the primary interface: `make setup`, `make build`, `make test`, `make sim`, `make lint`, `make fmt`
- HMI integrated into Makefile: `make build-hmi`, `make dev-hmi`, `make test-hmi`
- Deploy to ECU: `make flash` via SSH/rsync to ECU_HOST (configurable env var)
- Clone-to-running: git clone → make setup → make build → make test

### ECU install paths
- All EMS files under /opt/ems/ on the ECU-1170
- /opt/ems/bin/ — C binaries
- /opt/ems/python/ — Python packages
- /opt/ems/config/ — 14 YAML config files
- /opt/ems/data/ — Parquet telemetry, DuckDB
- /opt/ems/log/ — JSONL event logs
- /opt/ems/hmi/ — Built React static files
- /opt/ems/run/ — PID files
- /opt/ems/run/ipc/ — ZeroMQ IPC sockets (bms_pub.sock, pcs_pub.sock, safety_pub.sock, etc.)

### CI/CD pipeline
- GitHub Actions, triggered on PR to master (fast checks) and push to master (full suite)
- PR checks (<5 min): cmake build + ctest, uv sync + pytest, clang-format + ruff lint
- Master merge: above + cross-compile ARM64 + simulator integration smoke tests + bun build/test HMI
- Branch protection enabled: CI must pass before merge
- Block on lint + test failures from day 1

### Linting and formatting
- C: clang-format (formatting) with .clang-format in repo root. clang-tidy (static analysis) optional, add later
- Python: ruff for both linting and formatting (replaces flake8+black+isort)
- Makefile targets: `make lint` (check), `make fmt` (auto-format)

### ECU-1170 bring-up strategy
- ECU hardware is NOT available — all Phase 1 work targets Ubuntu 22.04 dev workstation
- Dev workstation setup: vcan0/vcan1 (virtual CAN), gpio-sim kernel module, socat virtual serial ports
- Cross-compile toolchain verified by producing ARM64 binaries (can't run them without ECU)
- ECU bring-up checklist documented for when hardware arrives
- Deferred: physical CAN/RS485/GPIO verification, BSP installation, `make flash` testing

### Systemd services
- Stub .service unit files created in deploy/systemd/ for all 12 modules
- ems.target groups all services for start/stop
- Service files point to /opt/ems/ install paths
- Actual service logic deferred to M1+ when modules have code

### Claude's Discretion
- Exact .clang-format style rules (Allman brace style per global preferences)
- CMake minimum version and feature flags
- GitHub Actions runner OS and caching strategy
- Makefile implementation details (GNU Make features, help target, etc.)
- Python minimum version (3.10+ likely, based on Ubuntu 22.04)

</decisions>

<specifics>
## Specific Ideas

- Developer workflow should feel like: `git clone → make setup → make build → make test → make sim` — five commands to a fully working dev environment
- ECU deploy path is /opt/ems/ — self-contained, clean, Yocto-migration-friendly
- ZeroMQ IPC sockets under /opt/ems/run/ipc/ with predictable names per module
- `make flash` uses rsync for fast incremental deploys during development

</specifics>

<deferred>
## Deferred Ideas

- Physical ECU-1170 bring-up — blocked on hardware availability, documented as checklist
- clang-tidy static analysis — add when C codebase grows beyond safety_manager
- Self-hosted GitHub Actions runner on ECU for hardware-in-the-loop CI
- Docker cross-build container — not needed, CMake toolchain file is simpler

</deferred>

---

*Phase: 01-project-scaffold-build-system*
*Context gathered: 2026-02-26*
