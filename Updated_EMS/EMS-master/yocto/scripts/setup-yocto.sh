#!/usr/bin/env bash
# yocto/scripts/setup-yocto.sh
# Initialize Yocto Scarthgap (5.0 LTS) build environment for EMS production image
#
# Usage: source yocto/scripts/setup-yocto.sh [BUILD_DIR]
# Default BUILD_DIR: yocto/build
#
# This script:
#   1. Clones Poky (Scarthgap), meta-ti (Scarthgap), meta-openembedded (Scarthgap)
#   2. Sources oe-init-build-env to initialize BitBake environment
#   3. Adds meta-ems, meta-ti-bsp, meta-oe, meta-python to bblayers.conf
#   4. Sets MACHINE = "am65xx-ems" in local.conf
#
# Prerequisites (Ubuntu 22.04 / 24.04 host):
#   sudo apt-get install -y gawk wget git diffstat unzip texinfo gcc build-essential \
#     chrpath socat cpio python3 python3-pip python3-pexpect xz-utils debianutils \
#     iputils-ping python3-git python3-jinja2 python3-subunit zstd liblz4-tool file \
#     locales libacl1
#
# After setup, build the EMS image with:
#   bitbake core-image-ems

set -euo pipefail

# Determine script and repo root directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
YOCTO_DIR="${REPO_ROOT}/yocto"
BUILD_DIR="${1:-${YOCTO_DIR}/build}"

# Yocto release: Scarthgap 5.0 LTS (supported until April 2028)
YOCTO_BRANCH="scarthgap"

echo "=== EMS Yocto Environment Setup ==="
echo "Repo root:  ${REPO_ROOT}"
echo "Yocto dir:  ${YOCTO_DIR}"
echo "Build dir:  ${BUILD_DIR}"
echo "Branch:     ${YOCTO_BRANCH}"
echo ""

# ── Step 1: Clone upstream Yocto layers ─────────────────────────────────────

clone_or_update() {
    local url="$1"
    local dir="$2"
    local branch="$3"
    if [ -d "${dir}/.git" ]; then
        echo "[skip] ${dir} already exists — run 'git -C ${dir} pull' to update"
    else
        echo "[clone] ${dir}"
        git clone --depth 1 -b "${branch}" "${url}" "${dir}"
    fi
}

# Poky: includes BitBake, OpenEmbedded-Core (meta, meta-poky, meta-yocto-bsp)
clone_or_update \
    "https://git.yoctoproject.org/poky" \
    "${YOCTO_DIR}/poky" \
    "${YOCTO_BRANCH}"

# meta-ti: TI BSP layer — provides AM65xx machine configs and kernel
clone_or_update \
    "https://git.yoctoproject.org/meta-ti" \
    "${YOCTO_DIR}/meta-ti" \
    "${YOCTO_BRANCH}"

# meta-openembedded: extra recipe collections (meta-oe, meta-python, meta-networking)
clone_or_update \
    "https://github.com/openembedded/meta-openembedded.git" \
    "${YOCTO_DIR}/meta-openembedded" \
    "${YOCTO_BRANCH}"

echo ""
echo "=== Initializing BitBake build environment ==="

# ── Step 2: Source oe-init-build-env ────────────────────────────────────────
# This sets up BBPATH, PATH, and creates build/conf/ if it doesn't exist
# Must be sourced (not executed) to export environment variables to calling shell

# shellcheck source=/dev/null
source "${YOCTO_DIR}/poky/oe-init-build-env" "${BUILD_DIR}"

echo ""
echo "=== Configuring bblayers.conf ==="

# ── Step 3: Add layers to bblayers.conf ─────────────────────────────────────
BBLAYERS_CONF="${BUILD_DIR}/conf/bblayers.conf"

add_layer_if_missing() {
    local layer_path="$1"
    if grep -q "${layer_path}" "${BBLAYERS_CONF}" 2>/dev/null; then
        echo "[skip] layer already in bblayers.conf: ${layer_path}"
    else
        echo "[add] ${layer_path}"
        # Insert before the closing ')' of the BBLAYERS variable
        sed -i "s|^\s*\"\?\s*$|  ${layer_path} \\\\\\n  \"|" "${BBLAYERS_CONF}" || true
        # Simpler append approach using bitbake-layers if sed fails
        bitbake-layers add-layer "${layer_path}" 2>/dev/null || true
    fi
}

# Use bitbake-layers for reliable layer management
bitbake-layers add-layer "${YOCTO_DIR}/meta-ti/meta-ti-bsp" 2>/dev/null || \
    echo "[warn] meta-ti-bsp may already be present"
bitbake-layers add-layer "${YOCTO_DIR}/meta-openembedded/meta-oe" 2>/dev/null || \
    echo "[warn] meta-oe may already be present"
bitbake-layers add-layer "${YOCTO_DIR}/meta-openembedded/meta-python" 2>/dev/null || \
    echo "[warn] meta-python may already be present"
bitbake-layers add-layer "${YOCTO_DIR}/meta-ems" 2>/dev/null || \
    echo "[warn] meta-ems may already be present"

echo ""
echo "=== Configuring local.conf ==="

# ── Step 4: Set MACHINE and SSTATE_DIR in local.conf ────────────────────────
LOCAL_CONF="${BUILD_DIR}/conf/local.conf"

set_local_conf() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}" "${LOCAL_CONF}" 2>/dev/null; then
        # Replace existing line
        sed -i "s|^${key}.*|${key} = \"${value}\"|" "${LOCAL_CONF}"
        echo "[update] ${key} = \"${value}\""
    else
        echo "${key} = \"${value}\"" >> "${LOCAL_CONF}"
        echo "[add] ${key} = \"${value}\""
    fi
}

# Target machine: Advantech ECU-1170-552A custom config (extends am65xx-evm)
set_local_conf "MACHINE" "am65xx-ems"

# Shared state cache: store in yocto/sstate-cache for reuse across builds
set_local_conf "SSTATE_DIR" "${YOCTO_DIR}/sstate-cache"

# Download directory: store in yocto/downloads to avoid re-downloading
set_local_conf "DL_DIR" "${YOCTO_DIR}/downloads"

# Parallel build settings (adjust to host CPU count)
NPROC=$(nproc 2>/dev/null || echo 4)
set_local_conf "BB_NUMBER_THREADS" "${NPROC}"
set_local_conf "PARALLEL_MAKE" "-j${NPROC}"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Pre-build Python wheels:  cd ${REPO_ROOT} && uv build --wheel --out-dir yocto/wheels/"
echo "  2. Pre-build React frontend: cd src/hmi_server/frontend && bun install && bun run build"
echo "  3. Build EMS image:          bitbake core-image-ems"
echo ""
echo "Layer status:"
bitbake-layers show-layers 2>/dev/null || echo "(run 'source yocto/scripts/setup-yocto.sh' to initialize)"
