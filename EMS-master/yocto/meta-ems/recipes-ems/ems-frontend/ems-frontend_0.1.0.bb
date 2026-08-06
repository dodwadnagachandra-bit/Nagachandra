# yocto/meta-ems/recipes-ems/ems-frontend/ems-frontend_0.1.0.bb
# EMS HMI frontend — pre-built React dist/ served by hmi_server
# No bun or Node.js dependency in Yocto — frontend is built on dev machine before image build
#
# Pre-build procedure (dev machine):
#   cd src/hmi_server/frontend
#   bun install
#   bun run build
# Output: src/hmi_server/frontend/dist/ (gitignored static assets)

SUMMARY = "EMS HMI frontend — pre-built React/Vite static assets"
DESCRIPTION = "Pre-built React + Vite + Chart.js + Tailwind CSS frontend for the EMS HMI \
    server. Built with bun on the dev machine before image creation — no Node.js or bun \
    required in the Yocto image. Served by hmi_server (FastAPI) from /opt/ems/frontend/dist/."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit allarch

# Point at monorepo frontend dist/ directory (pre-built before bitbake invocation)
FILESEXTRAPATHS:prepend := "${TOPDIR}/../../src/hmi_server/frontend:"

# Stage the entire pre-built dist/ directory
SRC_URI = "file://dist"

S = "${WORKDIR}"

do_install() {
    # Install pre-built React dist/ to /opt/ems/frontend/dist/
    install -d ${D}/opt/ems/frontend
    cp -r ${WORKDIR}/dist ${D}/opt/ems/frontend/dist
    # Ensure all files are readable by the ems service user
    find ${D}/opt/ems/frontend -type f -exec chmod 0644 {} \;
    find ${D}/opt/ems/frontend -type d -exec chmod 0755 {} \;
}

FILES:${PN} = "/opt/ems/frontend/dist"
