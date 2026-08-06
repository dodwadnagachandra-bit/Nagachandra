#!/usr/bin/env bash
# Stage 5: 24-Hour Soak Test Launcher
#
# Verifies CAN bus traffic and Modbus responses are live before committing
# to a 24-hour soak run. Copies soak_monitor.py to the ECU and launches it
# via nohup so it survives SSH disconnect.
#
# Usage:
#   ./stage5-soak.sh <ECU_IP>
#
# Example:
#   ./stage5-soak.sh 192.168.1.100

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ECU_IP="${1:-}"
ECU_USER="${EMS_ECU_USER:-root}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOAK_MONITOR="${SCRIPT_DIR}/soak_monitor.py"
ECU_MONITOR_PATH="/opt/ems/soak_monitor.py"
SOAK_OUTPUT="/data/soak_monitor.jsonl"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

if [[ -z "${ECU_IP}" ]]; then
    echo "Usage: $0 <ECU_IP>"
    echo "  Example: $0 192.168.1.100"
    echo ""
    echo "  Override ECU user with: EMS_ECU_USER=user $0 <ECU_IP>"
    exit 1
fi

# ---------------------------------------------------------------------------
# Banner and pre-condition documentation
# ---------------------------------------------------------------------------

echo ""
echo "================================================================"
echo "  Stage 5: 24-Hour Soak Test"
echo "================================================================"
echo ""
echo "PRE-CONDITIONS:"
echo "  - CAN simulator running on laptop, connected to ECU CAN0"
echo "    via USB-CAN adapter (e.g., Kvaser Leaf or PEAK PCAN-USB)"
echo "  - Modbus simulators running on laptop, connected to ECU RS485"
echo "    ports via USB-RS485 adapters"
echo "  - All 14 EMS services active (run stage1-boot.sh first)"
echo "  - ECU reachable at: ${ECU_USER}@${ECU_IP}"
echo ""
echo "Checking pre-conditions..."
echo ""

PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Helper: pass/fail reporting
# ---------------------------------------------------------------------------

pass() {
    echo -e "  ${GREEN}[PASS]${NC} $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
    echo -e "  ${YELLOW}[WARN]${NC} $1"
}

# ---------------------------------------------------------------------------
# Pre-check 1: ECU reachability
# ---------------------------------------------------------------------------

echo "--- ECU Connectivity ---"
if ssh -o StrictHostKeyChecking=no \
       -o ConnectTimeout=5 \
       -o BatchMode=yes \
       "${ECU_USER}@${ECU_IP}" "echo ok" > /dev/null 2>&1; then
    pass "ECU reachable at ${ECU_USER}@${ECU_IP}"
else
    fail "ECU not reachable at ${ECU_USER}@${ECU_IP}"
    echo ""
    echo "Fix: Ensure ECU is powered on and connected via Ethernet."
    echo "     Verify IP: ping ${ECU_IP}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Pre-check 2: EMS services active
# ---------------------------------------------------------------------------

echo ""
echo "--- EMS Services ---"
SERVICES_STATUS=$(ssh -o StrictHostKeyChecking=no \
                      -o ConnectTimeout=5 \
                      -o BatchMode=yes \
                      "${ECU_USER}@${ECU_IP}" \
                      "systemctl is-active ems.target 2>/dev/null || echo inactive")

if [[ "${SERVICES_STATUS}" == "active" ]]; then
    pass "ems.target is active (all 14 services running)"
else
    fail "ems.target is not active (status: ${SERVICES_STATUS})"
    echo ""
    echo "Fix: Run stage1-boot.sh first to start all EMS services:"
    echo "     ./stage1-boot.sh ${ECU_IP}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ---------------------------------------------------------------------------
# Pre-check 3: CAN bus traffic
# ---------------------------------------------------------------------------

echo ""
echo "--- CAN Bus Traffic (candump can0 -n 1 -T 5000) ---"
CAN_OUTPUT=$(ssh -o StrictHostKeyChecking=no \
                 -o ConnectTimeout=10 \
                 -o BatchMode=yes \
                 "${ECU_USER}@${ECU_IP}" \
                 "candump can0 -n 1 -T 5000 2>&1" || true)

if [[ -n "${CAN_OUTPUT}" ]] && ! echo "${CAN_OUTPUT}" | grep -qi "error\|timeout\|No such\|Cannot"; then
    pass "CAN traffic detected on can0"
    echo "    Last frame: ${CAN_OUTPUT}"
else
    fail "No CAN traffic on can0 — ensure CAN simulator is running and connected via USB-CAN adapter"
    echo ""
    echo "  Expected: At least 1 CAN frame received within 5 seconds"
    echo "  Received: ${CAN_OUTPUT:-<no output>}"
    echo ""
    echo "  Fix:"
    echo "    1. Start the CAN simulator on your laptop:"
    echo "       cd simulators/can && uv run python can_simulator.py"
    echo "    2. Connect USB-CAN adapter from laptop to ECU CAN0 bus"
    echo "    3. Verify bus termination resistors (120 Ω at each end)"
fi

# ---------------------------------------------------------------------------
# Pre-check 4: Modbus response on RS485-1 (PCS)
# ---------------------------------------------------------------------------

echo ""
echo "--- Modbus Response (RS485-1 / /dev/ttyS1 → PCS simulator) ---"
MODBUS_OUTPUT=$(ssh -o StrictHostKeyChecking=no \
                    -o ConnectTimeout=15 \
                    -o BatchMode=yes \
                    "${ECU_USER}@${ECU_IP}" \
                    "python3 -c \"
from pymodbus.client import ModbusSerialClient
c = ModbusSerialClient('/dev/ttyS1', baudrate=9600, timeout=3)
c.connect()
r = c.read_holding_registers(0, 1, slave=1)
print('OK' if not r.isError() else 'ERR')
c.close()
\"" 2>&1 || true)

if echo "${MODBUS_OUTPUT}" | grep -q "^OK$"; then
    pass "Modbus response received on RS485-1 (/dev/ttyS1)"
else
    fail "No Modbus response on RS485-1 — ensure Modbus simulator is running and connected via USB-RS485 adapter"
    echo ""
    echo "  Expected: OK"
    echo "  Received: ${MODBUS_OUTPUT:-<no output>}"
    echo ""
    echo "  Fix:"
    echo "    1. Start the Modbus PCS simulator on your laptop:"
    echo "       cd simulators/modbus && uv run python pcs_simulator.py"
    echo "    2. Connect USB-RS485 adapter from laptop to ECU RS485-1 port"
    echo "    3. Verify baud rate: 9600 baud, 8N1"
fi

# ---------------------------------------------------------------------------
# Pre-condition summary
# ---------------------------------------------------------------------------

echo ""
echo "================================================================"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    echo -e "  ${RED}PRE-CONDITIONS FAILED${NC}: ${FAIL_COUNT} check(s) failed"
    echo ""
    echo "  Fix the issues above before running the soak test."
    echo "  A 24-hour soak with failed pre-conditions produces invalid results."
    echo ""
    exit 1
else
    echo -e "  ${GREEN}PRE-CONDITIONS PASSED${NC}: ${PASS_COUNT}/${PASS_COUNT} checks passed"
fi
echo "================================================================"
echo ""

# ---------------------------------------------------------------------------
# Launch soak monitor
# ---------------------------------------------------------------------------

echo "Launching soak monitor on ${ECU_USER}@${ECU_IP}..."
echo ""

# Copy soak_monitor.py to ECU if not present or if local version is newer
if [[ ! -f "${SOAK_MONITOR}" ]]; then
    echo "ERROR: soak_monitor.py not found at ${SOAK_MONITOR}"
    echo "       Run this script from the tools/hw-validation/ directory."
    exit 1
fi

echo "Copying soak_monitor.py to ECU..."
scp -o StrictHostKeyChecking=no \
    -o ConnectTimeout=10 \
    "${SOAK_MONITOR}" \
    "${ECU_USER}@${ECU_IP}:${ECU_MONITOR_PATH}"

echo "Making soak_monitor.py executable..."
ssh -o StrictHostKeyChecking=no \
    -o ConnectTimeout=5 \
    -o BatchMode=yes \
    "${ECU_USER}@${ECU_IP}" \
    "chmod +x ${ECU_MONITOR_PATH}"

# Remove stale output from a previous run
echo "Clearing previous soak output (if any)..."
ssh -o StrictHostKeyChecking=no \
    -o ConnectTimeout=5 \
    -o BatchMode=yes \
    "${ECU_USER}@${ECU_IP}" \
    "rm -f ${SOAK_OUTPUT}"

# Launch with nohup so it survives SSH disconnect
echo "Starting soak monitor (nohup)..."
ssh -o StrictHostKeyChecking=no \
    -o ConnectTimeout=10 \
    -o BatchMode=yes \
    "${ECU_USER}@${ECU_IP}" \
    "nohup /opt/ems/python/.venv/bin/python3 ${ECU_MONITOR_PATH} \
         --duration 86400 \
         --output ${SOAK_OUTPUT} \
         > /data/soak_monitor.log 2>&1 &
     echo \$! > /data/soak_monitor.pid
     sleep 1
     cat /data/soak_monitor.pid"

echo ""
echo "================================================================"
echo -e "  ${GREEN}SOAK TEST STARTED${NC}"
echo "================================================================"
echo ""
echo "  The soak monitor is running on the ECU in the background."
echo "  It will run for 24 hours and write results to:"
echo "    ${SOAK_OUTPUT}"
echo ""
echo "  Monitor live output:"
echo "    ssh ${ECU_USER}@${ECU_IP} tail -f /data/soak_monitor.jsonl"
echo ""
echo "  Check current status (latest heartbeat):"
echo "    ssh ${ECU_USER}@${ECU_IP} 'tail -1 /data/soak_monitor.jsonl | python3 -m json.tool'"
echo ""
echo "  Check process is still running:"
echo "    ssh ${ECU_USER}@${ECU_IP} 'ps aux | grep soak_monitor'"
echo ""
echo "  View full log:"
echo "    ssh ${ECU_USER}@${ECU_IP} tail -50 /data/soak_monitor.log"
echo ""
echo "  The test will complete automatically in 24 hours."
echo "  When complete, run the report generator:"
echo "    python3 tools/hw-validation/hw_validation_report.py --ecu-ip ${ECU_IP}"
echo ""
