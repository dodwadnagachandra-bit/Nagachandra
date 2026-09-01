# yocto/meta-ems/recipes-ems/ems-config/ems-config_0.1.0.bb
# EMS configuration files — 14 YAML configs, 16 JSON schemas, 3 deployment profiles
# Installed to /etc/ems/config/ as part of the read-only rootfs
#
# Config files are read-only in the image (rootfs is read-only ext4).
# Hot-reload for the 3 hot-reloadable configs operates via a /data/config/ overlay:
#   - config_manager reads from /data/config/ first (site-specific overrides)
#   - Falls back to /etc/ems/config/ (image defaults)
#   - Overlay populated at first boot if /data/config/ is empty

SUMMARY = "EMS configuration files — YAML configs, JSON schemas, deployment profiles"
DESCRIPTION = "All 14 YAML configuration files, 16 JSON Schema validation files, and \
    3 deployment profiles (residential_50kwh, commercial_500kwh, container_6mwh). \
    Installed to /etc/ems/config/ on the read-only rootfs."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit allarch

# EXTERNALSRC pattern for config directory
FILESEXTRAPATHS:prepend := "${TOPDIR}/../../config:"

# The entire config/ directory tree from the monorepo
SRC_URI = "file://."

S = "${WORKDIR}"

do_install() {
    # Install all YAML configs to /etc/ems/config/
    install -d ${D}${sysconfdir}/ems/config

    # Install YAML configuration files
    find ${WORKDIR} -maxdepth 1 -name "*.yaml" -exec install -m 0644 {} ${D}${sysconfdir}/ems/config/ \;

    # Install JSON Schema validation files
    if [ -d ${WORKDIR}/schemas ]; then
        install -d ${D}${sysconfdir}/ems/config/schemas
        find ${WORKDIR}/schemas -name "*.json" -exec install -m 0644 {} ${D}${sysconfdir}/ems/config/schemas/ \;
    fi

    # Install deployment profiles
    if [ -d ${WORKDIR}/profiles ]; then
        install -d ${D}${sysconfdir}/ems/config/profiles
        find ${WORKDIR}/profiles -name "*.yaml" -exec install -m 0644 {} ${D}${sysconfdir}/ems/config/profiles/ \;
    fi
}

FILES:${PN} = "${sysconfdir}/ems/config"
