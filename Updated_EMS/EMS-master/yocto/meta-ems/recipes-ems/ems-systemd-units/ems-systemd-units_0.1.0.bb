# yocto/meta-ems/recipes-ems/ems-systemd-units/ems-systemd-units_0.1.0.bb
# EMS systemd service units — all 14 service files + ems.target
# Installs and enables all EMS services on the production image

SUMMARY = "EMS systemd service units — all 14 service files and ems.target"
DESCRIPTION = "All 14 EMS systemd service files plus ems.target. Services are installed \
    to /usr/lib/systemd/system/ and enabled at boot. Startup order is defined by \
    After=/Requires= dependencies in each service file, anchored to ems.target."
LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

inherit systemd allarch

# Point at monorepo deploy/systemd/ directory
FILESEXTRAPATHS:prepend := "${TOPDIR}/../../deploy/systemd:"

SRC_URI = "\
    file://alarm_manager.service \
    file://cloud_manager.service \
    file://comm_manager_c.service \
    file://comm_manager.service \
    file://config_manager.service \
    file://control_manager.service \
    file://data_manager.service \
    file://diagnostics.service \
    file://ems-config-manager.service \
    file://ems-data-manager-python.service \
    file://ems-data-manager.service \
    file://ems.target \
    file://hmi_server.service \
    file://logger.service \
    file://ota_manager.service \
    file://safety_manager.service \
    file://scheduler.service \
"

S = "${WORKDIR}"

# List all service units for systemd bbclass to enable at image build time
SYSTEMD_SERVICE:${PN} = "\
    alarm_manager.service \
    cloud_manager.service \
    comm_manager_c.service \
    comm_manager.service \
    config_manager.service \
    control_manager.service \
    data_manager.service \
    diagnostics.service \
    ems-config-manager.service \
    ems-data-manager-python.service \
    ems-data-manager.service \
    hmi_server.service \
    logger.service \
    ota_manager.service \
    safety_manager.service \
    scheduler.service \
"

# Enable all EMS services automatically at first boot
SYSTEMD_AUTO_ENABLE = "enable"

do_install() {
    install -d ${D}${systemd_system_unitdir}

    # Install all .service files
    for svc in ${WORKDIR}/*.service; do
        install -m 0644 "${svc}" ${D}${systemd_system_unitdir}/
    done

    # Install ems.target
    install -m 0644 ${WORKDIR}/ems.target ${D}${systemd_system_unitdir}/ems.target
}

FILES:${PN} = "${systemd_system_unitdir}"
