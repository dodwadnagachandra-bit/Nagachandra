# yocto/meta-ems/recipes-core/base-files/base-files_%.bbappend
# Appends EMS-specific fstab entries to the Yocto base-files /etc/fstab.
#
# Mount layout per CONTEXT.md Read-Only Rootfs decision:
#
#   /tmp              tmpfs 50MB  mode=1777  — temporary files (sticky bit for world-writable)
#   /var/run          tmpfs 10MB  mode=0755  — PID files, systemd runtime state
#   /run/ems          tmpfs  5MB  mode=0755  — ZMQ IPC sockets (ipc:///run/ems/*.sock)
#   /var/log/journal  tmpfs 50MB  mode=0755  — systemd journal (volatile; EMS logs to SSD JSONL)
#   /data             ext4  rw    noatime    — SSD NVMe data partition (Parquet, JSONL, buffer)
#
# The /run/ems directory is created at runtime by systemd-tmpfiles using
# deploy/tmpfiles/ems.conf (installed to /usr/lib/tmpfiles.d/ems.conf by ems-config recipe).
#
# The SSD device /dev/nvme0n1p1 is partition 1 of the NVMe SSD on the ECU-1170-552A.
# It is separate from the eMMC A/B rootfs partitions (mmcblk0p2/p3) and survives OTA updates.
#
# noatime on /data prevents SSD write amplification from access time metadata updates.
# 0 2 at end of /data entry: dump=0 (not included in dump), fsck=2 (non-root fsck order).

do_install:append() {
    cat >> ${D}${sysconfdir}/fstab << 'EMS_FSTAB_ENTRIES'

# EMS tmpfs overlays — required for read-only rootfs operation
# Format: <device> <mount> <type> <options> <dump> <fsck>
tmpfs    /tmp                tmpfs  defaults,size=50m,mode=1777       0  0
tmpfs    /var/run            tmpfs  defaults,size=10m,mode=0755       0  0
tmpfs    /run/ems            tmpfs  defaults,size=5m,mode=0755        0  0
tmpfs    /var/log/journal    tmpfs  defaults,size=50m,mode=0755       0  0

# EMS SSD data partition — persistent storage across reboots and OTA updates
# Device: NVMe SSD partition 1 on Advantech ECU-1170-552A
# Contains: Parquet telemetry, JSONL events, cloud buffer, RTDB snapshots, config overlay
/dev/nvme0n1p1    /data    ext4    defaults,noatime    0  2
EMS_FSTAB_ENTRIES
}
