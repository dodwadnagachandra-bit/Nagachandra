# EMS U-Boot A/B Boot Selection Script
#
# This script implements A/B partition selection with automatic rollback.
# It is compiled to boot.scr via:
#   mkimage -C none -A arm64 -T script -d boot.cmd boot.scr
#
# U-Boot environment variables managed by EMS OTA manager:
#   ems_active_slot          - Current boot slot: "a" or "b" (default: "a")
#   ems_boot_count           - Boot attempts for current slot (default: 0)
#
# Rollback logic: if ems_boot_count > 2, the slot is considered unhealthy
# and U-Boot reverts to the previous slot automatically.
#
# Target: TI AM654x (Advantech ECU-1170-552A)
# MMC layout:
#   mmc 0:1  - boot partition (kernel Image + DTB)
#   mmc 0:2  - rootfs slot A (/dev/mmcblk0p2)
#   mmc 0:3  - rootfs slot B (/dev/mmcblk0p3)

# ---------------------------------------------------------------------------
# Step 1: Read active slot — default to "a" if not set
# ---------------------------------------------------------------------------
if env exists ems_active_slot
then
    echo "EMS: ems_active_slot=${ems_active_slot}"
else
    setenv ems_active_slot a
    echo "EMS: ems_active_slot not set, defaulting to a"
fi

# ---------------------------------------------------------------------------
# Step 2: Map slot to root device
#   slot a -> mmcblk0p2
#   slot b -> mmcblk0p3
# ---------------------------------------------------------------------------
if test "${ems_active_slot}" = "a"
then
    setenv rootdev mmcblk0p2
    setenv fallback_slot b
    echo "EMS: Booting slot A -> /dev/${rootdev}"
else
    setenv rootdev mmcblk0p3
    setenv fallback_slot a
    echo "EMS: Booting slot B -> /dev/${rootdev}"
fi

# ---------------------------------------------------------------------------
# Step 3: Read boot count — default to 0 if not set
# ---------------------------------------------------------------------------
if env exists ems_boot_count
then
    echo "EMS: ems_boot_count=${ems_boot_count}"
else
    setenv ems_boot_count 0
fi

# ---------------------------------------------------------------------------
# Step 4: Rollback if boot count exceeds threshold (> 2 attempts)
#   Revert to fallback slot, reset boot count, and save to env storage
# ---------------------------------------------------------------------------
if test ${ems_boot_count} -gt 2
then
    echo "EMS: Boot count ${ems_boot_count} > 2 — slot ${ems_active_slot} unhealthy"
    echo "EMS: Reverting to fallback slot ${fallback_slot}"
    setenv ems_active_slot ${fallback_slot}
    setenv ems_boot_count 0
    saveenv
    # Remap root device after slot reversion
    if test "${ems_active_slot}" = "a"
    then
        setenv rootdev mmcblk0p2
    else
        setenv rootdev mmcblk0p3
    fi
    echo "EMS: Reverted to slot ${ems_active_slot} -> /dev/${rootdev}"
fi

# ---------------------------------------------------------------------------
# Step 5: Increment boot count and persist
#   The EMS OTA health checker will reset this to 0 on successful boot
# ---------------------------------------------------------------------------
setexpr ems_boot_count ${ems_boot_count} + 1
echo "EMS: Incrementing ems_boot_count to ${ems_boot_count}"
saveenv

# ---------------------------------------------------------------------------
# Step 6: Set kernel boot arguments
#   console=ttyS2,115200n8  - AM654x UART2 (ECU-1170 debug UART)
#   root=/dev/${rootdev}    - Selected A/B root partition
#   ro                      - Mount root read-only (overlayfs handles writes)
#   rootfstype=ext4         - Root filesystem type
#   rootwait                - Wait for MMC device to become available
#   systemd.unified_cgroup_hierarchy=1  - cgroup v2 for systemd 250+
# ---------------------------------------------------------------------------
setenv bootargs "console=ttyS2,115200n8 root=/dev/${rootdev} ro rootfstype=ext4 rootwait systemd.unified_cgroup_hierarchy=1"
echo "EMS: bootargs=${bootargs}"

# ---------------------------------------------------------------------------
# Step 7: Load kernel Image from boot partition (mmc 0:1)
#   Load address 0x80080000 (standard ARM64 kernel load address for AM65x)
# ---------------------------------------------------------------------------
setenv loadaddr 0x80080000
echo "EMS: Loading kernel Image from mmc 0:1..."
load mmc 0:1 ${loadaddr} Image

# ---------------------------------------------------------------------------
# Step 8: Load device tree blob from boot partition (mmc 0:1)
#   k3-am654-base-board.dtb — TI AM654x base board device tree
# ---------------------------------------------------------------------------
setenv fdtaddr 0x83000000
echo "EMS: Loading DTB k3-am654-base-board.dtb from mmc 0:1..."
load mmc 0:1 ${fdtaddr} k3-am654-base-board.dtb

# ---------------------------------------------------------------------------
# Step 9: Boot the kernel
#   booti <kernel_addr> - <fdt_addr>
#   "-" means no initrd/ramdisk is used
# ---------------------------------------------------------------------------
echo "EMS: Booting Linux on slot ${ems_active_slot}..."
booti ${loadaddr} - ${fdtaddr}
