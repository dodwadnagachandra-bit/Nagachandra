# yocto/meta-ems/recipes-ems/comm-manager-c/comm-manager-c_0.1.0.bb
# EMS comm manager C component — CAN bus DBC parser, Modbus RTU driver
# Cross-compiled from monorepo src/comm_manager/c/ using cmake bbclass with EXTERNALSRC
# Layer 2 (L2) — hardware communication layer

SUMMARY = "EMS comm manager C component — CAN bus DBC parser and Modbus RTU driver"
DESCRIPTION = "Layer 2 communication process (C component). Handles CAN 2.0B/FD from BMS \
    (IDs 0x98FF0003–0x98FF0903) using DBC decoding, and Modbus RTU to PCS (9600/8N1, V1.24 \
    register map). Publishes decoded telemetry to data_manager via ZeroMQ."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit cmake externalsrc

EXTERNALSRC = "${TOPDIR}/../../src/comm_manager/c"
EXTERNALSRC_BUILD = "${WORKDIR}/build"

# NOTE: Do NOT set CMAKE_TOOLCHAIN_FILE in EXTRA_OECMAKE.
# The cmake bbclass auto-generates a toolchain.cmake with the correct Yocto sysroot.
EXTRA_OECMAKE = "-DCMAKE_BUILD_TYPE=Release"

# libzmq: ZeroMQ IPC to data_manager
# libmodbus: Modbus RTU protocol (PCS, BTMS, meter, DG, solar PV)
# ems-common-c: shared RTDB and mpack serialization
DEPENDS = "libzmq libmodbus ems-common-c"

do_install() {
    install -d ${D}/opt/ems/bin
    install -m 0755 ${B}/comm_manager_c ${D}/opt/ems/bin/comm_manager_c
}

FILES:${PN} = "/opt/ems/bin/comm_manager_c"
