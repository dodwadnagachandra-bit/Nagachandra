# yocto/meta-ems/recipes-ems/data-manager-c/data-manager-c_0.1.0.bb
# EMS data manager C component — POSIX shared memory RTDB writer, ZMQ PUB hub
# Cross-compiled from monorepo src/data_manager/c/ using cmake bbclass with EXTERNALSRC
# Layer 3 (L3) — data aggregation and distribution

SUMMARY = "EMS data manager C component — POSIX shared memory RTDB and ZMQ publish hub"
DESCRIPTION = "Layer 3 data process (C component). Receives telemetry from all hardware \
    drivers via ZeroMQ PUSH/PULL, writes to POSIX shared memory RTDB (seqlock concurrency), \
    and publishes 1Hz snapshots to Python subscribers via ZMQ PUB socket."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit cmake externalsrc

EXTERNALSRC = "${TOPDIR}/../../src/data_manager/c"
EXTERNALSRC_BUILD = "${WORKDIR}/build"

# NOTE: Do NOT set CMAKE_TOOLCHAIN_FILE in EXTRA_OECMAKE.
# The cmake bbclass auto-generates a toolchain.cmake with the correct Yocto sysroot.
EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"

# libzmq: ZeroMQ for telemetry aggregation (PUSH/PULL) and distribution (PUB)
# ems-common-c: shared RTDB structures and seqlock primitives
DEPENDS = "libzmq ems-common-c"

do_install() {
    install -d ${D}/opt/ems/bin
    install -m 0755 ${B}/data_manager_c ${D}/opt/ems/bin/data_manager_c
}

FILES:${PN} = "/opt/ems/bin/data_manager_c"
