SUMMARY = "EMS A/B boot selection script for U-Boot"
DESCRIPTION = "Compiles boot.cmd to boot.scr for A/B partition selection \
and automatic rollback on the Advantech ECU-1170-552A (TI AM654x). \
The script reads ems_active_slot and ems_boot_count from U-Boot env, \
selects the correct rootfs partition, and reverts to the fallback slot \
if boot_count exceeds 2."
LICENSE = "CLOSED"

# Host tool required for mkimage
DEPENDS = "u-boot-mkimage-native"

# Source: boot.cmd in the same recipe directory (files/ subdirectory via SRC_URI)
SRC_URI = "file://boot.cmd"

S = "${WORKDIR}"

inherit deploy

# Compile boot.cmd to boot.scr using mkimage.
# -C none      : no compression
# -A arm64     : ARM64 architecture
# -T script    : U-Boot script image type
# -d boot.cmd  : input source file
do_compile() {
    mkimage -C none -A arm64 -T script -d ${WORKDIR}/boot.cmd ${WORKDIR}/boot.scr
}

# Install boot.scr into the root filesystem at /boot/boot.scr
# so it is available when IMAGE_INSTALL includes this recipe.
do_install() {
    install -d ${D}/boot
    install -m 0644 ${WORKDIR}/boot.scr ${D}/boot/boot.scr
}

# Deploy boot.scr to DEPLOYDIR so it can be copied to the
# boot partition during image creation.
# IMAGE_BOOT_FILES:append = " boot.scr" in core-image-ems.bb picks this up.
do_deploy() {
    install -d ${DEPLOYDIR}
    install -m 0644 ${WORKDIR}/boot.scr ${DEPLOYDIR}/boot.scr
}

addtask deploy after do_compile before do_build

FILES:${PN} += "/boot/boot.scr"
