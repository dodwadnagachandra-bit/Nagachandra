# yocto/meta-ems/recipes-core/images/core-image-ems.bb
# EMS production image recipe for Advantech ECU-1170-552A (TI AM6548, 4xA53 + 2xR5).
#
# Extends core-image-minimal with:
#   - Read-only rootfs (IMAGE_FEATURES += read-only-rootfs)
#   - All EMS module packages
#   - Python venv created during image build (ROOTFS_POSTPROCESS_COMMAND)
#   - U-Boot boot.scr added to boot partition (IMAGE_BOOT_FILES)
#
# Filesystem layout (see CONTEXT.md Read-Only Rootfs decision):
#   /             read-only ext4 on eMMC (rootfs_a or rootfs_b via A/B OTA)
#   /tmp          tmpfs 50MB  (declared in base-files bbappend)
#   /var/run      tmpfs 10MB  (declared in base-files bbappend)
#   /run/ems      tmpfs  5MB  (declared in base-files bbappend)
#   /var/log/journal  tmpfs 50MB (declared in base-files bbappend)
#   /data         ext4 rw on SSD nvme0n1p1 (Parquet, JSONL, cloud buffer, config overlay)

DESCRIPTION = "EMS production image for Advantech ECU-1170-552A"
LICENSE = "MIT"

require recipes-core/images/core-image-minimal.bb

# ---------------------------------------------------------------------------
# Image features
# ---------------------------------------------------------------------------

# read-only-rootfs: mounts rootfs read-only at boot; tmpfs overlays declared in fstab.
# ssh-server-openssh: enables SSH for field diagnostics and OTA staging.
IMAGE_FEATURES += "read-only-rootfs ssh-server-openssh"

# ---------------------------------------------------------------------------
# systemd as init manager
# ---------------------------------------------------------------------------

DISTRO_FEATURES:append = " systemd"
VIRTUAL-RUNTIME_init_manager = "systemd"
VIRTUAL-RUNTIME_initscripts = ""

# ---------------------------------------------------------------------------
# IMAGE_INSTALL — all EMS packages
# ---------------------------------------------------------------------------
# NOTE: ems-boot-script (plan 27-03) and ems-certs (plan 27-04) are listed here
# even though their recipes are created in later plans. Bitbake resolves recipe
# dependencies at build time (not at parse time for IMAGE_INSTALL variable lists),
# so this is valid. The image will not build until all referenced recipes exist.

IMAGE_INSTALL:append = " \
    python3 \
    python3-modules \
    python3-venv \
    python3-pip \
    python3-wheel \
    ems-common-c \
    safety-manager \
    comm-manager-c \
    data-manager-c \
    control-manager-c \
    ems-python-venv \
    ems-config \
    ems-systemd-units \
    ems-frontend \
    libubootenv \
    libubootenv-bin \
    ems-boot-script \
    ems-certs \
"

# ---------------------------------------------------------------------------
# Boot files — U-Boot boot script for A/B slot selection
# ---------------------------------------------------------------------------
# boot.scr is compiled from boot.script.in by the ems-boot-script recipe (plan 27-03).
# It is placed on the FAT32 boot partition and loaded by U-Boot to select rootfs_a or rootfs_b.

IMAGE_BOOT_FILES:append = " boot.scr"

# ---------------------------------------------------------------------------
# Python venv — created during image build via ROOTFS_POSTPROCESS_COMMAND
# ---------------------------------------------------------------------------
# The ems-python-venv recipe stages pre-built wheels to /opt/ems/python/wheels/.
# This function creates the actual venv from those wheels, avoiding any runtime
# pip invocation on the read-only rootfs.
#
# Wheel staging deviation: uv_build backend is not supported by python_setuptools_build_meta
# in Yocto Scarthgap, so pre-built wheels are used (see ems-python-venv_0.1.0.bb).

ROOTFS_POSTPROCESS_COMMAND += "create_ems_venv; "

create_ems_venv() {
    local venv_dir="${IMAGE_ROOTFS}/opt/ems/python/.venv"
    local wheels_dir="${IMAGE_ROOTFS}/opt/ems/python/wheels"
    local req_lock="${IMAGE_ROOTFS}/opt/ems/python/requirements.lock"

    # Create venv without pip (pip will be installed from staged wheel)
    python3 -m venv --without-pip "${venv_dir}"

    # Bootstrap pip from the staged wheel set, then install all EMS packages
    if [ -d "${wheels_dir}" ] && [ -f "${req_lock}" ]; then
        "${venv_dir}/bin/python3" -m ensurepip --root "${venv_dir}" || true
        "${venv_dir}/bin/pip3" install \
            --no-index \
            --find-links="${wheels_dir}" \
            -r "${req_lock}" \
            --quiet

        # Remove pip from venv to reduce image size — packages are already installed
        "${venv_dir}/bin/pip3" uninstall --yes pip setuptools wheel 2>/dev/null || true
        rm -rf "${venv_dir}/lib/python3*/site-packages/pip"
        rm -rf "${venv_dir}/lib/python3*/site-packages/setuptools"
        rm -rf "${venv_dir}/lib/python3*/site-packages/wheel"
    else
        bbwarn "EMS: wheels_dir or requirements.lock not found — venv will be empty"
        bbwarn "  wheels_dir: ${wheels_dir}"
        bbwarn "  req_lock: ${req_lock}"
    fi
}
