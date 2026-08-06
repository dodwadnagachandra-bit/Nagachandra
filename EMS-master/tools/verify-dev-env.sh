#!/usr/bin/env bash
# tools/verify-dev-env.sh — EMS Dev Environment Verification
#
# Checks that all required tools and virtual interfaces are available on the
# developer workstation for EMS simulator development. Run after `make setup`
# to confirm everything is correctly installed.
#
# Usage: ./tools/verify-dev-env.sh
# Exit:  0 if all required checks pass, 1 if any required check fails

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# ANSI colours (only when stdout is a terminal)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[0;33m'
    NC='\033[0m'
else
    GREEN=''
    RED=''
    YELLOW=''
    NC=''
fi

pass() {
    printf "${GREEN}[PASS]${NC} %s\n" "$1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    printf "${RED}[FAIL]${NC} %s\n" "$1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
    printf "${YELLOW}[WARN]${NC} %s\n" "$1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

echo "============================================"
echo " EMS Dev Environment Verification"
echo "============================================"
echo ""

# ------------------------------------------------------------------ Build tools
echo "--- Build Tools ---"

# 1. cmake (>= 3.22 required for CMake presets / target-based generators)
if cmake --version >/dev/null 2>&1; then
    CMAKE_VER=$(cmake --version | head -1 | awk '{print $3}')
    CMAKE_MAJOR=$(echo "$CMAKE_VER" | cut -d. -f1)
    CMAKE_MINOR=$(echo "$CMAKE_VER" | cut -d. -f2)
    if [ "$CMAKE_MAJOR" -gt 3 ] || { [ "$CMAKE_MAJOR" -eq 3 ] && [ "$CMAKE_MINOR" -ge 22 ]; }; then
        pass "cmake $CMAKE_VER (>= 3.22)"
    else
        fail "cmake $CMAKE_VER found but >= 3.22 required"
    fi
else
    fail "cmake not found — install: sudo apt-get install cmake"
fi

# 2. make
if make --version >/dev/null 2>&1; then
    MAKE_VER=$(make --version | head -1)
    pass "make ($MAKE_VER)"
else
    fail "make not found — install: sudo apt-get install make"
fi

# 3. gcc (native, for unit tests on dev workstation)
if gcc --version >/dev/null 2>&1; then
    GCC_VER=$(gcc --version | head -1)
    pass "gcc ($GCC_VER)"
else
    fail "gcc not found — install: sudo apt-get install gcc"
fi

# 4. aarch64-linux-gnu-gcc (cross-compiler for ECU-1170 ARM64)
if aarch64-linux-gnu-gcc --version >/dev/null 2>&1; then
    CROSS_VER=$(aarch64-linux-gnu-gcc --version | head -1)
    pass "aarch64-linux-gnu-gcc ($CROSS_VER)"
else
    fail "aarch64-linux-gnu-gcc not found — install: sudo apt-get install gcc-aarch64-linux-gnu"
fi

echo ""

# ------------------------------------------------------------------ Python toolchain
echo "--- Python Toolchain ---"

# 5. uv (Python package manager)
if uv --version >/dev/null 2>&1; then
    UV_VER=$(uv --version)
    pass "uv ($UV_VER)"
else
    fail "uv not found — install: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# 6. Python 3.12+ (via uv managed environment)
if uv run python --version >/dev/null 2>&1; then
    PY_VER=$(uv run python --version 2>&1 | awk '{print $2}')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
        pass "Python $PY_VER (>= 3.12)"
    else
        fail "Python $PY_VER found but >= 3.12 required — run: uv python install 3.12"
    fi
else
    fail "Python (via uv) not available — run: uv python install 3.12"
fi

echo ""

# ------------------------------------------------------------------ Frontend toolchain
echo "--- Frontend Toolchain ---"

# 7. bun (JavaScript/TypeScript runtime and package manager for HMI)
if bun --version >/dev/null 2>&1; then
    BUN_VER=$(bun --version)
    pass "bun $BUN_VER"
else
    fail "bun not found — install: curl -fsSL https://bun.sh/install | bash"
fi

echo ""

# ------------------------------------------------------------------ Code quality
echo "--- Code Quality ---"

# 8. clang-format (C/C++ formatting)
if clang-format --version >/dev/null 2>&1; then
    CF_VER=$(clang-format --version)
    pass "clang-format ($CF_VER)"
else
    fail "clang-format not found — install: sudo apt-get install clang-format"
fi

# 9. ruff (Python linter/formatter, installed in uv venv)
if uv run ruff --version >/dev/null 2>&1; then
    RUFF_VER=$(uv run ruff --version)
    pass "ruff ($RUFF_VER)"
else
    fail "ruff not found — run: uv sync --dev"
fi

echo ""

# ------------------------------------------------------------------ C libraries
echo "--- C Libraries ---"

# libzmq (required by safety_manager and comm_manager_c)
if pkg-config --modversion libzmq >/dev/null 2>&1; then
    ZMQ_VER=$(pkg-config --modversion libzmq)
    pass "libzmq $ZMQ_VER"
else
    fail "libzmq-dev not found — install: sudo apt-get install libzmq3-dev"
fi

# libgpiod (optional — RTDB backend used when absent)
if pkg-config --modversion libgpiod >/dev/null 2>&1; then
    GPIOD_VER=$(pkg-config --modversion libgpiod)
    pass "libgpiod $GPIOD_VER"
else
    warn "libgpiod-dev not found — safety_manager will use RTDB test backend only"
fi

echo ""

# ------------------------------------------------------------------ CAN / serial interfaces
echo "--- Virtual Interfaces ---"

# 10. can-utils (cansend, candump, etc. for CAN simulator work)
if which cansend >/dev/null 2>&1 || cansend --help >/dev/null 2>&1; then
    pass "can-utils (cansend found)"
else
    fail "can-utils not found — install: sudo apt-get install can-utils"
fi

# 11. socat (virtual serial ports for Modbus simulator)
if which socat >/dev/null 2>&1; then
    SOCAT_VER=$(socat -V 2>&1 | head -1)
    pass "socat ($SOCAT_VER)"
else
    fail "socat not found — install: sudo apt-get install socat"
fi

# 12. vcan kernel module (virtual CAN bus for BMS simulator)
if modprobe -n vcan >/dev/null 2>&1; then
    pass "vcan kernel module available"
else
    fail "vcan kernel module not available — may need PREEMPT_RT kernel or module install"
fi

# 13. gpio-sim kernel module (virtual GPIO for safety GPIO simulator)
#     Note: gpio-sim was added in kernel 5.17 and may not be present on all dev machines.
#     This is a WARNING, not a FAIL — safety GPIO tests can use a mock driver instead.
if modprobe -n gpio-sim >/dev/null 2>&1; then
    pass "gpio-sim kernel module available"
else
    warn "gpio-sim kernel module not available (kernel >= 5.17 required) — GPIO simulator will use mock driver"
fi

echo ""

# ------------------------------------------------------------------ Summary
echo "============================================"
echo " Summary"
echo "============================================"
echo ""
printf " Passed:   %d\n" "$PASS_COUNT"
printf " Failed:   %d\n" "$FAIL_COUNT"
printf " Warnings: %d\n" "$WARN_COUNT"
echo ""

if [ "$FAIL_COUNT" -eq 0 ]; then
    printf "${GREEN}All required checks passed. Dev environment is ready.${NC}\n"
    exit 0
else
    printf "${RED}${FAIL_COUNT} check(s) failed. Run 'make setup' to install missing dependencies.${NC}\n"
    exit 1
fi
