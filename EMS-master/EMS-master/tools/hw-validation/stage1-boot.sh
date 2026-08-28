#!/usr/bin/env bash
# tools/hw-validation/stage1-boot.sh
# Stage 1: Boot Validation — ECU-1170-552A
#
# Validates all 14 EMS services start within 60 seconds on the ECU.
# Runs from a developer laptop with SSH access to the ECU.
#
# Usage:
#   bash stage1-boot.sh [ECU_IP]
#   bash stage1-boot.sh 192.168.1.100
#
# Requirements:
#   - SSH key-based auth configured for root@ECU_IP
#   - ECU booted with Yocto EMS image
#
# Exit codes:
#   0  All 14 services active
#   1  One or more services failed to become active within timeout

set -euo pipefail

ECU_IP="${1:-192.168.1.100}"
ECU_USER="${EMS_ECU_USER:-root}"
TIMEOUT=60
PASS=0
FAIL=0

# All 14 EMS runtime services (must match deploy/systemd/)
SERVICES=(
    data_manager
    ems-data-manager-python
    config_manager
    safety_manager
    comm_manager_c
    comm_manager
    logger
    control_manager
    alarm_manager
    scheduler
    diagnostics
    cloud_manager
    ota_manager
    hmi_server
)

SSH_CMD="ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes"

echo "============================================================"
echo "  EMS Stage 1: Boot Validation"
echo "  Target: ${ECU_USER}@${ECU_IP}"
echo "  Timeout: ${TIMEOUT}s"
echo "  Services: ${#SERVICES[@]}"
echo "============================================================"
echo ""

# Verify ECU is reachable before starting
if ! ping -c 1 -W 3 "${ECU_IP}" > /dev/null 2>&1; then
    echo "[ERROR] ECU not reachable at ${ECU_IP}"
    echo "        Check network connection or set ECU_IP as first argument."
    exit 1
fi

deadline=$(( $(date +%s) + TIMEOUT ))

for svc in "${SERVICES[@]}"; do
    found=false
    while true; do
        state=$(${SSH_CMD} "${ECU_USER}@${ECU_IP}" "systemctl is-active ${svc} 2>/dev/null" || echo "ssh-error")

        if [[ "${state}" == "active" ]]; then
            printf "[PASS] %-35s active\n" "${svc}"
            PASS=$(( PASS + 1 ))
            found=true
            break
        fi

        if [[ $(date +%s) -ge ${deadline} ]]; then
            printf "[FAIL] %-35s not active within %ds (state: %s)\n" \
                "${svc}" "${TIMEOUT}" "${state}"
            FAIL=$(( FAIL + 1 ))
            found=true
            break
        fi

        sleep 1
    done
done

echo ""
echo "------------------------------------------------------------"
echo "  Boot validation summary:"
echo "    PASS: ${PASS}/${#SERVICES[@]} services active"
echo "    FAIL: ${FAIL}/${#SERVICES[@]} services not active"
echo "------------------------------------------------------------"

# Check ems.target as an additional aggregate health indicator
echo ""
echo "  Checking ems.target..."
target_state=$(${SSH_CMD} "${ECU_USER}@${ECU_IP}" "systemctl is-active ems.target 2>/dev/null" || echo "ssh-error")
if [[ "${target_state}" == "active" ]]; then
    echo "[PASS] ems.target: active"
else
    echo "[FAIL] ems.target: ${target_state}"
fi

echo ""
if [[ ${FAIL} -eq 0 ]]; then
    echo "  Result: PASS — all services active"
    exit 0
else
    echo "  Result: FAIL — ${FAIL} service(s) not active"
    echo ""
    echo "  Troubleshoot with:"
    echo "    ssh ${ECU_USER}@${ECU_IP} systemctl status <service>"
    echo "    ssh ${ECU_USER}@${ECU_IP} journalctl -u <service> -n 50"
    exit 1
fi
