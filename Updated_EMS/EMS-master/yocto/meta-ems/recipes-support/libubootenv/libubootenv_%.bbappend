# yocto/meta-ems/recipes-support/libubootenv/libubootenv_%.bbappend
# Override libubootenv to install /etc/fw_env.config for the Advantech ECU-1170-552A
# This file tells fw_setenv/fw_printenv where to find the U-Boot environment on eMMC
#
# NOTE: The offset 0x3E0000 is the typical value for TI AM65xx-evm U-Boot builds.
# This MUST be verified against the actual CONFIG_ENV_OFFSET in the U-Boot defconfig
# used to flash the ECU-1170. An incorrect offset causes fw_printenv to report
# "Warning: Bad CRC, using default environment" and all ems_active_slot reads will
# return empty/default values, breaking OTA A/B partition management.
# TODO (Phase 29): Confirm CONFIG_ENV_OFFSET with Advantech BSP or U-Boot source for ECU-1170.
#
# Format: device  offset  size  [sectorsize  [nrofcopies]]
# Source: https://github.com/sbabic/libubootenv (fw_env.config documentation)

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += "file://fw_env.config"

do_install:append() {
    # Install fw_env.config to /etc/ so fw_setenv and fw_printenv know the eMMC layout
    install -d ${D}${sysconfdir}
    install -m 0644 ${WORKDIR}/fw_env.config ${D}${sysconfdir}/fw_env.config
}

FILES:${PN} += "${sysconfdir}/fw_env.config"
