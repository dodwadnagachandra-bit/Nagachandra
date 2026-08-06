# yocto/meta-ems/recipes-ems/ems-python-venv/ems-python-venv_0.1.0.bb
# EMS Python wheel staging recipe — stages pre-built wheels for all 12 Python modules
#
# Deviation from CONTEXT.md python3-setuptools decision:
# CONTEXT.md specified individual recipes per Python module using the python3-setuptools
# class. However, per RESEARCH.md findings, python_setuptools_build_meta does NOT support
# the uv_build backend used by this project's Python packages in Yocto Scarthgap.
# The uv_build backend is incompatible with Yocto's setuptools_build_meta bbclass because
# Yocto does not have 'uv' available in its build environment.
#
# Solution: Wheels are pre-built offline on the dev machine using 'uv build' for each
# module, then staged here as static files. The venv is created during image construction
# via ROOTFS_POSTPROCESS_COMMAND in core-image-ems.bb using:
#   python3 -m venv --without-pip /opt/ems/python/.venv
#   pip install --no-index --find-links=/opt/ems/python/wheels -r requirements.lock
#
# This approach is equivalent to the locked decision in CONTEXT.md (Python venv created
# during image build) — only the packaging mechanism differs (staged wheels vs setuptools).
#
# Pre-build procedure (dev machine):
#   cd /path/to/EMS
#   uv build --wheel --out-dir yocto/wheels/
#   # For each Python workspace member (12 modules)

SUMMARY = "EMS Python modules — pre-built wheels for venv installation at image build time"
DESCRIPTION = "Stages pre-built Python wheels for all 12 EMS Python modules and a pinned \
    requirements.lock file. The actual venv is created by ROOTFS_POSTPROCESS_COMMAND in \
    core-image-ems.bb using pip --no-index --find-links against these wheels."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

# FILES class: pure file staging, no compilation
inherit allarch

# Wheels directory — populated by CI build pipeline before image build
# Run: uv build --wheel for each Python module in the workspace
# Output: yocto/wheels/*.whl (gitignored — generated artifacts)
# The wheels/ directory must exist relative to the meta-ems layer at build time.
# In CI, this is populated by the build script before invoking bitbake.
FILESEXTRAPATHS:prepend := "${TOPDIR}/../../yocto/wheels:"
SRC_URI = "file://requirements.lock"

# Individual wheel files are added by the build pipeline to the wheels/ directory.
# BitBake resolves them via FILESEXTRAPATHS pointing at yocto/wheels/.
# During development, add specific wheels here as needed:
# SRC_URI += "file://ems_common-0.1.0-py3-none-any.whl"
# SRC_URI += "file://data_manager-0.1.0-py3-none-any.whl"
# ... (all 12 modules)

S = "${WORKDIR}"

do_install() {
    # Stage wheels to /opt/ems/python/wheels/ for use by ROOTFS_POSTPROCESS_COMMAND
    install -d ${D}/opt/ems/python/wheels
    # Copy any .whl files from workdir (populated from SRC_URI fetches)
    find ${WORKDIR} -maxdepth 1 -name "*.whl" -exec install -m 0644 {} ${D}/opt/ems/python/wheels/ \;

    # Stage the pinned requirements lockfile
    install -d ${D}/opt/ems/python
    install -m 0644 ${WORKDIR}/requirements.lock ${D}/opt/ems/python/requirements.lock
}

FILES:${PN} = "/opt/ems/python/wheels /opt/ems/python/requirements.lock"
