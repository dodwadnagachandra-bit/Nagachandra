#!/usr/bin/env bash
# gen-ca.sh — Generate a self-signed CA for development and testing.
#
# This CA is used to sign per-device certificates via gen-device-cert.sh.
# In production, the CA private key is held on the build server and NEVER
# deployed to devices.
#
# Usage:
#   ./tools/gen-ca.sh <out_dir>
#
# Arguments:
#   out_dir  Directory to write ca.key and ca.crt (will be created if absent)
#
# Outputs (in <out_dir>):
#   ca.key   — CA private key (RSA 4096-bit, chmod 600) — KEEP SECURE
#   ca.crt   — CA self-signed certificate (3650-day validity)
#
# WARNING: The CA private key must NEVER be included in a Yocto image or
#          committed to version control. Use gen-device-cert.sh to create
#          per-device certs and deploy only ca.crt + device.crt + device.key.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <out_dir>" >&2
    echo "" >&2
    echo "  out_dir  Directory to write ca.key and ca.crt" >&2
    exit 1
fi

OUT="$1"

if ! command -v openssl >/dev/null 2>&1; then
    echo "Error: openssl is not installed or not in PATH" >&2
    exit 1
fi

mkdir -p "${OUT}"

echo "Generating ReVx-EMS CA..."

openssl req \
    -x509 \
    -newkey rsa:4096 \
    -keyout "${OUT}/ca.key" \
    -out "${OUT}/ca.crt" \
    -sha256 \
    -days 3650 \
    -nodes \
    -subj "/CN=ReVx-EMS-CA/O=ReVx-Energy" \
    2>/dev/null

chmod 600 "${OUT}/ca.key"

echo ""
echo "CA generated in ${OUT}/"
echo "  ${OUT}/ca.key  — CA private key (600) — KEEP SECURE, never ship to devices"
echo "  ${OUT}/ca.crt  — CA certificate (3650-day validity)"
echo ""
echo "Next step: run './tools/gen-device-cert.sh <site_id> <serial> ${OUT}/ca.key ${OUT}/ca.crt <device_out_dir>'"
