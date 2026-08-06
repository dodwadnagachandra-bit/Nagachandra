#!/usr/bin/env bash
# gen-device-cert.sh — Generate per-device mTLS certificates for EMS.
#
# Each EMS device requires a unique certificate signed by the project CA for
# MQTT/TLS cloud authentication. This script is run at build time (before
# bitbake) for each device serial number, producing the three files that the
# ems-certs Yocto recipe bakes into the image.
#
# Usage:
#   ./tools/gen-device-cert.sh <site_id> <serial_number> <ca_key> <ca_cert> <out_dir>
#
# Arguments:
#   site_id        Site identifier (e.g. "site001")
#   serial_number  Device serial number (e.g. "ECU1170-0042")
#   ca_key         Path to CA private key (.pem / .key) — NEVER shipped to devices
#   ca_cert        Path to CA certificate (.crt / .pem)
#   out_dir        Output directory — will be created if it does not exist
#
# Outputs (in <out_dir>):
#   ca.crt      — CA certificate for broker trust verification
#   device.crt  — Device certificate signed by the CA
#   device.key  — Device private key (chmod 600)
#
# Security notes:
#   - The CA private key is NEVER written to out_dir.
#   - device.key is set to 600 immediately after creation.
#   - The intermediate CSR is deleted before the script exits.

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

if [ "$#" -ne 5 ]; then
    echo "Usage: $0 <site_id> <serial_number> <ca_key> <ca_cert> <out_dir>" >&2
    echo "" >&2
    echo "  site_id        Site identifier (e.g. site001)" >&2
    echo "  serial_number  Device serial number (e.g. ECU1170-0042)" >&2
    echo "  ca_key         Path to CA private key (never shipped to devices)" >&2
    echo "  ca_cert        Path to CA certificate" >&2
    echo "  out_dir        Output directory for generated certificates" >&2
    exit 1
fi

SITE_ID="$1"
SERIAL="$2"
CA_KEY="$3"
CA_CERT="$4"
OUT="$5"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

if [ ! -f "${CA_KEY}" ]; then
    echo "Error: CA key not found: ${CA_KEY}" >&2
    exit 1
fi

if [ ! -f "${CA_CERT}" ]; then
    echo "Error: CA cert not found: ${CA_CERT}" >&2
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "Error: openssl is not installed or not in PATH" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

CN="${SITE_ID}-${SERIAL}"

mkdir -p "${OUT}"

echo "Generating mTLS certificates for device: ${CN}"
echo "  CA cert:  ${CA_CERT}"
echo "  Output:   ${OUT}/"

# ---------------------------------------------------------------------------
# Step 1: Generate device private key (RSA 4096-bit)
# ---------------------------------------------------------------------------

openssl genrsa -out "${OUT}/device.key" 4096 2>/dev/null

# Set permissions immediately — before any data is written to device.crt
chmod 600 "${OUT}/device.key"

echo "  [1/4] Device private key generated (RSA 4096)"

# ---------------------------------------------------------------------------
# Step 2: Generate Certificate Signing Request (CSR)
# ---------------------------------------------------------------------------

openssl req \
    -new \
    -key "${OUT}/device.key" \
    -subj "/CN=${CN}/O=ReVx-Energy/OU=EMS" \
    -out "${OUT}/device.csr" \
    2>/dev/null

echo "  [2/4] CSR created (CN=${CN}, O=ReVx-Energy, OU=EMS)"

# ---------------------------------------------------------------------------
# Step 3: CA signs the CSR to produce device certificate
# ---------------------------------------------------------------------------

openssl x509 \
    -req \
    -in "${OUT}/device.csr" \
    -CA "${CA_CERT}" \
    -CAkey "${CA_KEY}" \
    -CAcreateserial \
    -days 365 \
    -sha256 \
    -out "${OUT}/device.crt" \
    2>/dev/null

echo "  [3/4] Device certificate signed by CA (365-day validity, SHA-256)"

# ---------------------------------------------------------------------------
# Step 4: Copy CA cert to output, clean up CSR
# ---------------------------------------------------------------------------

cp "${CA_CERT}" "${OUT}/ca.crt"

# Remove the intermediate CSR — not needed in the image
rm "${OUT}/device.csr"

echo "  [4/4] CA cert copied; CSR removed"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Certificates generated for ${CN} in ${OUT}/"
echo ""
echo "  ${OUT}/ca.crt     — CA certificate (broker trust)"
echo "  ${OUT}/device.crt — Device certificate"
echo "  ${OUT}/device.key — Device private key (600)"
echo ""
echo "Next step: run 'bitbake ems-certs' with FILESEXTRAPATHS pointing to ${OUT}/"
