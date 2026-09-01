---
phase: 01-project-scaffold-build-system
plan: 01
subsystem: infra
tags: [cmake, uv, bun, react, vite, tailwind, typescript, python, c, clang-format, ruff, pytest, monorepo]

# Dependency graph
requires: []
provides:
  - Monorepo directory structure: src/{12 modules}/, config/, tests/, docs/, tools/simulators/
  - CMake build system: top-level CMakeLists.txt with 5 C executables compiling cleanly
  - uv Python workspace: 12 member packages all resolvable and importable via --all-packages
  - bun HMI frontend: React+Vite+TypeScript+Tailwind scaffold with dependencies installed
  - Developer Makefile: setup, build, build-arm, test, lint, fmt, sim, flash, clean targets
  - ARM64 toolchain file: cmake/toolchains/aarch64-linux.cmake for ECU-1170 cross-compile
  - C formatting: .clang-format with Allman style enforced
  - Python tooling: ruff lint+format + pytest configured in root pyproject.toml
  - Smoke tests: 4 passing tests verifying module dirs, imports, and version strings
affects:
  - 01-02 (CI/CD pipeline)
  - 01-03 (systemd stubs)
  - All subsequent phases (foundation every module builds on)

# Tech tracking
tech-stack:
  added:
    - CMake 3.22 (C/C++ build system)
    - uv workspace (Python monorepo management)
    - bun 1.3.10 (HMI frontend package manager)
    - React 19, Vite 6, TypeScript 5, Tailwind CSS 4 (HMI frontend scaffold)
    - ruff 0.15 (Python lint + format)
    - pytest 9.0 (Python test runner)
    - clang-format (C code formatter, Allman style)
    - gcc-aarch64-linux-gnu (ARM64 cross-compiler toolchain)
  patterns:
    - uv virtual workspace root: root pyproject.toml without [build-system] + uv sync --all-packages
    - CMake INTERFACE library for shared C headers (ems_common_c)
    - Module CMakeLists.txt: add_executable + target_link_libraries + generator expressions for Debug/Release
    - Hybrid module layout: {module}/c/ for C code, {module}/python/ for Python code
    - Makefile as unified developer interface wrapping cmake + uv + bun

key-files:
  created:
    - CMakeLists.txt
    - pyproject.toml
    - Makefile
    - uv.lock
    - .clang-format
    - .gitignore
    - cmake/toolchains/aarch64-linux.cmake
    - src/common/c/include/ems_types.h
    - src/common/c/CMakeLists.txt
    - src/common/python/src/ems_common/__init__.py
    - src/safety_manager/CMakeLists.txt
    - src/safety_manager/src/main.c
    - tests/test_scaffold.py
  modified:
    - .gitignore (expanded from minimal stub)

key-decisions:
  - "uv sync --all-packages required (not just uv sync) to install all 12 workspace members into .venv"
  - "Root pyproject.toml must omit [build-system] to be a virtual workspace root (uv_build would look for src/ems/__init__.py otherwise)"
  - "CMake version 3.22 used (Ubuntu 22.04 apt default) instead of 3.25 from RESEARCH.md — more portable"
  - "C executables named {module}_c for hybrid modules to avoid collision with Python package names"
  - "bun installed via npm install -g bun (not in PATH by default on dev system)"

patterns-established:
  - "Python-only module: pyproject.toml at src/{module}/ root + src/{module}/src/ems_{module}/__init__.py"
  - "Hybrid module: c/CMakeLists.txt + c/src/main.c + python/pyproject.toml + python/src/ems_{module}/__init__.py"
  - "C-only module: CMakeLists.txt at src/{module}/ root + src/{module}/src/main.c"
  - "All workspace member pyproject.toml files list ems-common as dependency with [tool.uv.sources] workspace = true"

requirements-completed:
  - PLAT-03

# Metrics
duration: 9min
completed: 2026-02-26
---

# Phase 1 Plan 01: Project Scaffold & Build System Summary

**CMake + uv workspace + bun monorepo scaffold for 12-module EMS — 5 C executables compile, all 12 Python packages import, React HMI initializes, 4 smoke tests pass**

## Performance

- **Duration:** 9 min
- **Started:** 2026-02-26T11:14:40Z
- **Completed:** 2026-02-26T11:24:33Z
- **Tasks:** 2 of 2
- **Files modified:** 56

## Accomplishments

- 13 module directories created under src/ (12 modules + common) matching CONTEXT.md spec exactly
- CMake build system compiles 5 C executables (safety_manager, comm_manager_c, data_manager_c, logger_c, control_manager_c) in Debug and Release modes
- uv workspace resolves all 12 Python members; all packages importable and report __version__ == "0.1.0"
- React + Vite + TypeScript + Tailwind CSS HMI scaffold initialized with bun install (84 packages)
- Developer Makefile provides single-interface workflow: setup, build, build-arm, test, lint, fmt, sim, flash, clean

## Task Commits

Each task was committed atomically:

1. **Task 1: Create root configuration files and directory skeleton** - `7efc5be` (chore)
2. **Task 2: Scaffold all 12 module directories with stub source files** - `21b4bc8` (feat)

**Plan metadata:** (pending — created in final commit)

## Files Created/Modified

- `/home/overlord/Dev/Revx_Energy/EMS/.gitignore` - Build artifacts, Python, JS/HMI, IDE, OS ignore rules
- `/home/overlord/Dev/Revx_Energy/EMS/.clang-format` - Allman brace style, IndentWidth=4, ColumnLimit=100
- `/home/overlord/Dev/Revx_Energy/EMS/CMakeLists.txt` - Top-level CMake with add_subdirectory for 6 C modules
- `/home/overlord/Dev/Revx_Energy/EMS/pyproject.toml` - uv virtual workspace root, 12 members, ruff+pytest config
- `/home/overlord/Dev/Revx_Energy/EMS/uv.lock` - Committed lockfile with 20 resolved packages
- `/home/overlord/Dev/Revx_Energy/EMS/Makefile` - Developer interface (13 targets)
- `/home/overlord/Dev/Revx_Energy/EMS/cmake/toolchains/aarch64-linux.cmake` - ARM64 cross-compile toolchain
- `/home/overlord/Dev/Revx_Energy/EMS/src/common/c/include/ems_types.h` - EMS_VERSION macro, stub for future types
- `/home/overlord/Dev/Revx_Energy/EMS/src/safety_manager/src/main.c` - C stub: prints version, exits 0
- `/home/overlord/Dev/Revx_Energy/EMS/src/hmi_server/frontend/vite.config.ts` - React+Tailwind Vite config
- `/home/overlord/Dev/Revx_Energy/EMS/tests/test_scaffold.py` - 4 smoke tests: dirs, imports, versions

## Decisions Made

- **uv sync --all-packages**: Default `uv sync` only installs root package deps; all 12 workspace members require `--all-packages` flag. Updated Makefile accordingly.
- **Virtual workspace root**: Root pyproject.toml omits `[build-system]` so uv treats it as a virtual root (no installable package at repo root). With uv_build it would fail looking for `src/ems/__init__.py`.
- **CMake 3.22**: Used Ubuntu 22.04 apt default (3.22) instead of RESEARCH.md suggestion of 3.25 — more portable, sufficient for all patterns used.
- **C executable naming**: `comm_manager_c`, `data_manager_c`, `logger_c`, `control_manager_c` to avoid name collision with Python packages.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed uv workspace root build-backend causing install failure**
- **Found during:** Task 2 verification (`uv sync --dev`)
- **Issue:** Root pyproject.toml had `[build-system] build-backend = "uv_build"`. uv_build tried to find `src/ems/__init__.py` as the installable package, failing with "Expected a Python module at: src/ems/__init__.py".
- **Fix:** Removed `[build-system]` section from root pyproject.toml to make it a virtual workspace root.
- **Files modified:** `pyproject.toml`
- **Verification:** `uv sync --dev` runs cleanly; "Resolved 20 packages" without build errors.
- **Committed in:** `21b4bc8` (Task 2 commit)

**2. [Rule 2 - Missing Critical] Added --all-packages flag to Makefile uv sync**
- **Found during:** Task 2 verification (import testing)
- **Issue:** `uv sync --dev` installed only root dev dependencies (pytest, ruff). Workspace member packages (ems-common, ems-alarm-manager, etc.) were resolved but not installed as editable packages, making imports fail.
- **Fix:** Changed `uv sync --dev` to `uv sync --dev --all-packages` in Makefile setup target.
- **Files modified:** `Makefile`
- **Verification:** All 12 `import ems_*` statements succeed; `uv pip list` shows 18 packages installed.
- **Committed in:** `21b4bc8` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 bug, 1 missing critical)
**Impact on plan:** Both fixes required for the plan's must-have truth "uv sync succeeds and installs all 12 Python workspace members into .venv". No scope creep.

## Issues Encountered

- bun not in PATH on dev system (not installed globally). Installed via `npm install -g bun` using existing nvm node. Works fine; CI workflow will use `oven-sh/setup-bun@v2` action.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Scaffold complete; all 12 module directories exist and build cleanly
- CMake, uv workspace, and bun frontend ready for Phase 01-02 (CI/CD pipeline setup)
- ARM64 toolchain file in place; cross-compile can be verified in CI without ECU hardware
- Blocker: ECU-1170-552A hardware not available (documented in STATE.md); simulator phases unblocked

## Self-Check: PASSED

- FOUND: .gitignore
- FOUND: .clang-format
- FOUND: CMakeLists.txt
- FOUND: pyproject.toml
- FOUND: Makefile
- FOUND: cmake/toolchains/aarch64-linux.cmake
- FOUND: uv.lock
- FOUND: src/common/c/include/ems_types.h
- FOUND: src/safety_manager/src/main.c
- FOUND: tests/test_scaffold.py
- FOUND: .planning/phases/01-project-scaffold-build-system/01-01-SUMMARY.md
- COMMIT 7efc5be: EXISTS (chore(01-01): create root configuration files and directory skeleton)
- COMMIT 21b4bc8: EXISTS (feat(01-01): scaffold all 12 module directories with stub source files)
- MODULE DIRS: 13 (12 modules + common) — correct

---
*Phase: 01-project-scaffold-build-system*
*Completed: 2026-02-26*
