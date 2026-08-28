# Phase 1: Project Scaffold & Build System — Research

**Researched:** 2026-02-26
**Domain:** Monorepo scaffolding, CMake build system, uv Python workspaces, bun HMI frontend, GitHub Actions CI/CD, ARM64 cross-compilation toolchain, dev workstation environment setup
**Confidence:** HIGH (CMake, uv, GitHub Actions verified against official docs and Context7; cross-compilation pattern well-established)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Monorepo layout**
- Flat `src/{module}/` structure — all 12 modules as peers under src/
- Shared code in `src/common/` with c/ and python/ subdirs
- Hybrid modules (comm_manager, data_manager, control_manager, logger) use `c/` and `python/` subdirs within the module directory
- Simulators live in `tools/simulators/` (separate from production src/)
- Config files and JSON Schemas in `config/`
- Integration tests in `tests/`

**Shared code (src/common/)**
- C: rtdb.h, seqlock.h, ipc_topics.h, ems_types.h — shared headers as static lib
- Python: ems_common package with rtdb.py (ctypes access), ipc.py (ZMQ helpers), schemas.py (MessagePack schemas)
- Both C and Python common code have their own build files (CMakeLists.txt / pyproject.toml)

**Build system**
- CMake: Top-level CMakeLists.txt with add_subdirectory() for each C module. Devs can build all or target a single module
- Python: uv workspace — root pyproject.toml as workspace, each module has its own pyproject.toml. `uv sync` installs all
- HMI: bun for React frontend in src/hmi_server/frontend/
- Cross-compilation: cmake/toolchains/aarch64-linux.cmake toolchain file, triggered via `make build-arm`

**Developer workflow**
- Makefile as the primary interface: `make setup`, `make build`, `make test`, `make sim`, `make lint`, `make fmt`
- HMI integrated into Makefile: `make build-hmi`, `make dev-hmi`, `make test-hmi`
- Deploy to ECU: `make flash` via SSH/rsync to ECU_HOST (configurable env var)
- Clone-to-running: git clone → make setup → make build → make test

**ECU install paths**
- All EMS files under /opt/ems/ on the ECU-1170
- /opt/ems/bin/ — C binaries
- /opt/ems/python/ — Python packages
- /opt/ems/config/ — 14 YAML config files
- /opt/ems/data/ — Parquet telemetry, DuckDB
- /opt/ems/log/ — JSONL event logs
- /opt/ems/hmi/ — Built React static files
- /opt/ems/run/ — PID files
- /opt/ems/run/ipc/ — ZeroMQ IPC sockets

**CI/CD pipeline**
- GitHub Actions, triggered on PR to master (fast checks) and push to master (full suite)
- PR checks (<5 min): cmake build + ctest, uv sync + pytest, clang-format + ruff lint
- Master merge: above + cross-compile ARM64 + simulator integration smoke tests + bun build/test HMI
- Branch protection enabled: CI must pass before merge
- Block on lint + test failures from day 1

**Linting and formatting**
- C: clang-format (formatting) with .clang-format in repo root. clang-tidy (static analysis) optional, add later
- Python: ruff for both linting and formatting (replaces flake8+black+isort)
- Makefile targets: `make lint` (check), `make fmt` (auto-format)

**ECU-1170 bring-up strategy**
- ECU hardware is NOT available — all Phase 1 work targets Ubuntu 22.04 dev workstation
- Dev workstation setup: vcan0/vcan1 (virtual CAN), gpio-sim kernel module, socat virtual serial ports
- Cross-compile toolchain verified by producing ARM64 binaries (can't run them without ECU)
- ECU bring-up checklist documented for when hardware arrives
- Deferred: physical CAN/RS485/GPIO verification, BSP installation, `make flash` testing

**Systemd services**
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

### Deferred Ideas (OUT OF SCOPE)
- Physical ECU-1170 bring-up — blocked on hardware availability, documented as checklist
- clang-tidy static analysis — add when C codebase grows beyond safety_manager
- Self-hosted GitHub Actions runner on ECU for hardware-in-the-loop CI
- Docker cross-build container — not needed, CMake toolchain file is simpler
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PLAT-01 | Ubuntu 22.04 LTS boots on ECU-1170-552A with SocketCAN, GPIO (libgpiod), and RS485 drivers verified | ECU not available; deferred to hardware checklist. Dev workstation virtual interfaces (vcan, gpio-sim, socat) are the Phase 1 deliverable. |
| PLAT-02 | CI/CD pipeline builds C (CMake) and Python (uv) targets, runs unit tests (ctest + pytest) | GitHub Actions + astral-sh/setup-uv@v7 + cmake build + ctest. PR workflow documented. Caching via setup-uv enable-cache. |
| PLAT-03 | Monorepo scaffold with src/{module}/, config/, tests/, docs/ structure | Directory structure with 12 module dirs, src/common/, tools/simulators/, config/, tests/, docs/, deploy/systemd/ all locked in CONTEXT.md. |
| PLAT-07 | Cross-compilation toolchain configured for ARM64 (A53) target | cmake/toolchains/aarch64-linux.cmake with aarch64-linux-gnu-gcc. apt package: gcc-aarch64-linux-gnu. `make build-arm` target. |
</phase_requirements>

---

## Summary

Phase 1 establishes the complete developer and CI foundation before any module code is written. All four locked requirements (PLAT-01–03, PLAT-07) are achievable on Ubuntu 22.04 without the ECU hardware. PLAT-01 is partially deferred: virtual interfaces (vcan, gpio-sim, socat) can be verified on the dev workstation; physical driver verification waits for hardware arrival and is captured in a bring-up checklist.

The build system is a three-tier hybrid: CMake for all C code (with a top-level CMakeLists.txt that add_subdirectory()s each C module), uv workspaces for all Python code (one root pyproject.toml, one per module), and bun for the React HMI frontend. The developer-facing interface is a GNU Makefile that wraps all three. This is the right design — it gives new developers a single familiar entry point (`make setup && make build && make test`) while preserving the full power of each underlying tool.

Cross-compilation uses the standard `gcc-aarch64-linux-gnu` toolchain on Ubuntu (available via apt, present on GitHub Actions ubuntu-22.04 runners) with a CMake toolchain file at `cmake/toolchains/aarch64-linux.cmake`. Since the ECU is unavailable, verification means successfully producing ARM64 ELF binaries — execution testing is deferred. The GitHub Actions CI is split into a fast PR check job (<5 min) and a full master-merge job, with branch protection preventing merges on failures.

**Primary recommendation:** Build the Makefile first (as the developer contract), then the CMake structure, then uv workspace, then CI — in that order. The Makefile is the integration point that must work before anything else matters.

---

## Standard Stack

### Core

| Tool | Version | Purpose | Why Standard |
|------|---------|---------|--------------|
| CMake | 3.25+ | C/C++ build system generator | Industry standard for embedded/cross-platform C; supports toolchain files natively |
| uv | latest (0.5+) | Python package manager + workspace | Replaces pip/poetry/venv; monorepo workspaces built-in; 10-100x faster |
| bun | latest (1.x) | HMI JS runtime + package manager | Project standard; faster than npm/yarn; locked in CLAUDE.md |
| GNU Make | 4.3 (Ubuntu 22.04 default) | Developer interface wrapper | Familiar, universal, no additional install |
| GitHub Actions | N/A | CI/CD pipeline | Repo already on GitHub; free tier sufficient for M0 |
| astral-sh/setup-uv | v7 | GitHub Actions uv installer | Official action; handles caching via `enable-cache: true` |
| oven-sh/setup-bun | v2 | GitHub Actions bun installer | Official action for bun in CI |
| gcc-aarch64-linux-gnu | Ubuntu 22.04 package | ARM64 cross-compiler | Standard Debian/Ubuntu cross-toolchain; available in apt and on GHA runners |
| clang-format | 14+ (Ubuntu 22.04 default) | C code formatter | Configured via .clang-format; deterministic, CI-enforceable |
| ruff | latest (0.9+) | Python lint + format | Replaces flake8+black+isort; 10-100x faster; single config in pyproject.toml |

### Supporting

| Tool | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| ctest | bundled with CMake | C unit test runner | Run after `cmake --build`; reports pass/fail |
| pytest | 8.x | Python unit test runner | `uv run pytest tests/` |
| FetchContent / find_package | CMake built-in | Dependency acquisition | Use FetchContent for test-only deps (e.g., Unity/CMock); find_package for system libs |
| modprobe vcan / can_raw | Linux kernel module | Virtual CAN interfaces for dev | `make setup` step on dev workstation |
| socat | Ubuntu package | Virtual serial ports for Modbus dev | `make sim` target |
| gpio-sim | Linux kernel module | Virtual GPIO for safety_manager dev | `make setup` step; verify with libgpiod |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| GNU Make wrapper | CMake presets | CMake presets are more portable but less familiar to embedded devs; Makefile is simpler for the 5-command clone-to-run workflow |
| gcc-aarch64-linux-gnu | Clang cross-compile | Clang supports `--target=aarch64-linux-gnu` but requires more toolchain wiring; gcc-aarch64-linux-gnu is simpler one-apt-install |
| ruff | flake8 + black + isort | Three separate tools vs one; ruff is strictly better — faster, single config, same rules |
| ctest | catch2 / googletest directly | ctest is the runner, not the framework; use Unity or cmocka as the C test framework inside ctest |

---

## Architecture Patterns

### Recommended Repository Structure

```
EMS/                               # repo root
├── CMakeLists.txt                 # top-level CMake: cmake_minimum_required, project(), add_subdirectory()
├── Makefile                       # developer interface: setup, build, test, lint, fmt, sim, flash
├── pyproject.toml                 # uv workspace root: [tool.uv.workspace] members = ["src/**/python", "src/common/python"]
├── uv.lock                        # shared Python lockfile (committed)
├── .clang-format                  # C formatting rules (Allman style)
├── .gitignore                     # build/, dist/, .venv/, __pycache__, *.o, *.elf, etc.
├── cmake/
│   └── toolchains/
│       └── aarch64-linux.cmake   # ARM64 cross-compilation toolchain file
├── src/
│   ├── common/
│   │   ├── c/                    # shared C headers: rtdb.h, seqlock.h, ipc_topics.h, ems_types.h
│   │   │   └── CMakeLists.txt    # ems_common_c INTERFACE/STATIC library
│   │   └── python/               # ems_common Python package: rtdb.py, ipc.py, schemas.py
│   │       └── pyproject.toml
│   ├── safety_manager/           # L1 Safety — C only
│   │   └── CMakeLists.txt
│   ├── comm_manager/             # L2 Comms — hybrid C + Python
│   │   ├── c/
│   │   │   └── CMakeLists.txt
│   │   └── python/
│   │       └── pyproject.toml
│   ├── data_manager/             # L3 Data — hybrid C + Python
│   │   ├── c/
│   │   │   └── CMakeLists.txt
│   │   └── python/
│   │       └── pyproject.toml
│   ├── logger/                   # L3 Logging — hybrid C++ + Python
│   │   ├── c/
│   │   │   └── CMakeLists.txt
│   │   └── python/
│   │       └── pyproject.toml
│   ├── config_manager/           # L3 Config — Python only
│   │   └── pyproject.toml
│   ├── control_manager/          # L4 App — hybrid Python/C
│   │   ├── c/
│   │   │   └── CMakeLists.txt
│   │   └── python/
│   │       └── pyproject.toml
│   ├── alarm_manager/            # L4 App — Python only
│   │   └── pyproject.toml
│   ├── scheduler/                # L4 App — Python only
│   │   └── pyproject.toml
│   ├── diagnostics/              # L4 App — Python only
│   │   └── pyproject.toml
│   ├── cloud_manager/            # L5 Cloud — Python only
│   │   └── pyproject.toml
│   ├── ota_manager/              # L5 Cloud — Python only
│   │   └── pyproject.toml
│   └── hmi_server/               # L5 Cloud — Python backend + React frontend
│       ├── pyproject.toml
│       └── frontend/             # React + Vite + Tailwind (bun)
│           ├── package.json
│           ├── vite.config.ts
│           └── src/
├── config/                        # 14 YAML config files + JSON Schemas (stubs in Phase 1)
├── tests/                         # integration tests (empty in Phase 1)
├── docs/                          # documentation
├── tools/
│   └── simulators/               # CAN/Modbus/GPIO simulators (Phase 5-7)
├── deploy/
│   └── systemd/                  # stub .service files for all 12 modules + ems.target
└── .github/
    └── workflows/
        ├── pr-check.yml          # fast PR checks (<5 min)
        └── master-merge.yml      # full suite + ARM64 cross-compile
```

**Note on Python workspace member discovery:** The uv workspace root `pyproject.toml` uses glob patterns. Given the hybrid module layout, the members list should enumerate explicitly rather than using a catch-all glob, since Python pyproject.toml files are nested under `python/` subdirs for hybrid modules:

```toml
[tool.uv.workspace]
members = [
  "src/common/python",
  "src/config_manager",
  "src/alarm_manager",
  "src/scheduler",
  "src/diagnostics",
  "src/cloud_manager",
  "src/ota_manager",
  "src/hmi_server",
  "src/comm_manager/python",
  "src/data_manager/python",
  "src/logger/python",
  "src/control_manager/python",
]
```

### Pattern 1: CMake Toolchain File for ARM64

**What:** A standalone `.cmake` file that sets system/compiler variables; passed to CMake via `-DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/aarch64-linux.cmake`
**When to use:** Any time you invoke CMake targeting the ECU (ARM64)

```cmake
# cmake/toolchains/aarch64-linux.cmake
# Source: https://cmake.org/cmake/help/latest/manual/cmake-toolchains.7.html

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Standard Debian/Ubuntu aarch64 cross-compiler (apt install gcc-aarch64-linux-gnu)
set(CMAKE_C_COMPILER /usr/bin/aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER /usr/bin/aarch64-linux-gnu-g++)

# Sysroot: set when a cross-sysroot is available; omit for bare cross-compile
# set(CMAKE_SYSROOT /path/to/aarch64-sysroot)

# Prevent CMake from finding host libraries for the target
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
```

**Makefile target:**
```makefile
BUILD_DIR_ARM = build-arm

build-arm: ## Cross-compile for ARM64 (aarch64-linux-gnu)
	cmake -B $(BUILD_DIR_ARM) \
	      -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/aarch64-linux.cmake \
	      -DCMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_DIR_ARM) -- -j$(nproc)
```

### Pattern 2: Top-Level CMakeLists.txt with add_subdirectory

**What:** Single entry point; each C module has its own CMakeLists.txt. Common library is linked by modules that need it.

```cmake
# CMakeLists.txt (repo root)
cmake_minimum_required(VERSION 3.25)
project(EMS C CXX)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Enable testing at top level (required for ctest to discover tests)
include(CTest)

# Shared C library (headers + common types)
add_subdirectory(src/common/c)

# L1 Safety
add_subdirectory(src/safety_manager)

# L2 Comms (C portion)
add_subdirectory(src/comm_manager/c)

# L3 Data (C portion)
add_subdirectory(src/data_manager/c)
add_subdirectory(src/logger/c)

# L4 Control (C portion)
add_subdirectory(src/control_manager/c)
```

### Pattern 3: uv Workspace Root pyproject.toml

```toml
# pyproject.toml (repo root) — workspace root only, no installable package
[project]
name = "ems"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[tool.uv.workspace]
members = [
  "src/common/python",
  "src/config_manager",
  "src/alarm_manager",
  "src/scheduler",
  "src/diagnostics",
  "src/cloud_manager",
  "src/ota_manager",
  "src/hmi_server",
  "src/comm_manager/python",
  "src/data_manager/python",
  "src/logger/python",
  "src/control_manager/python",
]

[dependency-groups]
dev = [
  "pytest>=8.0",
  "ruff>=0.9",
]

[build-system]
requires = ["uv_build>=0.10,<0.11"]
build-backend = "uv_build"
```

**Note:** As of uv 0.5+, the root pyproject.toml must include `[project]` metadata even if the workspace root itself is not an installable package. The root is always treated as a workspace member. `uv sync` at the root installs all members' dependencies into a single `.venv`. (Source: https://docs.astral.sh/uv/concepts/projects/workspaces/ — verified Feb 2026)

### Pattern 4: GitHub Actions PR Check Workflow

```yaml
# .github/workflows/pr-check.yml
name: PR Check

on:
  pull_request:
    branches: [master]

jobs:
  build-and-test:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4

      - name: Install system deps
        run: sudo apt-get install -y cmake ninja-build clang-format

      - name: Configure CMake (native)
        run: cmake -B build -DCMAKE_BUILD_TYPE=Debug

      - name: Build C targets
        run: cmake --build build -- -j$(nproc)

      - name: Run C tests
        run: ctest --test-dir build --output-on-failure

      - name: Install uv
        uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true

      - name: Install Python deps
        run: uv sync --locked --all-extras --dev

      - name: Run Python tests
        run: uv run pytest tests/ -v

      - name: Check C formatting (clang-format)
        run: |
          find src -name "*.c" -o -name "*.h" | \
          xargs clang-format --dry-run --Werror

      - name: Check Python lint + format (ruff)
        run: |
          uv run ruff check src/
          uv run ruff format --check src/
```

### Pattern 5: .clang-format (Allman Style)

```yaml
# .clang-format
---
BasedOnStyle: LLVM
BreakBeforeBraces: Allman
IndentWidth: 4
ColumnLimit: 100
AlignConsecutiveAssignments: true
AlignConsecutiveDeclarations: true
SortIncludes: true
```

**Note:** `BreakBeforeBraces: Allman` is the canonical setting for Allman-style (opening brace on its own line). This matches the global CLAUDE.md preference. (Source: https://clang.llvm.org/docs/ClangFormatStyleOptions.html — verified Feb 2026)

### Pattern 6: ruff Configuration in Root pyproject.toml

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

exclude = [
  ".git", "__pycache__", ".venv",
  "build", "dist", "node_modules",
]

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "B"]
ignore = ["E501"]   # line length handled by formatter

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

### Pattern 7: Makefile (Developer Interface)

```makefile
# Makefile — Developer interface for EMS monorepo
# Usage: make <target>

.PHONY: help setup build build-arm test lint fmt sim flash \
        build-hmi dev-hmi test-hmi clean

BUILD_DIR  := build
BUILD_ARM  := build-arm
ECU_HOST   ?= ems@192.168.1.100

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Install all dependencies (run once after clone)
	@echo "--- Installing system deps ---"
	sudo apt-get install -y cmake ninja-build gcc-aarch64-linux-gnu \
	  clang-format libgpiod-dev can-utils socat
	@echo "--- Setting up virtual CAN ---"
	sudo modprobe vcan can can_raw
	sudo ip link add dev vcan0 type vcan || true
	sudo ip link set up vcan0
	@echo "--- Installing Python deps ---"
	uv sync --locked --all-extras --dev
	@echo "--- Installing HMI deps ---"
	cd src/hmi_server/frontend && bun install

build: ## Build all C targets (native)
	cmake -B $(BUILD_DIR) -DCMAKE_BUILD_TYPE=Debug
	cmake --build $(BUILD_DIR) -- -j$(nproc)

build-arm: ## Cross-compile for ARM64 (ECU-1170)
	cmake -B $(BUILD_ARM) \
	  -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/aarch64-linux.cmake \
	  -DCMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_ARM) -- -j$(nproc)

test: ## Run all unit tests (ctest + pytest)
	ctest --test-dir $(BUILD_DIR) --output-on-failure
	uv run pytest tests/ -v

lint: ## Check C formatting and Python lint (no changes)
	find src -name "*.c" -o -name "*.h" | xargs clang-format --dry-run --Werror
	uv run ruff check src/
	uv run ruff format --check src/

fmt: ## Auto-format C and Python code
	find src -name "*.c" -o -name "*.h" | xargs clang-format -i
	uv run ruff format src/
	uv run ruff check --fix src/

sim: ## Start simulators (vcan + socat virtual serial)
	@echo "Starting virtual interfaces..."
	sudo modprobe vcan can can_raw || true
	sudo ip link add dev vcan0 type vcan 2>/dev/null || true
	sudo ip link set up vcan0

build-hmi: ## Build HMI React frontend (production)
	cd src/hmi_server/frontend && bun run build

dev-hmi: ## Start HMI dev server
	cd src/hmi_server/frontend && bun run dev

test-hmi: ## Run HMI unit tests
	cd src/hmi_server/frontend && bun test

flash: ## Deploy to ECU via rsync (requires ECU_HOST env var)
	@echo "Deploying to $(ECU_HOST)..."
	rsync -avz --delete $(BUILD_ARM)/bin/ $(ECU_HOST):/opt/ems/bin/
	rsync -avz config/ $(ECU_HOST):/opt/ems/config/

clean: ## Remove build artifacts
	rm -rf $(BUILD_DIR) $(BUILD_ARM)
```

### Pattern 8: Stub systemd Service File

```ini
# deploy/systemd/safety_manager.service
[Unit]
Description=EMS Safety Manager
Documentation=https://github.com/ReVx-Energy/EMS
After=network.target
PartOf=ems.target

[Service]
Type=simple
ExecStart=/opt/ems/bin/safety_manager
Restart=on-failure
RestartSec=5
User=ems
Group=ems
WorkingDirectory=/opt/ems
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=ems.target
```

```ini
# deploy/systemd/ems.target
[Unit]
Description=EMS — Energy Management System
Documentation=https://github.com/ReVx-Energy/EMS

[Install]
WantedBy=multi-user.target
```

### Anti-Patterns to Avoid

- **Single CMakeLists.txt for all modules:** Prevents per-module builds (`cmake --build build --target safety_manager`). Use add_subdirectory() so each module is independently buildable.
- **Python packages installed globally:** Always use `uv sync` into `.venv`; never `pip install` system-wide during development.
- **Root uv workspace without `[project]` metadata:** uv requires the root to be a valid package (has `[project]` section). A workspace root without it will fail `uv sync`.
- **clang-format without `--Werror` in CI:** Formatting checks that don't fail the build are not enforced. Always use `--dry-run --Werror`.
- **Wildcard `git add .` including build artifacts:** The `.gitignore` must cover `build/`, `build-arm/`, `.venv/`, `node_modules/`, `__pycache__/`, `*.o`, `*.elf` before the first commit.
- **vcan interface without `modprobe vcan` first:** `ip link add dev vcan0 type vcan` silently fails if the vcan kernel module is not loaded.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| ARM64 cross-compilation | Custom shell scripts that call gcc-aarch64 directly | CMake toolchain file | CMake handles include paths, link flags, sysroot automatically; scripts break with any project structure change |
| Python dependency isolation | pip + venv manually | `uv sync` (workspace-aware) | uv handles transitive deps, lockfile, and per-workspace member installation atomically |
| C formatting enforcement | Custom regex or manual review | clang-format with `--Werror` in CI | clang-format handles all edge cases (macros, attributes, packed structs); manual review misses things |
| Python lint + format | Separate flake8 + black + isort | ruff (single tool) | ruff replaces all three with one config section and 10-100x faster execution |
| Developer onboarding script | Standalone bash script | Makefile `setup` target | Makefile is the documented interface; keeps setup discoverable via `make help` |
| Per-module virtual environments | One `.venv` per Python module | uv workspace shared `.venv` | Shared lockfile prevents dependency conflicts between modules; reduces disk usage |

**Key insight:** For cross-compilation, the CMake toolchain file pattern is the industry standard specifically because it handles the three-way distinction between host tools, target compilers, and find paths. Any custom script approach collapses this distinction and causes find_package() failures.

---

## Common Pitfalls

### Pitfall 1: vcan Module Not Loaded Before IP Link Commands

**What goes wrong:** `ip link add dev vcan0 type vcan` returns `RTNETLINK answers: Operation not supported` even though `iproute2` is installed correctly.
**Why it happens:** The vcan kernel module (`vcan.ko`) is not loaded. The kernel has the driver compiled as a module, not built-in.
**How to avoid:** `sudo modprobe vcan` before any `ip link` commands. `make setup` must load modules first.
**Warning signs:** `RTNETLINK: Operation not supported` on `ip link add type vcan`.

### Pitfall 2: uv Workspace Root Missing `[project]` Section

**What goes wrong:** `uv sync` fails with a parse error or treats the root as non-installable and skips member resolution.
**Why it happens:** uv workspaces require the root `pyproject.toml` to have a `[project]` table. The root is always a workspace member.
**How to avoid:** Always include a minimal `[project]` section in the workspace root `pyproject.toml`, even if the root itself has no dependencies.
**Warning signs:** `uv sync` errors mentioning "missing project metadata" or "workspace root not a package".

### Pitfall 3: CMake FetchContent in Cross-Compilation Context

**What goes wrong:** FetchContent downloads and builds a dependency for the host, not the target. Tests link against host-architecture libraries, causing link errors or exec format errors.
**Why it happens:** FetchContent uses the current CMake toolchain, which is the cross-compiler when the toolchain file is active.
**How to avoid:** For Phase 1, avoid FetchContent for test dependencies in the cross-compilation build. Use a separate native build for tests (`build/`) and only use the ARM build (`build-arm/`) for binary verification. Alternatively, structure CMakeLists to guard test targets with `if(NOT CMAKE_CROSSCOMPILING)`.
**Warning signs:** Cross-compilation build downloading test frameworks and failing to link.

### Pitfall 4: CMake `enable_testing()` Only in Top-Level

**What goes wrong:** `ctest --test-dir build` finds no tests even though `add_test()` is called in subdirectories.
**Why it happens:** `enable_testing()` (or `include(CTest)`) must be called in the top-level CMakeLists.txt, not just in subdirectory files.
**How to avoid:** Call `include(CTest)` in the root CMakeLists.txt before any `add_subdirectory()` calls.
**Warning signs:** `ctest` reports "No tests were found".

### Pitfall 5: ruff Check vs ruff Format as Separate Steps

**What goes wrong:** `ruff check` (linting) passes but import ordering or formatting is wrong; CI does not catch it.
**Why it happens:** ruff has two separate commands: `ruff check` (lint rules) and `ruff format` (formatting/style). Both must be run in CI.
**How to avoid:** In CI and `make lint`, always run both: `uv run ruff check src/` AND `uv run ruff format --check src/`.
**Warning signs:** PRs with inconsistently formatted Python code that pass CI.

### Pitfall 6: GitHub Actions `ubuntu-latest` vs `ubuntu-22.04`

**What goes wrong:** `ubuntu-latest` changes over time (currently 24.04); packages available or their versions differ between runs, breaking reproducible builds.
**Why it happens:** `ubuntu-latest` is a rolling alias.
**How to avoid:** Pin to `ubuntu-22.04` explicitly in all workflow files. This also ensures `clang-format` version consistency (Ubuntu 22.04 ships clang-format-14).
**Warning signs:** Build failures that appear after a GitHub Actions runner update.

### Pitfall 7: Python Minimum Version on Ubuntu 22.04

**What goes wrong:** `python3 --version` on Ubuntu 22.04 returns 3.10, but pyproject.toml specifies `requires-python = ">=3.12"`, causing uv to fail.
**Why it happens:** Ubuntu 22.04 ships Python 3.10.x as the system default. Python 3.12 is available via deadsnakes PPA or uv's managed Python feature.
**How to avoid:** Use `uv python install 3.12` in `make setup` to install 3.12 via uv's managed Python, or add `astral-sh/setup-uv@v7` with `python-version: "3.12"` in CI. `requires-python = ">=3.12"` is correct; uv will resolve it automatically if a managed Python is available.
**Warning signs:** `uv sync` errors: "No Python 3.12 found".

---

## Code Examples

### GitHub Actions Master-Merge Workflow (Full Suite)

```yaml
# .github/workflows/master-merge.yml
name: Master Merge (Full Suite)

on:
  push:
    branches: [master]

jobs:
  full-suite:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4

      - name: Install system deps (native + cross)
        run: |
          sudo apt-get update
          sudo apt-get install -y cmake ninja-build clang-format \
            gcc-aarch64-linux-gnu g++-aarch64-linux-gnu

      - name: Configure and build (native)
        run: |
          cmake -B build -DCMAKE_BUILD_TYPE=Debug
          cmake --build build -- -j$(nproc)

      - name: Run C tests (native)
        run: ctest --test-dir build --output-on-failure

      - name: Cross-compile for ARM64
        run: |
          cmake -B build-arm \
            -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/aarch64-linux.cmake \
            -DCMAKE_BUILD_TYPE=Release
          cmake --build build-arm -- -j$(nproc)

      - name: Verify ARM64 binary architecture
        run: |
          find build-arm -name "*.elf" -o -name "safety_manager" | \
          xargs -I{} file {} | grep -q "aarch64" && echo "ARM64 OK"

      - name: Install uv
        uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
          python-version: "3.12"

      - name: Install Python deps
        run: uv sync --locked --all-extras --dev

      - name: Run Python tests
        run: uv run pytest tests/ -v

      - name: Lint + format check
        run: |
          find src -name "*.c" -o -name "*.h" | \
            xargs clang-format --dry-run --Werror
          uv run ruff check src/
          uv run ruff format --check src/

      - name: Setup bun
        uses: oven-sh/setup-bun@v2

      - name: Install HMI deps
        run: cd src/hmi_server/frontend && bun install

      - name: Build HMI
        run: cd src/hmi_server/frontend && bun run build

      - name: Test HMI
        run: cd src/hmi_server/frontend && bun test
```

### Cross-Compilation Verification (shell)

```bash
# Verify the ARM64 toolchain is functional after setup
aarch64-linux-gnu-gcc --version           # Should print aarch64-linux-gnu-gcc 11.x
aarch64-linux-gnu-gcc -o /tmp/hello_arm hello.c
file /tmp/hello_arm
# → /tmp/hello_arm: ELF 64-bit LSB pie executable, ARM aarch64, ...
```

### Individual Module CMakeLists.txt (stub)

```cmake
# src/safety_manager/CMakeLists.txt
cmake_minimum_required(VERSION 3.25)

add_executable(safety_manager
    src/main.c
)

target_link_libraries(safety_manager
    PRIVATE
        ems_common_c   # shared headers from src/common/c
)

target_compile_options(safety_manager
    PRIVATE
        -Wall -Wextra -Werror
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
        $<$<CONFIG:Release>:-O2 -DNDEBUG>
)

# Unit tests (only for native builds)
if(NOT CMAKE_CROSSCOMPILING)
    add_subdirectory(tests)
endif()
```

### Individual Module pyproject.toml (stub)

```toml
# src/config_manager/pyproject.toml
[project]
name = "ems-config-manager"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pyyaml>=6.0",
    "jsonschema>=4.0",
    "ems-common",           # workspace member dependency
]

[tool.uv.sources]
ems-common = { workspace = true }

[build-system]
requires = ["uv_build>=0.10,<0.11"]
build-backend = "uv_build"
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pip + venv manually | uv workspaces (`uv sync`) | 2023-2024 | Single lockfile for all modules; 10-100x faster installs |
| flake8 + black + isort separately | ruff (all-in-one) | 2023 | One config, one command, same rules, 150x faster |
| Autotools / bare Makefiles for C | CMake with toolchain files | 2015+ (embedded) | Portable, IDE-friendly, native cross-compile support |
| `ubuntu-latest` in CI | Pin to `ubuntu-22.04` | N/A (best practice) | Reproducible builds, consistent tool versions |
| CMake 3.10 minimum | CMake 3.25+ minimum | 2022+ | Access to generator expressions, FetchContent improvements, version ranges |

**Deprecated/outdated:**
- `pip install -r requirements.txt`: Replaced by `uv sync --locked` for all new Python projects
- `setup.py` / `setup.cfg`: Replaced by `pyproject.toml` + `uv_build` or `hatchling`
- Raw Makefiles for C cross-compilation: CMake toolchain files handle sysroot, find paths, and generator expressions that raw Makefiles cannot

---

## Validation Architecture

*(nyquist_validation: true — this section is required)*

### Test Framework

| Property | Value |
|----------|-------|
| C framework | ctest (runner) + Unity or custom (Phase 1 stubs — framework choice for Phase 1 is stub/placeholder; real framework selected in M1) |
| Python framework | pytest 8.x, configured via `pyproject.toml` |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) — does not exist yet (Wave 0 gap) |
| Quick run command (Python) | `uv run pytest tests/ -v` |
| Full suite command | `make test` (runs both ctest + pytest) |
| C quick run | `ctest --test-dir build --output-on-failure` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| PLAT-01 | Ubuntu 22.04 dev workstation has vcan, gpio-sim, socat available | smoke (manual) | `make setup && modprobe vcan && ip link show vcan0` | ❌ Wave 0 — script in `tools/verify-dev-env.sh` |
| PLAT-02 | CI pipeline builds C (CMake) and Python (uv), runs unit tests | integration | `.github/workflows/pr-check.yml` triggers on PR | ❌ Wave 0 — workflow files |
| PLAT-03 | Monorepo directory structure: src/{module}/, config/, tests/, docs/ all present | smoke | `ls src/{safety_manager,comm_manager,...}` | ❌ Wave 0 — created by scaffold task |
| PLAT-07 | Cross-compilation produces ARM64 ELF binaries | smoke | `file build-arm/bin/safety_manager \| grep aarch64` | ❌ Wave 0 — build-arm target in Makefile |

**Note:** PLAT-01, PLAT-03, and PLAT-07 are structure/environment verification, not pure unit tests. The automated commands are smoke checks that can be run in CI (except the modprobe vcan step which requires a real Linux kernel and cannot run on standard GitHub Actions runners — it is a manual dev workstation check). PLAT-02 is self-validating: the CI workflow itself is the test.

### Sampling Rate

- **Per task commit:** `make build` (C compile) + `uv run pytest tests/ -v` (Python tests — empty in Phase 1 but framework must be present)
- **Per wave merge:** `make test` (full ctest + pytest)
- **Phase gate:** `make build-arm` produces ARM64 ELF + `make lint` clean + GitHub Actions PR check green

### Wave 0 Gaps

All of these must be created in Wave 0 of the plan (before implementation tasks):

- [ ] `pyproject.toml` (root workspace) — `[tool.pytest.ini_options]` section, ruff config
- [ ] `.github/workflows/pr-check.yml` — fast PR check workflow
- [ ] `.github/workflows/master-merge.yml` — full suite workflow
- [ ] `cmake/toolchains/aarch64-linux.cmake` — ARM64 toolchain file
- [ ] `Makefile` — developer interface (all targets)
- [ ] `.clang-format` — C formatting rules
- [ ] `tests/` — empty directory with `__init__.py` and stub `conftest.py`
- [ ] `tools/verify-dev-env.sh` — smoke test: vcan, gpio-sim, socat availability
- [ ] Framework install: `uv add --dev pytest ruff` — if not already in root pyproject.toml

---

## Open Questions

1. **Python version pinning: 3.12 vs 3.10**
   - What we know: Ubuntu 22.04 ships Python 3.10.12 by default. uv can manage Python 3.12 independently via `uv python install 3.12`.
   - What's unclear: Should `requires-python = ">=3.12"` be enforced from day 1, or start with `>=3.10` and upgrade later?
   - Recommendation: Use `>=3.12` with `uv python install 3.12` in `make setup`. Python 3.12 is the current stable release and has better performance and error messages. Starting at 3.12 avoids a painful mid-project upgrade.

2. **CMake test framework for C: Unity vs cmocka vs hand-rolled**
   - What we know: Phase 1 creates stub CMakeLists.txt; actual unit tests arrive in M1 (safety_manager, comm_manager). The framework must be chosen before writing the first C test.
   - What's unclear: Unity (single-header, embedded-friendly) vs cmocka (POSIX, mock support) vs GoogleTest (more complex, C++ API).
   - Recommendation (LOW confidence — needs team input): Unity for safety_manager and bare C modules (no external deps, single .h/.c, used in production embedded C); cmocka if mocking of hardware interfaces (GPIO, CAN) is needed. Defer final decision to the M1 safety_manager task.

3. **vcan on GitHub Actions ubuntu-22.04 runner**
   - What we know: GitHub Actions ubuntu-22.04 runners are virtual machines with limited kernel module support. `modprobe vcan` may fail.
   - What's unclear: Whether vcan is available on GHA ubuntu-22.04 runners by default.
   - Recommendation: Test this explicitly in the first CI PR. If vcan is unavailable, the simulator smoke tests in the master-merge workflow must be guarded (`if: runner.os == 'Linux' && ...`), and the simulator integration tests may need a self-hosted runner in later phases. For Phase 1, the ARM64 cross-compile verification does not depend on vcan.

4. **uv workspace root: virtual root vs installable package**
   - What we know: As of uv 0.5, the workspace root must have `[project]` metadata. uv supports "virtual" workspace roots only in specific configurations.
   - What's unclear: Whether `uv_build` is required or if `flit_core` / `hatchling` is acceptable for the workspace root build backend.
   - Recommendation: Use `uv_build` as the build backend for the root and all members — it is uv's native backend and the path of least resistance. (Source: https://docs.astral.sh/uv/concepts/projects/workspaces — HIGH confidence)

---

## Sources

### Primary (HIGH confidence)

- `/websites/cmake_cmake_help` (Context7) — cross-compilation toolchain file pattern, CTest integration, FetchContent
- `/websites/astral_sh_uv` (Context7) — workspace configuration, uv sync, GitHub Actions integration
- `/websites/astral_sh_ruff` (Context7) — pyproject.toml configuration, lint rules, format options
- https://cmake.org/cmake/help/latest/manual/cmake-toolchains.7.html — ARM Linux toolchain file structure
- https://docs.astral.sh/uv/concepts/projects/workspaces/ — workspace root requirements, member discovery
- https://docs.astral.sh/uv/guides/integration/github/ — GitHub Actions setup-uv@v7 workflow
- https://github.com/astral-sh/setup-uv — setup-uv@v7, enable-cache parameter
- https://clang.llvm.org/docs/ClangFormatStyleOptions.html — BreakBeforeBraces: Allman

### Secondary (MEDIUM confidence)

- https://discourse.cmake.org/t/cross-compile-for-aarch64-on-ubuntu/2161 — aarch64-linux-gnu-gcc on Ubuntu, confirmed by apt package existence
- https://www.pragmaticlinux.com/2021/10/how-to-create-a-virtual-can-interface-on-linux/ — vcan setup steps (modprobe + ip link), consistent with kernel docs
- https://jensd.be/1126/linux/cross-compiling-for-arm-or-aarch64-on-debian-or-ubuntu — gcc-aarch64-linux-gnu apt package name on Ubuntu

### Tertiary (LOW confidence — flagged for validation)

- GitHub Actions vcan availability on ubuntu-22.04 runners: not confirmed via official GHA docs. **Must be validated** in first CI task.
- CMake minimum version 3.25 vs 3.28: w3tutorials.net article references discourse.cmake.org; not verified against current cmake_minimum_required docs for what 3.25 specifically enables. **Recommendation: test with 3.22** (Ubuntu 22.04 default apt cmake version) and use VERSION 3.22 as minimum if 3.25+ is not available on the GHA runner without manual install.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all tools verified via Context7 official docs (CMake, uv, ruff) and official GitHub repos (setup-uv, setup-bun)
- Architecture: HIGH — directory structure is locked in CONTEXT.md; patterns are standard CMake/uv idioms verified against official docs
- Pitfalls: MEDIUM-HIGH — vcan, Python version, and uv workspace root requirements verified against official docs; CMake cross-compile pitfalls verified against cmake.org discourse
- GitHub Actions workflows: MEDIUM — structure verified against official astral-sh/setup-uv docs and uv GitHub Actions guide; exact runner capabilities (vcan) flagged LOW

**Research date:** 2026-02-26
**Valid until:** 2026-03-28 (uv and ruff are fast-moving; re-verify versions; CMake toolchain patterns are stable for 1+ year)
