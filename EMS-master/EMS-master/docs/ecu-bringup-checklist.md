# ECU-1170-552A Hardware Bring-Up Checklist

> **When to use:** When the Advantech ECU-1170-552A hardware unit arrives.
> All steps below were deferred from PLAT-01 because the hardware is not yet available.
> Simulator development proceeds independently on the dev workstation.

---

## Prerequisites

Gather these before starting:

- [ ] Advantech ECU-1170-552A unit (powered off, anti-static bag)
- [ ] Ubuntu 22.04 LTS Advantech BSP ISO image (from Advantech support portal)
- [ ] Ethernet cable (for SSH access after flash)
- [ ] USB-to-serial console cable (3.3V UART, typically Prolific or FTDI)
- [ ] microSD card (>= 16 GB, class 10) or USB flash drive for BSP installer
- [ ] Network switch or direct connection to dev workstation
- [ ] 24 V DC power supply or rack power feed (check ECU power spec)

---

## Step 1: Flash BSP

Flash the Advantech Ubuntu 22.04 LTS BSP to the ECU-1170 eMMC.

- [ ] Download the latest BSP from the Advantech support portal for ECU-1170-552A
- [ ] Write the BSP installer to microSD: `sudo dd if=ECU1170_Ubuntu22.04_BSP.img of=/dev/sdX bs=4M status=progress`
- [ ] Insert microSD into ECU-1170 and power on
- [ ] Connect USB serial console at 115200 baud: `screen /dev/ttyUSB0 115200`
- [ ] Follow on-screen BSP installer prompts to flash to eMMC
- [ ] Remove microSD and reboot from eMMC
- [ ] Confirm login prompt appears on serial console

Expected output: Ubuntu 22.04 LTS login prompt on serial console

---

## Step 2: Network Setup

Configure static IP and hostname for reliable SSH access.

- [ ] Log in as `root` or the default Advantech BSP user via serial console
- [ ] Set hostname: `hostnamectl set-hostname ems-ecu`
- [ ] Configure static IP on eth0 (edit `/etc/netplan/01-netcfg.yaml`):
  ```yaml
  network:
    version: 2
    ethernets:
      eth0:
        dhcp4: false
        addresses: [192.168.1.100/24]
        gateway4: 192.168.1.1
        nameservers:
          addresses: [8.8.8.8]
  ```
- [ ] Apply: `netplan apply`
- [ ] Verify SSH is reachable from dev workstation: `ssh root@192.168.1.100`

Expected output: `ems-ecu:~#` prompt over SSH

---

## Step 3: Create EMS User

Create the `ems` service account with the correct group memberships.

- [ ] Create user: `adduser --system --group --no-create-home ems`
- [ ] Or if interactive user preferred: `adduser ems`
- [ ] Add to required groups:
  ```bash
  usermod -aG dialout ems    # RS485/Modbus serial ports
  usermod -aG can ems        # SocketCAN access
  usermod -aG gpio ems       # libgpiod GPIO access
  ```
- [ ] Create EMS install directory: `mkdir -p /opt/ems/{bin,config,data,logs,python}`
- [ ] Set ownership: `chown -R ems:ems /opt/ems`

Expected output: `id ems` shows dialout, can, gpio groups

---

## Step 4: Install EMS Dependencies

Install all system packages required by EMS on the ECU.

- [ ] Update package list: `apt-get update`
- [ ] Install C build tools (if not in BSP): `apt-get install -y gcc make cmake`
- [ ] Install CAN tools and libraries: `apt-get install -y can-utils libsocketcan-dev`
- [ ] Install GPIO library: `apt-get install -y libgpiod-dev`
- [ ] Install serial utilities: `apt-get install -y socat minicom`
- [ ] Install Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [ ] Install Python 3.12: `uv python install 3.12`
- [ ] Verify: `uv run python --version` should report 3.12.x

Expected output: All apt packages install cleanly, `uv run python --version` returns `Python 3.12.x`

---

## Step 5: Verify SocketCAN

Confirm CAN hardware (AM6548 DCAN controller) is accessible and functional.

- [ ] Check available CAN interfaces: `ip link show type can`
- [ ] Expected interfaces: `can0`, `can1` (ECU-1170-552A has 2x CAN)
- [ ] Configure bitrate (BMS uses 250 kbps): `ip link set can0 type can bitrate 250000`
- [ ] Bring up interface: `ip link set up can0`
- [ ] Start candump to verify no errors: `candump can0 &`
- [ ] Send a test frame from can1: `cansend can1 001#DEADBEEF`
- [ ] Confirm candump on can0 receives the frame
- [ ] Stop candump: `kill %1`
- [ ] Load SocketCAN kernel modules if not auto-loaded:
  ```bash
  modprobe can
  modprobe can_raw
  modprobe can_dev
  ```

Expected output: `candump can0` shows test frame `001#DEADBEEF` received without errors

---

## Step 6: Verify RS485 / Modbus Serial Ports

Confirm RS485 serial ports are accessible for Modbus RTU communication.

- [ ] List serial devices: `ls /dev/ttyS* /dev/ttyUSB*`
- [ ] Expected: `ttyS0` (console), `ttyS1–ttyS3` (RS485-1 through RS485-4) or via USB adapter
- [ ] Set baud rate on RS485-1 (PCS port, 9600 baud): `stty -F /dev/ttyS1 9600 cs8 -cstopb -parenb`
- [ ] Test loopback: connect RS485+ to RS485+ on a loopback adapter
  ```bash
  cat /dev/ttyS1 &
  echo "test" > /dev/ttyS1
  ```
- [ ] Confirm echo received (loopback test)
- [ ] Document actual device paths in `/opt/ems/config/comms.yaml`

Expected output: Loopback echo received on at least one RS485 port; actual `/dev/ttyS*` paths confirmed

---

## Step 7: Verify GPIO (libgpiod)

Confirm AM6548 GPIO bank is accessible via libgpiod for safety I/O.

- [ ] Install libgpiod utilities if not present: `apt-get install -y gpiod`
- [ ] List GPIO chips: `gpiodetect`
- [ ] Expected: at least one chip entry (e.g., `gpiochip0` with 128+ lines)
- [ ] List GPIO lines on chip 0: `gpioinfo gpiochip0`
- [ ] Read a digital input (DI-1, verify line number from ECU-1170 schematic):
  ```bash
  gpioget gpiochip0 <DI_1_LINE>
  ```
- [ ] Set a digital output (DO-1, verify it is safe to toggle before testing):
  ```bash
  gpioset gpiochip0 <DO_1_LINE>=1
  gpioset gpiochip0 <DO_1_LINE>=0
  ```
- [ ] Document actual gpiochip number and line offsets in `/opt/ems/config/gpio.yaml`

Expected output: `gpiodetect` lists at least one chip; DI reads 0 or 1; DO toggles without error

---

## Step 8: Deploy EMS Binaries

Deploy the built EMS software from dev workstation to ECU.

- [ ] On dev workstation: build ARM64 binaries: `make build-arm`
- [ ] Deploy to ECU: `make flash ECU_HOST=ems@192.168.1.100`
  - This runs: `rsync -avz build-arm/ ems@192.168.1.100:/opt/ems/bin/`
  - And:        `rsync -avz config/ ems@192.168.1.100:/opt/ems/config/`
- [ ] SSH into ECU and verify binaries: `ls -la /opt/ems/bin/`
- [ ] Confirm safety_manager binary is present: `test -f /opt/ems/bin/safety_manager && echo FOUND`

Expected output: All binaries present in `/opt/ems/bin/`, configs in `/opt/ems/config/`

---

## Step 9: Verify Binary Execution on ARM64

Confirm the cross-compiled ARM64 binaries execute correctly on the ECU.

- [ ] SSH to ECU as ems user: `ssh ems@192.168.1.100`
- [ ] Test safety_manager binary: `/opt/ems/bin/safety_manager --version`
  - Should print version string without `Exec format error`
- [ ] Check binary architecture: `file /opt/ems/bin/safety_manager`
  - Should show: `ELF 64-bit LSB executable, ARM aarch64`
- [ ] Run a brief sanity check (will exit quickly without real hardware):
  ```bash
  timeout 2 /opt/ems/bin/safety_manager || true
  ```
- [ ] Confirm no segfaults or `Illegal instruction` errors in output

Expected output: Binary runs (even briefly), no architecture mismatch errors

---

## Step 10: Enable systemd Services

Install and enable the EMS systemd units for automatic startup.

- [ ] Copy systemd units from deployment package:
  ```bash
  cp /opt/ems/deploy/systemd/*.service /etc/systemd/system/
  cp /opt/ems/deploy/systemd/ems.target /etc/systemd/system/
  ```
- [ ] Reload systemd daemon: `systemctl daemon-reload`
- [ ] Enable the EMS target: `systemctl enable ems.target`
- [ ] Enable individual services: `systemctl enable safety_manager.service`
- [ ] Start safety_manager for initial test: `systemctl start safety_manager.service`
- [ ] Check status: `systemctl status safety_manager.service`
- [ ] Check journal logs: `journalctl -u safety_manager.service -f`
- [ ] Reboot and verify auto-start: `systemctl reboot` then SSH back and check `systemctl status ems.target`

Expected output: `systemctl status ems.target` shows `active`, all configured services appear as `active` or `activating`

---

## Notes

- **ECU power:** ECU-1170-552A requires 24 V DC ± 20%. Verify supply before powering on.
- **Real-time kernel:** For PREEMPT_RT support (safety_manager), check if BSP ships a `linux-image-rt-*` kernel. If not, PREEMPT_RT patch set must be applied to the kernel source from Advantech.
- **CAN termination:** CAN bus requires 120 Ω termination resistors at each end. ECU-1170 has onboard jumpers — consult ECU-1170-552A hardware manual.
- **GPIO line numbers:** Actual GPIO line offsets depend on ECU-1170-552A device tree. Consult the Advantech ECU-1170-552A hardware datasheet or device tree source for correct mapping.
- **Contact:** For BSP issues, contact Advantech technical support with the ECU part number and Ubuntu BSP version.
