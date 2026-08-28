# yocto/meta-ems/recipes-ems/safety-manager/safety-manager_0.1.0.bb
# EMS safety manager — PREEMPT_RT GPIO watchdog, dual-channel E-Stop, fire/flood detection
# Cross-compiled from monorepo src/safety_manager/ using cmake bbclass with EXTERNALSRC
# Layer 1 (L1) — highest priority safety process, runs SCHED_FIFO

SUMMARY = "EMS safety manager — PREEMPT_RT GPIO watchdog and emergency stop controller"
DESCRIPTION = "Layer 1 safety process. Monitors 8 digital inputs (E-Stop dual-channel, \
    fire, flood) and controls 8 digital outputs (PCS stop, ACDB trip, siren) within 100ms. \
    Runs with SCHED_FIFO priority on PREEMPT_RT kernel."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit cmake externalsrc

# safety_manager CMakeLists.txt is at the module root (not in a c/ subdirectory)
EXTERNALSRC = "${TOPDIR}/../../src/safety_manager"
EXTERNALSRC_BUILD = "${WORKDIR}/build"

# NOTE: Do NOT set CMAKE_TOOLCHAIN_FILE in EXTRA_OECMAKE.
# The cmake bbclass auto-generates a toolchain.cmake with the correct Yocto sysroot.
EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"

# libzmq: ZeroMQ for IPC with data_manager
# libgpiod: Linux GPIO character device interface (replaces sysfs GPIO)
# ems-common-c: shared RTDB and mpack serialization
DEPENDS = "libzmq libgpiod ems-common-c"

do_install() {
    install -d ${D}/opt/ems/bin
    install -m 0755 ${B}/safety_manager ${D}/opt/ems/bin/safety_manager
}

FILES:${PN} = "/opt/ems/bin/safety_manager"
