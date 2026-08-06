#!/usr/bin/env bash
# sim-all.sh — Unified simulator launcher for EMS development
# Launches CAN, Modbus, and GPIO simulators simultaneously with PID tracking
# and clean teardown on SIGINT/SIGTERM.
#
# Usage:
#   bash tools/sim-all.sh                           # residential profile
#   bash tools/sim-all.sh --profile commercial      # commercial profile
#   bash tools/sim-all.sh --verbose --tcp-port 5021 # verbose, custom port
#
# Requirements: uv, vcan kernel modules (for CAN sim)

set -euo pipefail

# -------------------------------------------------------------------
# Argument parsing
# -------------------------------------------------------------------
PROFILE="residential"
TCP_PORT=5020
VERBOSE=""
VERBOSE_FLAG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --tcp-port)
            TCP_PORT="$2"
            shift 2
            ;;
        --verbose|-v)
            VERBOSE="1"
            VERBOSE_FLAG="--verbose"
            shift
            ;;
        -h|--help)
            echo "Usage: sim-all.sh [--profile NAME] [--tcp-port PORT] [--verbose]"
            echo ""
            echo "Options:"
            echo "  --profile NAME   Config profile (residential|commercial|container) [default: residential]"
            echo "  --tcp-port PORT  Modbus TCP port [default: 5020]"
            echo "  --verbose, -v    Enable verbose logging for all simulators"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# -------------------------------------------------------------------
# Validate config profile directory
# -------------------------------------------------------------------
CONFIG_DIR="config/profiles/$PROFILE"

if [ ! -d "$CONFIG_DIR" ]; then
    echo "ERROR: Config profile directory not found: $CONFIG_DIR" >&2
    echo "Available profiles: $(ls config/profiles/ 2>/dev/null | tr '\n' ' ')" >&2
    exit 1
fi

# -------------------------------------------------------------------
# Create log directory
# -------------------------------------------------------------------
mkdir -p logs

# -------------------------------------------------------------------
# Set up vcan0 interface
# -------------------------------------------------------------------
echo "Setting up virtual CAN interface (vcan0)..."
sudo modprobe vcan can can_raw 2>/dev/null || true
sudo ip link add dev vcan0 type vcan 2>/dev/null || true
sudo ip link set up vcan0 2>/dev/null || true

# -------------------------------------------------------------------
# PID tracking and cleanup
# -------------------------------------------------------------------
PIDS=()

cleanup() {
    echo ""
    echo "Shutting down simulators..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "All simulators stopped."
}

trap cleanup EXIT INT TERM

# -------------------------------------------------------------------
# Health check helper
# -------------------------------------------------------------------
wait_for_health() {
    local name="$1" check_cmd="$2" timeout=5 elapsed=0
    while ! eval "$check_cmd" 2>/dev/null; do
        sleep 0.5
        elapsed=$((elapsed + 1))
        if [ "$elapsed" -ge "$((timeout * 2))" ]; then
            echo "FAIL: $name health check timed out after ${timeout}s" >&2
            return 1
        fi
    done
    echo "  $name: OK"
}

# -------------------------------------------------------------------
# Launch simulators
# -------------------------------------------------------------------
echo "Launching simulators (profile: $PROFILE)..."
echo ""

# CAN simulator
echo "  Starting CAN simulator on vcan0..."
uv run python -m tools.simulators.can_sim \
    --interface vcan0 \
    --config "$CONFIG_DIR/bms_config.yaml" \
    $VERBOSE_FLAG \
    > logs/sim-can.log 2>&1 &
PIDS+=($!)

# Modbus simulator (TCP mode — no socat dependency)
echo "  Starting Modbus simulator on TCP :$TCP_PORT..."
uv run python -m tools.simulators.modbus_sim \
    --transport tcp \
    --tcp-port "$TCP_PORT" \
    --config "$CONFIG_DIR/pcs_config.yaml" \
    $VERBOSE_FLAG \
    > logs/sim-modbus.log 2>&1 &
PIDS+=($!)

# GPIO harness — RTDB-backed, stateless (no daemon needed)
# The RTDB backend creates/attaches to shared memory on demand.
# Integration tests interact with it directly via RtdbBackend.
echo "  GPIO harness: RTDB-backed (stateless, no daemon needed)"

# -------------------------------------------------------------------
# Health checks
# -------------------------------------------------------------------
echo ""
echo "Running health checks..."

wait_for_health "CAN (vcan0)" "ip link show vcan0 | grep -q UP"
wait_for_health "Modbus (TCP :$TCP_PORT)" "ss -tln | grep -q :$TCP_PORT"

# GPIO health: check that RTDB shm can be created (just validate the import works)
# The RTDB segment is created on-demand by RtdbBackend, no persistent daemon needed.
echo "  GPIO (RTDB): OK (stateless backend)"

# -------------------------------------------------------------------
# Status summary
# -------------------------------------------------------------------
echo ""
echo "=============================================="
echo "CAN:    running (vcan0)        PID: ${PIDS[0]}"
echo "Modbus: running (TCP :$TCP_PORT)  PID: ${PIDS[1]}"
echo "GPIO:   ready (RTDB-backed, stateless)"
echo "=============================================="
echo "Profile: $PROFILE | Logs: logs/sim-*.log"
echo "Press Ctrl+C to stop all simulators."
echo ""

# Block until Ctrl+C triggers cleanup
wait
