# yocto/meta-ems/recipes-ems/ems-common-c/ems-common-c_0.1.0.bb
# EMS common C library — shared memory RTDB, mpack serialization, ZMQ helpers
# Cross-compiled from monorepo src/common/c/ using cmake bbclass with EXTERNALSRC

SUMMARY = "EMS common C shared library — RTDB, mpack, ZMQ utilities"
DESCRIPTION = "Shared C library used by all EMS C binaries. Provides POSIX shared memory \
    RTDB with seqlock concurrency, mpack MessagePack serialization, and ZMQ socket helpers."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit cmake externalsrc

# Point at monorepo source; replace with git SRC_URI for production CI builds
# Path is relative to the Yocto build/ directory: build/ -> yocto/ -> (monorepo root)
EXTERNALSRC = "${TOPDIR}/../../src/common/c"
EXTERNALSRC_BUILD = "${WORKDIR}/build"

# NOTE: Do NOT set CMAKE_TOOLCHAIN_FILE in EXTRA_OECMAKE.
# The cmake bbclass auto-generates a toolchain.cmake with the correct Yocto sysroot.
# Overriding CMAKE_TOOLCHAIN_FILE would break cross-compilation.
EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"

# ems-common-c has no external dependencies (only POSIX + bundled mpack vendor)
DEPENDS = ""

do_install() {
    # Install shared library to /opt/ems/lib/
    install -d ${D}/opt/ems/lib
    install -m 0755 ${B}/libems_common_c.so ${D}/opt/ems/lib/libems_common_c.so

    # Install headers to /opt/ems/include/ for use by dependent C recipes
    install -d ${D}/opt/ems/include
    install -m 0644 ${S}/include/*.h ${D}/opt/ems/include/
}

FILES:${PN} = "/opt/ems/lib/libems_common_c.so"
FILES:${PN}-dev = "/opt/ems/include/*.h"
