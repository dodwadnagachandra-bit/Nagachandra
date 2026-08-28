---
phase: 27-yocto-migration
plan: "04"
subsystem: cert-provisioning
tags: [mtls, certs, openssl, yocto, security]
dependency_graph:
  requires: []
  provides: [mTLS cert provisioning workflow, ems-certs Yocto recipe]
  affects: [cloud_manager, ota_manager]
tech_stack:
  added: []
  patterns: [build-time cert provisioning, RSA-4096 device certs, Yocto FILES recipe]
key_files:
  created:
    - tools/gen-device-cert.sh
    - tools/gen-ca.sh
    - yocto/meta-ems/recipes-ems/ems-certs/ems-certs_0.1.0.bb
    - tests/test_cert_provisioning.py
  modified: []
decisions:
  - "openssl >= 3.x formats subject as 'CN = value' (with spaces); tests adjusted to handle both formats"
  - "gen-ca.sh added as optional dev helper alongside gen-device-cert.sh"
metrics:
  duration: "3m8s"
  completed: "2026-03-16"
  tasks_completed: 1
  tasks_total: 1
  files_created: 4
  files_modified: 0
---

# Phase 27 Plan 04: mTLS Certificate Provisioning Summary

**One-liner:** Build-time per-device RSA-4096 cert generation via openssl (gen-device-cert.sh) baked into Yocto image by ems-certs recipe installing to /etc/ems/certs/.

## What Was Built

### tools/gen-device-cert.sh

Per-device mTLS certificate generator. Takes 5 arguments: `site_id`, `serial_number`, `ca_key`, `ca_cert`, `out_dir`. Produces three files in the output directory:

- `ca.crt` — CA certificate copied for broker trust verification
- `device.crt` — Device certificate signed by CA (365-day, SHA-256, RSA-4096)
- `device.key` — Device private key (chmod 600 immediately after creation)

Certificate subject: `CN={site_id}-{serial_number}/O=ReVx-Energy/OU=EMS`

Security enforcement: CA private key never written to output dir; CSR deleted after signing; `set -euo pipefail` throughout.

### tools/gen-ca.sh

Helper to generate a self-signed CA for development and testing. Produces `ca.key` (RSA-4096, chmod 600) and `ca.crt` (3650-day validity). CA key is intended to stay on the build server — never shipped to devices.

### yocto/meta-ems/recipes-ems/ems-certs/ems-certs_0.1.0.bb

Yocto FILES recipe. Sources `ca.crt`, `device.crt`, `device.key` from the build pipeline (set via `FILESEXTRAPATHS:prepend:pn-ems-certs`). Installs to `/etc/ems/certs/` with correct permissions:

- `ca.crt` — 0644
- `device.crt` — 0644
- `device.key` — 0600

Explicit comment in recipe that CA private key must never be included.

### tests/test_cert_provisioning.py

10 automated tests covering all cert provisioning behaviors. Uses `ca_dir` and `cert_output_dir` tmp_path fixtures. Tests are conditionally skipped if openssl CLI unavailable. All tests pass in 9s on the dev machine.

## Task Summary

| Task | Name | Commit | Files |
|------|------|--------|-------|
| RED | Add failing tests | 7a66c6f | tests/test_cert_provisioning.py |
| GREEN | Implement cert provisioning | f31e43f | tools/gen-device-cert.sh, tools/gen-ca.sh, yocto/meta-ems/recipes-ems/ems-certs/ems-certs_0.1.0.bb |

## Verification

```
uv run pytest tests/test_cert_provisioning.py -x -q
10 passed in 9.10s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed openssl >= 3.x subject output format in test assertions**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** `openssl x509 -subject` in openssl >= 3.x outputs `CN = value` (with spaces around `=`), not `CN=value`. Test assertions checking for `"CN=site001-ECU1170-0042"` failed.
- **Fix:** Updated assertions to check for the value string and attribute name separately (e.g., `"site001-ECU1170-0042" in subject and "CN" in subject`).
- **Files modified:** tests/test_cert_provisioning.py
- **Commit:** f31e43f (included in GREEN commit)

## Self-Check: PASSED

All files confirmed present. All commits confirmed in git log.
