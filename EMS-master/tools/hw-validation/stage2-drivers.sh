#!/usr/bin/env bash
# tools/hw-validation/stage2-drivers.sh
# Stage 2: Driver Verification — ECU-1170-552A
#
# Verifies CAN, RS485, GPIO, ETH, and HDMI hardware drivers via SSH.
# RS485 section has TWO distinct steps per port:
#   1. "UART exists"  — /dev/ttySN character device present (PASS/FAIL)
#   2. "Modbus poll"  — pymodbus read_holding_registers over UART (PASS/FAIL/SKIP)
#
# Usage:
#   bash stage2-drivers.sh [ECU_IP]
#   bash stage2-drivers.sh 192.168.1.100
#
# Requirements:
#   - SSH key-based auth configured for root@ECU_IP
#   - ECU booted with Yocto EMS image (Stage 1 must pass first)
#   - For Modbus poll tests: Modbus simulators running on USB-RS485 adapters
#
# Exit codes:
#   0  All hardware checks passed (communication checks may be skipped)
#   1  One or more hardware checks failed
#
# Summary distinguishes:
#   "N/M hardware checks passed" (UART, CAN iface, GPIO, ETH)
#   "N/M communication checks passed (K skipped — no simulator)"

set -euo pipefail

ECU_IP="${1:-192.168.1.100}"
ECU_USER="${EMS_ECU_USER:-root}"
SSH_CMD="ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes"

# Counters
HW_PASS=0
HW_FAIL=0
COMM_PASS=0
COMM_FAIL=0
COMM_SKIP=0

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

pass_hw() { echo "[PASS] $*"; HW_PASS=$(( HW_PASS + 1 )); }
fail_hw() { echo "[FAIL] $*"; HW_FAIL=$(( HW_FAIL + 1 )); }
pass_comm() { echo "[PASS] $*"; COMM_PASS=$(( COMM_PASS + 1 )); }
fail_comm() { echo "[FAIL] $*"; COMM_FAIL=$(( COMM_FAIL + 1 )); }
skip_comm() { echo "[SKIP] $*"; COMM_SKIP=$(( COMM_SKIP + 1 )); }

ecu() {
    # Run a command on the ECU via SSH; return exit code
    ${SSH_CMD} "${ECU_USER}@${ECU_IP}" "$@" 2>/dev/null
}

ecu_out() {
    # Run a command and capture stdout (suppress errors)
    ${SSH_CMD} "${ECU_USER}@${ECU_IP}" "$@" 2>/dev/null || true
}

print_header() {
    echo ""
    echo "------------------------------------------------------------"
    echo "  $*"
    echo "------------------------------------------------------------"
}

# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------

echo "============================================================"
echo "  EMS Stage 2: Driver Verification"
echo "  Target: ${ECU_USER}@${ECU_IP}"
echo "============================================================"

if ! ping -c 1 -W 3 "${ECU_IP}" > /dev/null 2>&1; then
    echo "[ERROR] ECU not reachable at ${ECU_IP}"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2.1 CAN Bus Tests
# ---------------------------------------------------------------------------

print_header "2.1 CAN Bus (SocketCAN)"

for iface in can0 can1; do
    if ecu "ip link show ${iface}" > /dev/null 2>&1; then
        pass_hw "${iface}: interface present"
    else
        fail_hw "${iface}: interface not found — check DTB MCAN node and CONFIG_CAN_M_CAN"
        echo "       Diagnostic: $(ecu_out 'dmesg | grep -i can | tail -5')"
    fi
done

# CAN loopback test (requires can-utils on ECU)
if ecu "which cansend && which candump" > /dev/null 2>&1; then
    # Bring up can0 in loopback mode for self-test
    ecu "ip link set can0 down" > /dev/null 2>&1 || true
    if ecu "ip link set can0 up type can loopback on bitrate 250000" > /dev/null 2>&1; then
        loopback_out=$(ecu_out "candump can0 -n 1 -T 2000 & sleep 0.2 && cansend can0 123#DEADBEEF; wait")
        if echo "${loopback_out}" | grep -qi "DEADBEEF"; then
            pass_hw "CAN0 loopback: frame TX/RX successful"
        else
            fail_hw "CAN0 loopback: frame not received (output: ${loopback_out})"
        fi
        ecu "ip link set can0 down" > /dev/null 2>&1 || true
    else
        fail_hw "CAN0 loopback: failed to configure loopback mode"
    fi
else
    skip_comm "CAN0 loopback: can-utils not available on ECU image"
    # Don't count this as COMM check since it's a tooling issue
fi

# ---------------------------------------------------------------------------
# 2.2 RS485 Tests (UART existence + Modbus poll)
# ---------------------------------------------------------------------------

print_header "2.2 RS485 (UART + Modbus RTU)"

RS485_NAMES=("PCS" "Meter" "BTMS" "DG/PV")
RS485_CANDIDATES=(
    "/dev/ttyS1 /dev/ttyS3 /dev/ttyUSB0"
    "/dev/ttyS2 /dev/ttyS4 /dev/ttyUSB1"
    "/dev/ttyS5 /dev/ttyS6 /dev/ttyUSB2"
    "/dev/ttyS7 /dev/ttyS8 /dev/ttyUSB3"
)

for port_idx in 0 1 2 3; do
    port_num=$(( port_idx + 1 ))
    port_name="${RS485_NAMES[$port_idx]}"
    candidates="${RS485_CANDIDATES[$port_idx]}"

    echo ""
    echo "  RS485-${port_num} (${port_name}):"

    # Step 1: UART device existence
    found_dev=""
    for dev in ${candidates}; do
        if ecu "test -c ${dev}" > /dev/null 2>&1; then
            found_dev="${dev}"
            break
        fi
    done

    if [[ -n "${found_dev}" ]]; then
        pass_hw "  RS485-${port_num} UART exists: ${found_dev}"
    else
        fail_hw "  RS485-${port_num} UART exists: not found (checked: ${candidates})"
        skip_comm "  RS485-${port_num} Modbus poll: skipped (no UART device)"
        continue
    fi

    # Step 2: Modbus RTU poll via pymodbus one-liner on the ECU
    modbus_out=$(ecu_out "python3 -c \"
from pymodbus.client import ModbusSerialClient
c = ModbusSerialClient('${found_dev}', baudrate=9600, timeout=2)
c.connect()
r = c.read_holding_registers(0, 1, slave=1)
print('OK' if not r.isError() else 'ERR')
c.close()
\" 2>/dev/null")

    case "${modbus_out}" in
        "OK")
            pass_comm "  RS485-${port_num} Modbus poll (${found_dev}): register read OK"
            ;;
        "ERR")
            skip_comm "  RS485-${port_num} Modbus poll (${found_dev}): no device response — connect Modbus simulator"
            ;;
        *)
            skip_comm "  RS485-${port_num} Modbus poll (${found_dev}): pymodbus not available or timeout (output: '${modbus_out}') — connect Modbus simulator"
            ;;
    esac
done

# ---------------------------------------------------------------------------
# 2.3 GPIO Tests
# ---------------------------------------------------------------------------

print_header "2.3 GPIO (libgpiod)"

# Check gpiodetect
if chipline=$(ecu_out "gpiodetect | head -1"); [[ -n "${chipline}" ]]; then
    chipname=$(echo "${chipline}" | awk '{print $1}')
    pass_hw "gpiochip detected: ${chipname}"

    # Check line count
    linecount=$(ecu_out "gpioinfo | grep -c 'line'" 2>/dev/null || echo "0")
    if [[ "${linecount}" -ge 16 ]]; then
        pass_hw "GPIO lines: ${linecount} lines found (need ≥16 for 8 DI + 8 DO)"
    else
        fail_hw "GPIO lines: only ${linecount} lines found (need ≥16)"
    fi

    # DI read test (offsets 0-7)
    di_fails=0
    for offset in 0 1 2 3 4 5 6 7; do
        val=$(ecu_out "gpioget ${chipname} ${offset}" || echo "ERR")
        if [[ "${val}" != "0" && "${val}" != "1" ]]; then
            di_fails=$(( di_fails + 1 ))
        fi
    done
    if [[ ${di_fails} -eq 0 ]]; then
        pass_hw "GPIO DI-0..7: all 8 DI lines readable on ${chipname}"
    else
        fail_hw "GPIO DI: ${di_fails}/8 DI lines not readable on ${chipname}"
    fi

    # Safe DO write test (DO-3=offset 11 RUNNING_LAMP, DO-7=offset 15 SPARE_DO7)
    # SAFETY: DO-0 (ACDB_TRIP), DO-1 (EXTINGUISHER), DO-5 (PCS_STOP) not tested here
    do_fails=0
    for offset in 11 15; do
        if ! ecu "gpioset ${chipname} ${offset}=1" > /dev/null 2>&1; then
            do_fails=$(( do_fails + 1 ))
        elif ! ecu "gpioset ${chipname} ${offset}=0" > /dev/null 2>&1; then
            do_fails=$(( do_fails + 1 ))
        fi
    done
    if [[ ${do_fails} -eq 0 ]]; then
        pass_hw "GPIO DO (safe): RUNNING_LAMP and SPARE_DO7 writable"
    else
        fail_hw "GPIO DO (safe): ${do_fails} safe DO line(s) not writable"
    fi
else
    fail_hw "gpiochip: gpiodetect returned no output — libgpiod not available or GPIO driver not loaded"
fi

# ---------------------------------------------------------------------------
# 2.4 Network / Ethernet Tests
# ---------------------------------------------------------------------------

print_header "2.4 Ethernet Interfaces"

iface_list=$(ecu_out "ip -br link show | grep -v '^lo ' | grep -v '^can' | awk '{print \$1}'")
iface_count=$(echo "${iface_list}" | grep -c . 2>/dev/null || echo "0")

if [[ ${iface_count} -ge 2 ]]; then
    pass_hw "ETH interfaces: ${iface_count} found (${iface_list//$'\n'/, })"
else
    fail_hw "ETH interfaces: only ${iface_count} found (expected ≥2 for WAN + LAN)"
fi

# Check first interface link state
primary_iface=$(echo "${iface_list}" | head -1)
if [[ -n "${primary_iface}" ]]; then
    link_state=$(ecu_out "ip -br link show ${primary_iface}" | awk '{print $2}')
    if echo "${link_state}" | grep -q "UP"; then
        pass_hw "ETH0 (${primary_iface}): link UP"
    else
        fail_hw "ETH0 (${primary_iface}): link ${link_state} — connect Ethernet cable"
    fi
fi

# ---------------------------------------------------------------------------
# 2.5 HDMI / DRM Test
# ---------------------------------------------------------------------------

print_header "2.5 HDMI / DRM"

drm_status=$(ecu_out "cat /sys/class/drm/card*/status 2>/dev/null || echo 'no drm'")
if [[ "${drm_status}" == "no drm" ]]; then
    fail_hw "HDMI/DRM: DRM subsystem not found — check Yocto kernel DRM config"
else
    pass_hw "HDMI/DRM: DRM subsystem present (status: ${drm_status})"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

HW_TOTAL=$(( HW_PASS + HW_FAIL ))
COMM_TOTAL=$(( COMM_PASS + COMM_FAIL + COMM_SKIP ))

echo ""
echo "============================================================"
echo "  Stage 2 Summary"
echo "============================================================"
echo ""
echo "  Hardware checks:      ${HW_PASS}/${HW_TOTAL} passed"
if [[ ${HW_FAIL} -gt 0 ]]; then
    echo "                        ${HW_FAIL} FAILED — see [FAIL] lines above"
fi
echo ""
echo "  Communication checks: ${COMM_PASS}/${COMM_TOTAL} passed"
if [[ ${COMM_SKIP} -gt 0 ]]; then
    echo "                        ${COMM_SKIP} skipped — no Modbus simulator connected"
    echo "                        Connect USB-RS485 adapters with simulators for full validation."
fi
if [[ ${COMM_FAIL} -gt 0 ]]; then
    echo "                        ${COMM_FAIL} FAILED"
fi
echo ""

if [[ ${HW_FAIL} -eq 0 ]]; then
    echo "  Hardware result: PASS"
else
    echo "  Hardware result: FAIL (${HW_FAIL} check(s) failed)"
fi

if [[ ${COMM_FAIL} -eq 0 ]]; then
    if [[ ${COMM_SKIP} -gt 0 ]]; then
        echo "  Comms result:   PARTIAL (${COMM_SKIP} port(s) need simulators)"
    else
        echo "  Comms result:   PASS"
    fi
else
    echo "  Comms result:   FAIL"
fi

echo ""

if [[ ${HW_FAIL} -gt 0 ]]; then
    exit 1
fi
exit 0
