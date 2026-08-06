# yocto/meta-ems/recipes-ems/control-manager-c/control-manager-c_0.1.0.bb
# EMS control manager C component — 1Hz state machine, charge/discharge setpoint writer
# Cross-compiled from monorepo src/control_manager/c/ using cmake bbclass with EXTERNALSRC
# Layer 4 (L4) — application control logic

SUMMARY = "EMS control manager C component — 1Hz BESS state machine and PCS setpoint writer"
DESCRIPTION = "Layer 4 control process (C component). Implements the 1Hz energy management \
    state machine (IDLE, CHARGING, DISCHARGING, FAULT, EMERGENCY_STOP). Reads RTDB for \
    current state and writes charge/discharge setpoints to PCS via comm_manager_c."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit cmake externalsrc

EXTERNALSRC = "${TOPDIR}/../../src/control_manager/c"
EXTERNALSRC_BUILD = "${WORKDIR}/build"

# NOTE: Do NOT set CMAKE_TOOLCHAIN_FILE in EXTRA_OECMAKE.
# The cmake bbclass auto-generates a toolchain.cmake with the correct Yocto sysroot.
EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"

# libzmq: ZeroMQ for IPC with data_manager (REQ/REP commands) and PUB state updates
# ems-common-c: shared RTDB read access and mpack serialization
DEPENDS = "libzmq ems-common-c"

do_install() {
    install -d ${D}/opt/ems/bin
    install -m 0755 ${B}/control_manager_c ${D}/opt/ems/bin/control_manager_c
}

FILES:${PN} = "/opt/ems/bin/control_manager_c"
